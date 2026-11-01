"""本地 ComfyUI（http://127.0.0.1:8188）：只出图，不花钱，不需要 key。

流程：读工作流模板（ComfyUI「导出 API 格式」的 JSON，放在 config/comfyui/，
路由的 model 字段写文件名）
→ 上传参考图（POST /upload/image）→ 填占位符 → POST /prompt → 轮询 GET /history/{id}
→ GET /view 取 SaveImage 的第一张图。

本机改过的模板（换了底模文件名等）存成同名的 *.local.json（不进 git），有就优先用它。

模板里的占位符（只认整串字符串值，或字符串里的 {{prompt}} / {{negative}}，不认具体节点 id）：
    "{{prompt}}"  正向提示词          "{{negative}}"  负向提示词
    "{{seed}}"    随机种子（填数字）   "{{width}}" / "{{height}}"  宽高（填数字）
    "{{ref_1}}" … "{{ref_3}}"  上传后的参考图文件名（参考图比占位符少时重复用第一张；
                               一张都没有时改用参数 workflow_without_refs 指定的模板）

提示词里有一行以「negative:」开头时，这行之后是负向、之前是正向（行首「positive:」去掉）；
没有时整段都是正向，负向用参数 negative。

规则同其他适配器：不重试（失败抛 ProviderError，换模型由调用方负责）；外部错误文本脱敏截断、
用 from None 切断异常链。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from roundtable.core.config.loader import DEFAULT_CONFIG_DIR
from roundtable.core.config.schema import ChannelSpec

from ._http import kind_for_status, safe_detail
from .base import ImageOutput, Media, Message, Provider, RawCompletion
from .errors import ErrorKind, ProviderError, UnsupportedCapability
from .registry import register_adapter
from .secrets import Secret

DEFAULT_WORKFLOW_DIR = DEFAULT_CONFIG_DIR / "comfyui"

# 调用参数（模型 / 路由的 params）里由适配器自己用掉、不进工作流的项
DEFAULTS: dict[str, Any] = {
    "width": 832,
    "height": 1216,
    "poll_interval_s": 1.0,  # 轮询 /history 的间隔
    "timeout_s": 300.0,  # 提交后最多等多久（8GB 显存 SDXL 一张约 20～60 秒，首次加载模型更久）
    "negative": "",
    "seed": None,  # 不填时按提示词算一个（同一提示词可复现）
    "workflow_without_refs": None,  # 没有参考图时用的模板（去掉 IPAdapter 分支的变体）
}

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_0-9]+)\s*\}\}")
_REF = re.compile(r"^ref_(\d+)$")
_NUMERIC = frozenset({"seed", "width", "height"})
_TEXT = frozenset({"prompt", "negative"})
_NEGATIVE_LINE = re.compile(r"^\s*negative\s*:", re.IGNORECASE | re.MULTILINE)
_POSITIVE_PREFIX = re.compile(r"^\s*positive\s*:\s*", re.IGNORECASE)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+\.json$")
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def split_prompt(text: str, default_negative: str = "") -> tuple[str, str]:
    """把提示词拆成 (正向, 负向)。"""
    match = _NEGATIVE_LINE.search(text)
    if not match:
        return _POSITIVE_PREFIX.sub("", text.strip()), default_negative
    positive = _POSITIVE_PREFIX.sub("", text[: match.start()].strip())
    negative = text[match.end() :].strip()
    return positive, negative or default_negative


def ref_slots(workflow: Any) -> int:
    """模板里最大的 {{ref_N}} 编号（没有参考图占位符时为 0）。"""
    found = [
        int(m.group(1))
        for name in _PLACEHOLDER.findall(json.dumps(workflow, ensure_ascii=False))
        if (m := _REF.match(name))
    ]
    return max(found, default=0)


def fill(workflow: Any, values: dict[str, Any]) -> Any:
    """填占位符。整串是 {{seed}} / {{width}} / {{height}} 时换成数字；{{prompt}} / {{negative}}
    可以嵌在字符串里；{{ref_N}} 换成上传后的文件名。遇到没有值的占位符报 KeyError。"""
    if isinstance(workflow, dict):
        return {k: fill(v, values) for k, v in workflow.items()}
    if isinstance(workflow, list):
        return [fill(v, values) for v in workflow]
    if not isinstance(workflow, str):
        return workflow
    whole = _PLACEHOLDER.fullmatch(workflow.strip())
    if whole and whole.group(1) in _NUMERIC:
        return int(values[whole.group(1)])

    def sub(m: re.Match[str]) -> str:
        return str(values[m.group(1)])

    return _PLACEHOLDER.sub(sub, workflow)


def save_nodes(workflow: dict[str, Any]) -> list[str]:
    """SaveImage 节点 id（按模板里的顺序）。"""
    return [
        nid
        for nid, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == "SaveImage"
    ]


def default_seed(positive: str, negative: str) -> int:
    digest = hashlib.sha256(f"{positive}\n{negative}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _sniff(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


@register_adapter("comfyui")
class ComfyUIProvider(Provider):
    def __init__(
        self,
        channel: str,
        spec: ChannelSpec,
        key: Secret | None,  # 不用：本地 ComfyUI 不需要 key
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
        workflow_dir: Path | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(channel)
        self._dir = Path(workflow_dir or DEFAULT_WORKFLOW_DIR)
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.AsyncClient(
            base_url=spec.base_url.rstrip("/") + "/", timeout=timeout_s, transport=transport
        )

    def _error(self, kind: ErrorKind, detail: str = "", **kw: Any) -> ProviderError:
        return ProviderError(kind, self.channel, safe_detail(detail), **kw)

    async def complete(
        self, model: str, messages: Sequence[Message], params: dict[str, Any]
    ) -> RawCompletion:
        raise UnsupportedCapability(f"{self.channel} 只能出图")

    # --- HTTP ----------------------------------------------------------------------

    async def _send(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kw)
        except httpx.TimeoutException:
            raise self._error(ErrorKind.TIMEOUT) from None
        except httpx.TransportError as exc:
            # 最常见：ComfyUI 没开（连不上 127.0.0.1:8188）
            detail = f"连不上 ComfyUI（{type(exc).__name__}）"
            raise self._error(ErrorKind.NETWORK, detail) from None
        if response.status_code >= 400:
            raise self._error(
                kind_for_status(response.status_code),
                _error_text(response),
                status=response.status_code,
            ) from None
        return response

    def _json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            raise self._error(ErrorKind.INVALID_RESPONSE, "返回的不是 JSON") from None

    # --- 模板 ----------------------------------------------------------------------

    def _template(self, name: str) -> dict[str, Any]:
        if not _SAFE_NAME.match(name):
            raise self._error(ErrorKind.BAD_REQUEST, f"工作流模板名不合法：{name}")
        path = self._dir / name
        local = path.with_name(f"{path.stem}.local.json")  # 本机改过的版本（不进 git）优先
        if local.is_file():
            path = local
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise self._error(ErrorKind.BAD_REQUEST, f"找不到工作流模板 {name}") from None
        except (OSError, ValueError) as exc:
            raise self._error(
                ErrorKind.BAD_REQUEST, f"工作流模板 {name} 读不了（{type(exc).__name__}）"
            ) from None
        if not isinstance(data, dict) or not save_nodes(data):
            raise self._error(ErrorKind.BAD_REQUEST, f"工作流模板 {name} 里没有 SaveImage 节点")
        return data

    async def _upload(self, media: Media) -> str:
        digest = hashlib.sha256(media.data).hexdigest()[:16]
        name = f"roundtable_{digest}.{_EXT.get(media.mime, 'png')}"
        response = await self._send(
            "POST",
            "upload/image",
            files={"image": (name, media.data, media.mime)},
            data={"overwrite": "true"},
        )
        body = self._json(response)
        if not isinstance(body, dict) or not body.get("name"):
            raise self._error(ErrorKind.INVALID_RESPONSE, "上传参考图后没有返回文件名")
        sub = str(body.get("subfolder") or "")
        return f"{sub}/{body['name']}" if sub else str(body["name"])

    # --- 出图 ----------------------------------------------------------------------

    async def generate_image(
        self,
        model: str,
        prompt: str,
        params: dict[str, Any],
        images: Sequence[Media] = (),
        api: str = "chat",
    ) -> ImageOutput:
        opts = {**DEFAULTS, **{k: v for k, v in params.items() if k in DEFAULTS}}
        refs = [m for m in images if m.kind == "image"]
        workflow = self._template(model)
        slots = ref_slots(workflow)
        if slots and not refs:
            if not opts["workflow_without_refs"]:
                raise self._error(ErrorKind.BAD_REQUEST, "这个工作流需要参考图")
            workflow = self._template(str(opts["workflow_without_refs"]))
            slots = ref_slots(workflow)
        positive, negative = split_prompt(prompt, str(opts["negative"]))
        seed = opts["seed"]
        values: dict[str, Any] = {
            "prompt": positive,
            "negative": negative,
            "seed": default_seed(positive, negative) if seed is None else int(seed),
            "width": int(opts["width"]),
            "height": int(opts["height"]),
        }
        names = [await self._upload(m) for m in refs[:slots]]
        for i in range(1, slots + 1):
            values[f"ref_{i}"] = names[i - 1] if i <= len(names) else names[0]
        try:
            graph = fill(workflow, values)
        except KeyError as exc:
            raise self._error(ErrorKind.BAD_REQUEST, f"模板里有不认识的占位符 {exc}") from None

        submitted = self._json(
            await self._send("POST", "prompt", json={"prompt": graph, "client_id": "roundtable"})
        )
        prompt_id = submitted.get("prompt_id") if isinstance(submitted, dict) else None
        if not prompt_id:
            raise self._error(ErrorKind.INVALID_RESPONSE, "提交后没有返回 prompt_id")
        entry = await self._wait(str(prompt_id), float(opts["timeout_s"]), opts["poll_interval_s"])
        image = await self._fetch(entry, save_nodes(graph))
        return ImageOutput((image,), cost_usd=0.0)

    async def _wait(self, prompt_id: str, timeout_s: float, interval: float) -> dict[str, Any]:
        deadline = self._clock() + timeout_s
        while True:
            history = self._json(await self._send("GET", f"history/{prompt_id}"))
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(entry, dict):
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise self._error(ErrorKind.SERVER, _execution_error(status))
                if entry.get("outputs") and status.get("completed", True):
                    return entry
            if self._clock() >= deadline:
                await self._cancel(prompt_id)
                raise self._error(ErrorKind.TIMEOUT, f"等了 {timeout_s:g} 秒还没画完")
            await self._sleep(float(interval))

    async def _cancel(self, prompt_id: str) -> None:
        """超时后把还在排队的任务删掉（尽力而为，失败不管）。"""
        with contextlib.suppress(httpx.HTTPError):
            await self._client.post("queue", json={"delete": [prompt_id]})

    async def _fetch(self, entry: dict[str, Any], order: list[str]) -> Media:
        outputs = entry.get("outputs") or {}
        ids = [n for n in order if n in outputs] + [n for n in outputs if n not in order]
        for nid in ids:
            for item in (outputs.get(nid) or {}).get("images") or []:
                if not isinstance(item, dict) or not item.get("filename"):
                    continue
                response = await self._send(
                    "GET",
                    "view",
                    params={
                        "filename": item["filename"],
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    },
                )
                if response.content:
                    mime = response.headers.get("content-type", "").split(";")[0]
                    if not mime.startswith("image/"):
                        mime = _sniff(response.content)
                    return Media("image", mime, response.content)
        raise self._error(ErrorKind.INVALID_RESPONSE, "画完了但没有输出图片")

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_text(response: httpx.Response) -> str:
    """ComfyUI 的错误体：{"error": {"message", "details"}, "node_errors": {...}}。"""
    try:
        body = response.json()
    except ValueError:
        return response.text
    if not isinstance(body, dict):
        return str(body)
    err = body.get("error")
    parts = []
    if isinstance(err, dict):
        parts += [str(err.get("message") or ""), str(err.get("details") or "")]
    elif err:
        parts.append(str(err))
    for nid, node in (body.get("node_errors") or {}).items():
        for e in (node or {}).get("errors") or []:
            parts.append(f"节点 {nid}：{e.get('message', '')} {e.get('details', '')}")
    return " ".join(p for p in parts if p.strip())


def _execution_error(status: dict[str, Any]) -> str:
    for message in status.get("messages") or []:
        if isinstance(message, list) and len(message) == 2 and message[0] == "execution_error":
            info = message[1] or {}
            return f"节点 {info.get('node_type', '?')} 出错：{info.get('exception_message', '')}"
    return "ComfyUI 执行出错"
