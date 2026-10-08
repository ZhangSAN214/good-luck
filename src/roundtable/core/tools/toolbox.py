"""一张桌子上的工具环境：可用工具、各成员的工作目录、额度计数与执行。

可用工具 = 配置中该步骤的工具 ∩ 当前可用（python 需要沙箱后端，generate_image 需要带 image_gen
标签的可用模型）。同一步骤所有成员可用的工具、限额和说明完全相同。
"""

from __future__ import annotations

import random
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from roundtable.core.allocation import IdentityScrubber, cheapest
from roundtable.core.attachments import Attachment, FileStore
from roundtable.core.config import AppConfig, ModelSpec
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import AllChannelsFailed, ChannelRouter, NoChannelAvailable
from roundtable.core.storage import Repository

from .files import TEXT_EXTS, Workspace, clean_path
from .protocol import ToolRequest
from .sandbox import Sandbox, pick_sandbox

IMAGE_EXTS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
STATUS_TEXT = {
    "ok": "成功",
    "error": "出错",
    "timeout": "超时",
    "rejected": "被拒绝",
    "limit": "额度已用完",
}


@dataclass(frozen=True)
class ToolResult:
    tool: str
    status: str  # ok / error / timeout / rejected / limit
    text: str
    files: tuple[str, ...] = ()
    tool_call_id: int | None = None

    def block(self) -> str:
        body = self.text.replace("</tool_result", "<​/tool_result")
        if self.files:
            body += "\n生成 / 更新的文件：" + "、".join(f"out/{f}" for f in self.files)
        state = STATUS_TEXT.get(self.status, self.status)
        return f'<tool_result tool="{self.tool}" status="{state}">\n{body.strip()}\n</tool_result>'


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    return f"{text[:head]}\n……（中间省略 {len(text) - limit} 字）……\n{text[-(limit - head) :]}"


class ToolBox:
    def __init__(
        self,
        *,
        session_id: str,
        table_no: int,
        config: AppConfig,
        router: ChannelRouter,
        prompts: PromptLibrary,
        repo: Repository,
        store: FileStore,
        scrubber: IdentityScrubber,
        attachments: tuple[Attachment, ...] = (),
        sandbox: Sandbox | None = None,
        sandbox_reason: str | None = None,
        budget_ok: Callable[[], bool] = lambda: True,
        seed: str = "",
        project_root: Path | None = None,
    ) -> None:
        self.session_id, self.table_no = session_id, table_no
        self.config, self.router, self.prompts = config, router, prompts
        self.repo, self.store, self.scrubber = repo, store, scrubber
        self.attachments = attachments
        self.rules = config.roundtable.tools
        self.sandbox, self.sandbox_reason = sandbox, sandbox_reason
        self.budget_ok = budget_ok
        self.rng = random.Random(f"{seed}:{table_no}:tools")
        self.project_root = project_root
        self._workspaces: dict[str | None, Workspace] = {}
        self.usage: Counter[tuple[str, str | None, str]] = Counter()

    @classmethod
    def build(cls, **kw) -> ToolBox:
        rules = kw["config"].roundtable.tools
        sandbox, reason = pick_sandbox(rules.python)
        return cls(sandbox=sandbox, sandbox_reason=reason, **kw)

    # --- 可用工具 ----------------------------------------------------------------

    def image_model(self) -> ModelSpec | None:
        pool = [m for m in self.router.available_models() if "image_gen" in m.tags]
        if not pool:
            return None
        rng = random.Random(f"{self.session_id}:image")
        return cheapest(pool, rng, input_tokens=200, output_tokens=1000)

    def tools_for(self, step: str) -> list[str]:
        if not self.rules.enabled:
            return []
        out = []
        for tool in self.rules.by_step.get(step, []):
            if tool == "python" and self.sandbox is None:
                continue
            if tool == "generate_image" and (
                self.rules.image.max_per_step == 0 or self.image_model() is None
            ):
                continue
            out.append(tool)
        return out

    def unavailable(self) -> dict[str, str]:
        """配置了但当前不可用的工具及原因（界面说明用）。"""
        out = {}
        if self.sandbox is None and self.rules.enabled:
            out["python"] = self.sandbox_reason or "没有可用的代码运行环境"
        if self.image_model() is None:
            out["generate_image"] = "没有可用的图像生成模型（需要带 image_gen 标签的模型）"
        return out

    # --- 工作目录 ----------------------------------------------------------------

    def workspace(self, code: str | None) -> Workspace:
        if code not in self._workspaces:
            self._workspaces[code] = Workspace(
                session_id=self.session_id,
                table_no=self.table_no,
                code=code,
                repo=self.repo,
                store=self.store,
                rules=self.rules.files,
                attachments=self.attachments,
                project_root=self.project_root,
            )
        return self._workspaces[code]

    def close(self) -> None:
        for ws in self._workspaces.values():
            ws.close()
        self._workspaces.clear()

    # --- 执行 --------------------------------------------------------------------

    async def execute(
        self,
        req: ToolRequest,
        *,
        step: str,
        code: str | None,
        round_no: int,
        call_id: int | None,
        allowed: list[str],
        on_model_call: Callable[..., None] | None = None,
    ) -> ToolResult:
        """执行一个工具调用并记录；生成的文件登记到该调用名下。"""
        start = time.monotonic()
        self._new_files: list[str] = []
        if req.name not in allowed:
            text = f"本步骤不能使用工具「{req.name}」。"
            result, inputs = (
                ToolResult(req.name or "?", "rejected", text),
                {"body": req.body[:2000]},
            )
        elif not self.budget_ok():
            text = "预算额度已用完，请直接给出最终结果。"
            result, inputs = ToolResult(req.name, "limit", text), {}
        elif req.name == "python":
            result, inputs = await self._python(req, step, code)
        elif req.name == "write_file":
            result, inputs = self._write_file(req, step, code)
        else:
            result, inputs = await self._image(req, step, code, on_model_call)
        tool_call_id = self.repo.record_tool_call(
            self.session_id,
            table_no=self.table_no,
            step=step,
            code=code,
            round_no=round_no,
            tool=req.name or "?",
            input=inputs,
            output=_clip(result.text, 20000),
            status=result.status,
            duration_s=time.monotonic() - start,
            call_id=call_id,
        )
        self.repo.link_files(self._new_files, tool_call_id)
        return ToolResult(result.tool, result.status, result.text, result.files, tool_call_id)

    def _count(self, step: str, code: str | None) -> int:
        return sum(
            1
            for r in self.repo.files(self.session_id)
            if r["table_no"] == self.table_no and r["code"] == code and r["step"] == step
        )

    def _collect(self, step: str, code: str | None) -> tuple[tuple[str, ...], list[str]]:
        ws = self.workspace(code)
        collected = ws.collect(step=step, tool_call_id=None, step_count=self._count(step, code))
        self._new_files += [r["id"] for r in collected.new]
        return tuple(r["path"] for r in collected.new), collected.rejected

    async def _python(self, req: ToolRequest, step: str, code: str | None):
        rules = self.rules.python
        key = (step, code, "python")
        inputs = {"code": req.body}
        if self.sandbox is None:
            return ToolResult("python", "rejected", "代码运行环境不可用。"), inputs
        if self.usage[key] >= rules.max_runs:
            text = f"本步骤已运行 {rules.max_runs} 次，额度已用完，请直接给出最终结果。"
            return ToolResult("python", "limit", text), inputs
        self.usage[key] += 1
        ws = self.workspace(code)
        (ws.root / "main.py").write_text(req.body, encoding="utf-8")
        files_cap = int(self.rules.files.max_session_mb * 2**20)
        run = await self.sandbox.run(ws.root, max_dir_bytes=files_cap)
        (ws.root / "main.py").unlink(missing_ok=True)
        parts = []
        if run.stdout.strip():
            parts.append(run.stdout.rstrip())
        if run.stderr.strip():
            parts.append("[stderr]\n" + run.stderr.rstrip())
        if run.timed_out:
            status, note = "timeout", f"运行超过 {rules.timeout_s:g} 秒，已被结束。"
        elif run.killed == "memory":
            status, note = "error", f"占用内存超过 {rules.memory_mb} MB，已被结束。"
        elif run.killed == "disk":
            status, note = "error", "生成的文件过大，已被结束。"
        elif run.exit_code != 0:
            status, note = "error", "程序出错（见上面的错误信息），可以修改后重新运行。"
        else:
            status, note = "ok", "" if parts else "（没有输出）"
        files, rejected = self._collect(step, code)
        if rejected:
            note += "\n以下文件没有保存：" + "；".join(rejected)
        left = rules.max_runs - self.usage[key]
        note += f"\n（本步骤还可以运行 {left} 次）"
        text = _clip("\n".join(parts), rules.max_output_chars) + "\n" + note.strip()
        return ToolResult("python", status, text.strip(), files), inputs

    def _write_file(self, req: ToolRequest, step: str, code: str | None):
        raw = req.attrs.get("path", "")
        rel = clean_path(raw)
        inputs = {"path": raw, "chars": len(req.body)}
        if rel is None:
            return ToolResult("write_file", "rejected", f"文件路径不合法：{raw!r}"), inputs
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext not in TEXT_EXTS:
            allowed = "、".join(sorted(TEXT_EXTS))
            text = f"write_file 只能写文本类文件（{allowed}）；其他格式请用 python 生成。"
            return ToolResult("write_file", "rejected", text), inputs
        self.workspace(code).write(rel, req.body.encode("utf-8"))
        files, rejected = self._collect(step, code)
        if rejected:
            return ToolResult("write_file", "rejected", "；".join(rejected)), inputs
        text = f"已写入 out/{rel}（{len(req.body)} 字）"
        return ToolResult("write_file", "ok", text, files), inputs

    async def _image(self, req: ToolRequest, step: str, code: str | None, on_model_call):
        raw = req.attrs.get("path", "") or "image.png"
        rel = clean_path(raw)
        description = req.body.strip()
        inputs = {"path": raw, "description": description[:2000]}
        key = (step, code, "generate_image")
        limit = self.rules.image.max_per_step
        model = self.image_model()
        if rel is None:
            return ToolResult("generate_image", "rejected", f"文件路径不合法：{raw!r}"), inputs
        if model is None:
            return ToolResult("generate_image", "rejected", "没有可用的图像生成模型。"), inputs
        if not description:
            return ToolResult("generate_image", "rejected", "请在标签内写出画面描述。"), inputs
        if self.usage[key] >= limit:
            text = f"本步骤最多生成 {limit} 张图片，额度已用完。"
            return ToolResult("generate_image", "limit", text), inputs
        self.usage[key] += 1
        version = self.config.roundtable.prompts["image_gen"]
        clean = self.scrubber.scrub(description)
        prompt = self.prompts.render("image_gen", version, description=clean)
        params = {"max_tokens": self.rules.image.max_tokens}
        try:
            completion = await self.router.complete(model.id, prompt.messages, params)
        except (AllChannelsFailed, NoChannelAvailable) as exc:
            if on_model_call:
                on_model_call(model.id, prompt, None, exc)
            text = "图像生成失败，可以稍后重试或改用 python 画图。"
            return ToolResult("generate_image", "error", text), inputs
        if on_model_call:
            on_model_call(model.id, prompt, completion, None)
        if not completion.images:
            return ToolResult("generate_image", "error", "图像模型没有返回图片。"), inputs
        image = completion.images[0]
        ext = IMAGE_EXTS.get(image.mime, "png")
        stem = rel.rsplit(".", 1)[0] if "." in rel else rel
        rel = f"{stem}.{ext}"
        self.workspace(code).write(rel, image.data)
        files, rejected = self._collect(step, code)
        if rejected:
            return ToolResult("generate_image", "rejected", "；".join(rejected)), inputs
        return ToolResult("generate_image", "ok", f"已生成 out/{rel}", files), inputs
