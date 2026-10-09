"""命令行试用：用真实模型跑一场圆桌。

    roundtable ask "题目"                         # 便宜档全员上桌
    roundtable ask --tier flagship "题目"         # 旗舰档全员上桌
    roundtable ask --models a,b,c "题目"          # 自选上桌的模型
    roundtable ask --anonymous "题目"             # 匿名：结束前只显示代号
    roundtable ask --mode collab "题目"           # 协同：拆分子任务、分工完成、合并
    roundtable ask --attach 图.png --attach 讲义.pdf "题目"   # 带附件
    roundtable estimate "题目"                     # 提交前预估：各模式 × 各档位的花费
    roundtable models                             # 查看模型、档位、哪些渠道有 key
    roundtable history                            # 最近的讨论
    roundtable stats                              # 各模型的历史贡献
    roundtable resume <会话 id>                   # 中断后继续
    roundtable show <会话 id>                     # 查看结果（--costs 显示花费明细）
    roundtable export <会话 id> [-o 文件]         # 导出完整记录（UTF-8 文本文件，含花费明细）
    roundtable files <会话 id> [-o 目录]          # 保存成员生成的文件（代码、图片、文档等）
    roundtable reveal <会话 id>                   # 揭晓身份（匿名讨论）

匿名讨论在揭晓前，终端上不会出现任何模型名、厂商名或渠道名。
发给模型的内容无论是否匿名都只用代号。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import secrets
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from roundtable.core.attachments import UploadError, ingest
from roundtable.core.config import ConfigError
from roundtable.core.orchestrator import Orchestrator, OrchestratorError, RunResult
from roundtable.core.routing import (
    CUSTOM,
    Question,
    RoutingError,
    UserChoice,
    attachment_tokens,
    preview_estimates,
)
from roundtable.core.runtime import Runtime
from roundtable.core.steps import Event
from roundtable.plaintext import to_terminal

STEP_NAMES = {
    "plan": "规划",
    "answer": "独立作答",
    "review": "匿名互评",
    "revise": "修订",
    "synthesize": "汇总",
    "reveal": "揭晓准备",
    "decompose": "统筹拆分子任务",
    "volunteer": "成员自荐",
    "assign": "统筹分配",
    "work": "完成子任务",
    "cross_review": "交叉审查",
    "rework": "按审查修改",
    "merge": "统筹合并",
    "attachments": "处理附件",
    "media": "生成媒体",
}
MEDIA_NAMES = {"image": "图片", "speech": "语音", "video": "视频"}
JOB_STATE = {
    "submitted": "已提交",
    "pending": "排队中",
    "running": "生成中",
    "completed": "完成",
    "failed": "失败",
    "timeout": "超时",
}
STANCE_NAMES = {"want": "想做", "can": "可以做", "unfit": "不适合"}
LEVEL_NAMES = {"full": "全部采用", "partial": "部分采用", "none": "未采用"}
SOURCE_NAMES = {"rule": "规则判断", "model": "规划员判断", "default": "默认"}
KIND_NAMES = {
    "answered": "作答",
    "adopted": "被采纳的要点",
    "volunteer_accepted": "自荐被采纳",
    "valid_review": "有效评审",
    "valid_issue": "指出的有效问题",
    "issue_accepted": "被作者采纳的问题",
    "redo": "被打回重做",
    "lazy": "敷衍",
    "dropped": "退出",
}
TOOL_NAMES = {
    "python": "运行代码",
    "write_file": "写文件",
    "generate_image": "生成图片",
    "search": "联网搜索",
    "fetch": "读取网页",
}
TOOL_STATUS = {
    "ok": "成功",
    "error": "出错",
    "timeout": "超时",
    "rejected": "被拒绝",
    "limit": "额度已用完",
}
FILE_KINDS = {
    "image": "图片",
    "audio": "音频",
    "video": "视频",
    "code": "代码",
    "text": "文本",
    "table": "表格",
    "document": "文档",
}
KIND_LABELS = {"image": "图片", "pdf": "PDF", "docx": "Word", "text": "文本", "audio": "音频"}
LENGTH_NAMES = {"simple": "短", "medium": "中等", "hard": "长", None: "未判断"}


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


class CLI:
    def __init__(
        self,
        runtime: Runtime,
        *,
        out: TextIO = sys.stdout,
        ask: Callable[[str], str] = input,
        auto_confirm: bool = False,
        plain_math: bool = True,
    ) -> None:
        self.rt = runtime
        self.out = out
        self.ask = ask
        self.auto_confirm = auto_confirm
        self.plain_math = plain_math
        self.orc = Orchestrator(runtime, on_event=self.on_event)
        self.prefix = runtime.config.personas.code_prefix
        routing = runtime.config.routing
        self.plan_labels = {name: p.label for name, p in routing.plans.items()}
        self.plan_labels["custom"] = routing.custom.label
        # 匿名关闭时：代号 → 模型 id（按桌）；匿名开启且未揭晓时为空
        self.names: dict[tuple[int, str | None], str] = {}

    def p(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def t(self, text: str) -> str:
        """模型输出：数学式转纯文本、去掉加粗符号（--raw 时保持原样）。"""
        return to_terminal(text) if self.plain_math else text

    def label(self, code: str | None, table_no: int = 0) -> str:
        base = f"{self.prefix}{code}" if code else "统筹"
        model = self.names.get((table_no, code))
        return f"{base}（{model}）" if model else base

    def load_names(self, sid: str) -> None:
        """身份可以显示时（匿名关闭，或已揭晓）记下各座位对应的模型。"""
        self.names = {}
        if not self.rt.repo.is_revealed(sid):
            return
        for seat in self.rt.repo.seats(sid):
            code = seat["code"] if seat["role"] == "member" else None
            self.names[(seat["table_no"], code)] = seat["model_id"]

    # --- 实时进度（事件只含代号，不含模型身份） --------------------------------------

    def on_event(self, sid: str, e: Event) -> None:
        d = e.data
        if e.type == "routed":
            plan = self.plan_labels.get(d["plan"], d["plan"])
            absent = f"，{d['absent']} 个模型缺席（无可用渠道）" if d.get("absent") else ""
            self.p(
                f"档位：{plan}（{d['members']} 位组员 + 统筹{absent}）"
                f"  预计 ${d['estimate_usd']:.4f}"
                f"  答案长度：{LENGTH_NAMES.get(d['difficulty'], d['difficulty'])}"
                f"（{SOURCE_NAMES.get(d['source'], d['source'])}）"
            )
            others = [
                f"{self.plan_labels.get(k, k)} ${v:.4f}"
                for k, v in d["options"].items()
                if v is not None and k != d["plan"]
            ]
            if others:
                self.p(f"  其他档位预计：{'；'.join(others)}")
        elif e.type == "table_started":
            self.load_names(sid)
            if e.table_no > 0:
                self.p(
                    f"\n=== 第 {e.table_no + 1} 张桌子：{self.plan_labels.get(d['plan'], '')} ==="
                )
            if self.rt.repo.session_row(sid)["workflow"] == "collab":
                self.p("模式：协同（拆分子任务 → 自荐 → 分工完成 → 交叉审查 → 合并）")
            members = "、".join(self.label(c, e.table_no) for c in d["members"])
            self.p(f"上桌：{members} + {self.label(None, e.table_no)}")
        elif e.type == "step_started" and e.step != "reveal":
            self.p(f"\n▶ {STEP_NAMES.get(e.step, e.step)}")
        elif e.type == "call_done":
            self.p(f"  {self.label(e.code, e.table_no)} 完成（${d.get('cost', 0):.4f}）")
        elif e.type == "call_failed":
            self.p(f"  {self.label(e.code, e.table_no)} 调用失败")
        elif e.type == "member_dropped":
            self.p(f"  {self.label(e.code, e.table_no)} 退出本轮（已有内容保留）")
        elif e.type == "step_finished" and d.get("degraded"):
            names = "、".join(self.label(None if c == "coordinator" else c) for c in d["degraded"])
            self.p(f"  注意：{names} 的输出格式不符，已降级处理")
        elif e.type == "escalating":
            self.p(f"\n⚠ 建议升级：{d['reason']}（预计 ${d['estimate_usd']:.4f}）")
        elif e.type == "effort_redo":
            reasons = "；".join(d.get("reasons", []))
            self.p(f"  {self.label(e.code, e.table_no)} 的产出没有实质内容，打回重做：{reasons}")
        elif e.type == "effort_flagged":
            self.p(f"  ⚠ {self.label(e.code, e.table_no)} 重做后仍不合格，标记为敷衍")
        elif e.type == "budget_warning":
            self.p(f"⚠ {d['message']}")
        elif e.type == "media_started":
            kind = MEDIA_NAMES.get(d.get("kind"), d.get("kind"))
            self.p(f"  生成{kind}（第 {d.get('round', 1)} 轮）…")
        elif e.type == "media_progress":
            self.p(f"  视频任务：{JOB_STATE.get(d.get('state'), d.get('state'))}")
        elif e.type == "media_failed":
            self.p(f"  ⚠ {MEDIA_NAMES.get(d.get('kind'), '媒体')}生成失败")
        elif e.type == "tool_finished":
            who = self.label(e.code, e.table_no or 0)
            tool = TOOL_NAMES.get(d.get("tool"), d.get("tool"))
            status = TOOL_STATUS.get(d.get("status"), d.get("status"))
            files = f"，生成 {'、'.join(d['files'])}" if d.get("files") else ""
            self.p(f"  {who} {tool}：{status}{files}")

    # --- 确认卡片 --------------------------------------------------------------------

    def choose(self, card: dict[str, Any]) -> str:
        self.p("\n" + "─" * 40)
        self.p(f"需要你确认：{card['situation']}")
        options = card["options"]
        for n, o in enumerate(options, 1):
            cost = f"  预计 ${o['cost_usd']:.4f}" if o.get("cost_usd") is not None else ""
            mark = " ← 推荐" if o["key"] == card["recommendation"] else ""
            note = f"（{o['note']}）" if o.get("note") else ""
            self.p(f"  {n}. {o['label']}{note}{cost}{mark}")
        if card.get("reason"):
            self.p(f"  推荐理由：{card['reason']}")
        if self.auto_confirm and card["kind"] in ("cost", "escalation", "members"):
            self.p("  （--yes：自动选择继续）")
            return "continue"
        keys = [o["key"] for o in options]
        while True:
            raw = self.ask(
                f"请输入序号（直接回车 = 推荐项 {keys.index(card['recommendation']) + 1}）："
            )
            raw = raw.strip()
            if not raw:
                return card["recommendation"]
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return keys[int(raw) - 1]
            if raw in keys:
                return raw
            self.p("  无效的输入，请重新选择。")

    async def drive(self, result: RunResult) -> RunResult:
        while result.checkpoint is not None:
            response = self.choose(result.checkpoint.card)
            result = await self.orc.respond(result.session_id, response)
        return result

    # --- 结果 ----------------------------------------------------------------------

    def show(self, sid: str, *, details: bool = False) -> None:
        view = self.rt.repo.session_view(sid, scrub=self.rt.scrubber.scrub)
        self.load_names(sid)
        self.p("\n" + "═" * 40)
        self.p(f"状态：{view.status}    本题花费：${view.cost_usd:.4f}    会话：{view.id}")
        if view.routing:
            r = view.routing
            self.p(
                f"档位：{self.plan_labels.get(r.get('plan'), r.get('plan'))}  "
                f"预估 ${r.get('estimated_cost_usd', 0):.4f}"
                + ("  （已升级）" if r.get("escalated") else "")
            )
        for row in self.rt.repo.session_attachments(sid):
            state = {"ready": "", "pending": "，待处理", "failed": f"，失败：{row['error']}"}
            source = {"vision": "，已生成文字版", "transcribe": "，已转写"}.get(
                row["text_source"] or "", ""
            )
            kind = KIND_LABELS.get(row["kind"], row["kind"])
            self.p(f"附件：{row['name']}（{kind}{source}{state.get(row['status'], '')}）")
            if details and row["text_source"] in ("vision", "transcribe") and row["text"]:
                self.p(f"\n【{row['name']} 的文字版】\n{self.t(row['text'].strip())}\n")
        tables = sorted({o.table_no for o in view.outputs})
        for table_no in tables:
            outputs = [o for o in view.outputs if o.table_no == table_no]
            if len(tables) > 1:
                self.p(f"\n—— 第 {table_no + 1} 张桌子 ——")
            if any(o.kind == "subtasks" for o in outputs):
                self.print_collab(outputs, table_no, details)
                continue
            if details:
                for o in outputs:
                    who = self.label(o.code, table_no)
                    if o.kind == "answer":
                        self.p(f"\n【{who} 的答案】\n{self.t(o.content)}")
                    elif o.kind == "review":
                        self.print_review(who, json.loads(o.content), table_no)
                    elif o.kind == "revision":
                        rev = json.loads(o.content)
                        tag = "（无有效评审，沿用原答案）" if rev["skipped"] else ""
                        self.p(f"\n【{who} 修订后{tag}】\n{self.t(rev['answer'])}")
            synth = next((o for o in outputs if o.kind == "synthesis"), None)
            if synth:
                self.print_synthesis(json.loads(synth.content), table_no)
            elif not details:
                answers = [o for o in outputs if o.kind in ("revision", "answer")]
                if len(answers) == 1:
                    only = answers[0]
                    text = (
                        json.loads(only.content)["answer"]
                        if only.kind == "revision"
                        else only.content
                    )
                    self.p(f"\n【答案】\n{self.t(text)}")
        self.print_media(sid, view.revealed, details)
        self.print_tools(sid, view.revealed, details)
        self.print_contributions(sid, view.revealed)
        if view.error:
            self.p(f"\n错误：{view.error}")

    def print_costs(self, sid: str) -> None:
        """花费明细：每桌每步的预估与实际（调用次数、输入 / 输出 token），以及每次调用。"""
        view = self.rt.repo.session_view(sid, scrub=self.rt.scrubber.scrub)
        estimates = {t["table_no"]: t["estimate"] for t in self.rt.repo.tables(sid)}
        self.p("\n【花费明细】")
        for role, title in (("planner", "规划员"), ("preprocess", "附件预处理")):
            calls = [c for c in view.calls if c.table_no is None and c.role == role]
            if calls:
                cost = sum(c.cost_usd for c in calls)
                self.p(f"  {title}：{len(calls)} 次调用 ${cost:.4f}")
        steps: dict[tuple[int, str], list] = defaultdict(list)
        for c in view.calls:
            if c.table_no is not None:
                steps[(c.table_no, c.step)].append(c)
        for table_no in sorted({t for t, _ in steps} | set(estimates)):
            est = estimates.get(table_no) or {}
            spent = sum(c.cost_usd for (t, _), cs in steps.items() if t == table_no for c in cs)
            limit = f"（最多约 ${est['max']:.4f}）" if est.get("max") else ""
            self.p(
                f"  第 {table_no + 1} 桌：预估 ${est.get('total', 0):.4f}{limit}，实际 ${spent:.4f}"
            )
            order = list(
                dict.fromkeys([*est.get("steps", {}), *(s for t, s in steps if t == table_no)])
            )
            for step in order:
                cs = steps.get((table_no, step), [])
                if not cs and not est.get("steps", {}).get(step):
                    continue
                tin = sum(c.input_tokens for c in cs)
                tout = sum(c.output_tokens for c in cs)
                failed = sum(c.failed for c in cs)
                fail = f"，失败 {failed}" if failed else ""
                planned = est.get("steps", {}).get(step, 0)
                self.p(
                    f"    {STEP_NAMES.get(step, step)}：预估 ${planned:.4f}"
                    f"，实际 ${sum(c.cost_usd for c in cs):.4f}"
                    f"（{len(cs)} 次调用{fail}，输入 {tin} / 输出 {tout} token）"
                )
        self.p("\n【每次调用】")
        for c in view.calls:
            where = "准备" if c.table_no is None else f"第 {c.table_no + 1} 桌"
            who = {"planner": "规划员", "preprocess": "附件预处理"}.get(c.role) or self.label(
                c.code, c.table_no or 0
            )
            channel = f" · {c.channel}" if c.channel else ""
            error = f" · 失败：{c.error}" if c.error else ""
            self.p(
                f"  #{c.id} {where} {STEP_NAMES.get(c.step, c.step)} {who}{channel}："
                f"输入 {c.input_tokens} / 输出 {c.output_tokens} token，${c.cost_usd:.4f}"
                + (f"，{c.latency_s:.1f}s" if c.latency_s is not None else "")
                + error
            )

    def export(self, sid: str, path: str | None) -> Path:
        """把完整记录（含每位成员的产出和花费明细）写成 UTF-8 文本文件，不经过终端编码。"""
        buffer = io.StringIO()
        out, self.out = self.out, buffer
        try:
            self.show(sid, details=True)
            self.print_costs(sid)
        finally:
            self.out = out
        target = Path(path or f"roundtable-{sid[:8]}.txt")
        # 带 BOM：Windows 记事本和旧版 PowerShell 也能正确识别 UTF-8
        target.write_text(buffer.getvalue(), encoding="utf-8-sig")
        return target

    def print_collab(self, outputs, table_no: int, details: bool) -> None:
        """协同模式：子任务与分工、（--details 时）各份成果与审查、合并结果。"""
        by_kind: dict[str, list] = {}
        for o in outputs:
            by_kind.setdefault(o.kind, []).append(o)
        subtasks = json.loads(by_kind["subtasks"][0].content)["subtasks"]
        assignment = (
            json.loads(by_kind["assignment"][0].content) if "assignment" in by_kind else None
        )
        owners = {a["subtask"]: a["members"] for a in (assignment or {}).get("assignments", [])}
        self.p("\n【子任务与分工】")
        for s in subtasks:
            who = "、".join(self.label(c, table_no) for c in owners.get(s["id"], [])) or "—"
            self.p(f"  {s['id']} {s['title']}：{who}")
        if assignment and assignment.get("repaired"):
            self.p("  （统筹的分配不符合规则，已由代码补齐）")
        if details:
            for o in by_kind.get("volunteer", []):
                v = json.loads(o.content)
                prefs = "，".join(
                    f"{p['subtask']} {STANCE_NAMES.get(p['stance'], p['stance'])}"
                    for p in v["preferences"]
                )
                self.p(f"\n【{self.label(o.code, table_no)} 的自荐】{v['strengths']}（{prefs}）")
            final = {
                (json.loads(o.content)["subtask"], o.code): json.loads(o.content)
                for o in by_kind.get("rework", [])
            }
            for o in by_kind.get("work", []):
                w = json.loads(o.content)
                rework = final.get((w["subtask"], o.code))
                text = rework["answer"] if rework and not rework["skipped"] else w["text"]
                tag = "（已按审查修改）" if rework and not rework["skipped"] else ""
                who = self.label(o.code, table_no)
                self.p(f"\n【{w['subtask']} · {who}{tag}】\n{self.t(text)}")
            for o in by_kind.get("cross_review", []):
                self.print_review(self.label(o.code, table_no), json.loads(o.content), table_no)
        if "merge" in by_kind:
            m = json.loads(by_kind["merge"][0].content)
            if m.get("degraded"):
                self.p("\n（统筹的合并不可用，以下为各份成果原文）")
            self.p(f"\n【完整成果】（把握程度：{m['confidence']}）\n{self.t(m['result'])}")
            adoption = [
                f"{a_s['subtask']} {self.label(a['member'], table_no)}"
                f" {LEVEL_NAMES.get(a['level'], a['level'])}"
                for a_s in m.get("subtasks", [])
                for a in a_s["adopted"]
            ]
            if adoption:
                self.p("\n【采纳情况】" + "；".join(adoption))
            for title, key in (("【缺失】", "gaps"), ("【仍需核实】", "open_questions")):
                if m.get(key):
                    self.p("\n" + title)
                    for x in m[key]:
                        self.p(f"  · {self.t(x)}")

    # --- 提交前预估 -------------------------------------------------------------------

    def preview(self, question: str, choice: UserChoice, ids: Sequence[str], *, seed: int):
        rows = [self.rt.repo.attachment(i) for i in ids]
        params = self.rt.config.routing.estimate
        q = Question(
            question,
            tuple(r["kind"] for r in rows),
            attachment_tokens(rows, params),
            choice.media,
            choice.media_tier,
        )
        return preview_estimates(
            q,
            config=self.rt.config,
            router=self.rt.router,
            seed=seed,
            recent_coordinators=self.rt.repo.recent_coordinators(),
            history=self.orc.estimate_history(),
            custom=choice if choice.tier == CUSTOM else None,
        )

    def print_preview(
        self,
        question: str,
        choice: UserChoice,
        ids: Sequence[str] = (),
        *,
        seed: int,
        anonymous: bool,
        brief: bool = False,
    ) -> None:
        """各模式 × 各档位的预计花费（不调用模型）。brief：只列所选模式，并标出所选档位。"""
        assessment, options = self.preview(question, choice, ids, seed=seed)
        cfg = self.rt.config.routing
        plan = choice.tier or cfg.default_plan
        threshold = cfg.confirm_threshold_usd
        mode_names = {"discussion": "讨论", "collab": "协同"}
        length = LENGTH_NAMES.get(assessment.difficulty, assessment.difficulty)
        source = SOURCE_NAMES.get(assessment.source, assessment.source)
        self.p(f"提交前预估（答案长度：{length}，{source}；不含规划员）：")
        for o in options:
            if brief and o.workflow != choice.workflow:
                continue
            mark = "▶" if o.plan == plan and o.workflow == choice.workflow else " "
            head = f" {mark} {mode_names.get(o.workflow, o.workflow)} · {o.label}"
            if not o.available or o.estimate is None or o.lineup is None:
                reason = self.rt.scrubber.scrub(o.reason) if anonymous else o.reason
                self.p(f"{head}：不可用（{reason}）")
                continue
            e = o.estimate
            over = "  超过确认门槛" if e.total_usd > threshold else ""
            absent = f"，缺席 {len(o.lineup.absent)}" if o.lineup.absent else ""
            self.p(
                f"{head}：{len(o.lineup.members)} 位组员 + 统筹{absent}，"
                f"预计 ${e.total_usd:.4f}（最多约 ${e.max_usd:.4f}）{over}"
            )
            if not anonymous and not brief:
                self.p(f"      上桌：{'、'.join(o.lineup.members)}；统筹 {o.lineup.coordinator}")
            if not brief:
                steps = "，".join(
                    f"{STEP_NAMES.get(st.step, st.step)} ${st.cost_usd:.4f}"
                    for st in e.steps
                    if st.step != "reveal"
                )
                self.p(f"      {steps}")
        self.p("")

    def latest_files(self, sid: str) -> list[dict[str, Any]]:
        latest: dict[tuple[int, str | None, str], dict[str, Any]] = {}
        for row in self.rt.repo.files(sid):
            latest[(row["table_no"], row["code"], row["path"])] = row
        return list(latest.values())

    def print_media(self, sid: str, revealed: bool, details: bool) -> None:
        """媒体生成：每次生成的轮次、状态、花费、文件（--details 时显示提示词；揭晓后显示模型）。"""
        jobs = self.rt.repo.media_jobs(sid)
        if not jobs:
            return
        scrub = (lambda t: t) if revealed else self.rt.scrubber.scrub
        self.p("\n【媒体生成】")
        for j in jobs:
            who = self.label(j["code"], j["table_no"]) if j["code"] else "统筹"
            where = (
                f"{who} · {j['subtask']}" if j["subtask"] else STEP_NAMES.get(j["step"], j["step"])
            )
            model = f" · {j['model_id']}（{j['channel']}）" if revealed and j["channel"] else ""
            self.p(
                f"  {MEDIA_NAMES.get(j['kind'], j['kind'])} · {where} · 第 {j['round']} 轮"
                f"（尝试 {j['attempt']}）：{JOB_STATE.get(j['state'], j['state'])}"
                f" · ${j['cost_usd']:.4f}{model}"
            )
            if j["error"]:
                self.p(f"    原因：{scrub(j['error'])}")
            if details:
                self.p("    提示词：\n" + _indent(self.t(scrub(j["prompt"]))))

    def print_tools(self, sid: str, revealed: bool, details: bool) -> None:
        """工具调用（--details 时逐条显示）与生成的文件。"""
        scrub = (lambda t: t) if revealed else self.rt.scrubber.scrub
        calls = self.rt.repo.tool_calls(sid)
        if details and calls:
            self.p("\n【工具调用】")
            for c in calls:
                who = self.label(c["code"], c["table_no"])
                tool = TOOL_NAMES.get(c["tool"], c["tool"])
                status = TOOL_STATUS.get(c["status"], c["status"])
                took = f"，{c['duration_s']:.1f}s" if c["duration_s"] else ""
                self.p(
                    f"\n· {who} · {STEP_NAMES.get(c['step'], c['step'])} · 第 {c['round']} 轮"
                    f" · {tool}：{status}{took}"
                )
                if c["tool"] == "python" and c["input"].get("code"):
                    self.p("  代码：\n" + _indent(scrub(c["input"]["code"])[:3000]))
                elif c["input"].get("path"):
                    self.p(f"  文件：{c['input']['path']}")
                elif c["tool"] == "search":
                    self.p(f"  搜索：{scrub(c['input'].get('query', ''))}")
                    for src in c["input"].get("sources", []):
                        self.p(f"    [{src['id']}] {src['title']}  {src['url']}")
                    continue
                elif c["tool"] == "fetch" and c["input"].get("url"):
                    self.p(f"  读取：[{c['input']['source']}] {c['input']['url']}")
                    continue
                if c["output"]:
                    self.p("  结果：\n" + _indent(scrub(c["output"])[:2000]))
        files = self.latest_files(sid)
        if files:
            self.p("\n【生成的文件】")
            for f in files:
                kind = FILE_KINDS.get(f["kind"], f["kind"])
                who = self.label(f["code"], f["table_no"])
                self.p(f"  {who}：{f['path']}（{kind}，{f['size'] / 1024:.1f} KB）")
            self.p(f"  保存到本地：roundtable files {sid}")

    def save_files(self, sid: str, target: str | None) -> Path:
        """把每位成员生成的文件（最新版本）保存到目录：<目录>/<成员>/<文件路径>。"""
        root = Path(target or f"roundtable-{sid[:8]}-files")
        self.load_names(sid)
        for f in self.latest_files(sid):
            folder = self.label(f["code"], f["table_no"]).replace("（", "_").replace("）", "")
            if len({r["table_no"] for r in self.latest_files(sid)}) > 1:
                folder = f"第{f['table_no'] + 1}桌_{folder}"
            path = root / folder / f["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.rt.files.load(f["storage_key"]))
        return root

    def print_contributions(self, sid: str, revealed: bool) -> None:
        rows = self.rt.repo.contributions(sid)
        if not rows:
            return
        grouped: dict[tuple[int, str], dict[str, int]] = {}
        for r in rows:
            grouped.setdefault((r["table_no"], r["code"]), {})[r["kind"]] = r["amount"]
        multi = len({t for t, _ in grouped}) > 1
        self.p("\n【贡献】")
        for (table_no, code), counts in grouped.items():
            who = self.label(code, table_no) if revealed else self.label(code)
            prefix = f"第 {table_no + 1} 桌 " if multi else ""
            parts = [
                f"{KIND_NAMES[k]} {counts[k]}"
                for k in KIND_NAMES
                if k != "answered" and k in counts
            ]
            self.p(f"  {prefix}{who}：{'，'.join(parts) or '—'}")

    def print_review(self, reviewer: str, data: dict[str, Any], table_no: int = 0) -> None:
        self.p(f"\n【{reviewer} 的评审】")
        for r in data["reviews"]:
            state = "有效" if r["valid"] else f"无效：{'；'.join(r['invalid_reasons'])}"
            self.p(f"  → {self.label(r['target'], table_no)}：{r['verdict']}（{state}）")
            for i in r["issues"]:
                where, what, fix = (self.t(i[k]) for k in ("location", "problem", "suggestion"))
                self.p(f"     · [{i['severity']}] {where}：{what} → {fix}")

    def print_synthesis(self, s: dict[str, Any], table_no: int = 0) -> None:
        if s.get("degraded"):
            self.p("\n（统筹的汇总不可用，以下为兜底结果）")
        if s["consensus"]:
            self.p("\n【共识】")
            for c in s["consensus"]:
                self.p(f"  · {self.t(c)}")
        if s["disagreements"]:
            self.p("\n【分歧】")
            for d in s["disagreements"]:
                status = "已裁定" if d.get("resolved") else "未解决"
                self.p(f"  · {self.t(d['point'])}（{status}）")
                for pos in d["positions"]:
                    who = "、".join(self.label(m, table_no) for m in pos["members"]) or "?"
                    self.p(f"      {who}：{self.t(pos['view'])}")
                if d.get("assessment"):
                    self.p(f"      统筹评估：{self.t(d['assessment'])}")
        self.p(f"\n【最终答案】（把握程度：{s['confidence']}）\n{self.t(s['final_answer'])}")
        if s["open_questions"]:
            self.p("\n【仍需核实】")
            for q in s["open_questions"]:
                self.p(f"  · {self.t(q)}")

    def reveal_identities(self, sid: str) -> None:
        if not self.rt.repo.session_row(sid)["anonymous"]:
            self.p("这场讨论没有开启匿名，身份一直是公开的。")
            self.print_identities(sid, "上桌的模型")
            return
        self.orc.reveal_identities(sid)
        self.print_identities(sid, "揭晓身份")

    def print_identities(self, sid: str, title: str) -> None:
        view = self.rt.repo.session_view(sid)
        self.names = {}  # 这里单独列出模型，代号后不再重复
        self.p("\n" + "═" * 40 + "\n" + title)
        for seat in view.seats:
            who = self.label(seat.code) if seat.role == "member" else "统筹"
            table = f"第 {seat.table_no + 1} 桌 " if any(s.table_no for s in view.seats) else ""
            self.p(f"  {table}{who} = {seat.model_id}")
        planner = view.routing.get("planner_model") if view.routing else None
        if planner:
            self.p(f"  规划员 = {planner}")
        self.p("\n每次调用：")
        for c in view.calls:
            trail = " → ".join(
                f"{a['channel']}{'' if a['ok'] else '(' + (a['error'] or '失败') + ')'}"
                for a in c.attempts
            )
            who = (
                self.label(c.code)
                if c.role == "member"
                else {"planner": "规划员"}.get(c.role, "统筹")
            )
            self.p(
                f"  {STEP_NAMES.get(c.step, c.step):<6} {who:<6} {c.model_id}  渠道：{trail or '-'}"
                f"  ${c.cost_usd:.4f}"
            )
        usage = self.rt.repo.spent_by_channel()
        month, day = self.rt.budget.status()
        self.p("\n" + month.describe() + (f"；{day.describe()}" if day else ""))
        self.p("按渠道累计：" + "；".join(f"{k} ${v.cost_usd:.4f}" for k, v in usage.items()))

    # --- 命令 ----------------------------------------------------------------------

    async def cmd_ask(
        self,
        question: str,
        choice: UserChoice,
        *,
        seed: int | None,
        details: bool,
        reveal: bool | None,
        anonymous: bool = False,
        attach: Sequence[str] = (),
    ) -> int:
        if not self.check_available():
            return 2
        ids = []
        for path in attach:
            try:
                data = Path(path).read_bytes()
                a = ingest(
                    Path(path).name,
                    data,
                    config=self.rt.config,
                    router=self.rt.router,
                    repo=self.rt.repo,
                    store=self.rt.files,
                )
            except OSError as exc:
                self.p(f"无法读取附件 {path}：{exc.strerror}")
                return 2
            except UploadError as exc:
                self.p(f"附件不可用：{exc}")
                return 2
            note = "，".join(a.warnings)
            self.p(
                f"附件：{a.name}（{KIND_LABELS.get(a.kind, a.kind)}）"
                + (f" ⚠ {note}" if note else "")
            )
            ids.append(a.id)
        self.p(f"题目：{question}\n")
        seed = secrets.randbelow(2**31) if seed is None else seed
        self.print_preview(question, choice, ids, seed=seed, anonymous=anonymous, brief=True)
        try:
            result = await self.orc.start(
                Question(question, (), 0, choice.media, choice.media_tier),
                choice,
                seed=seed,
                anonymous=anonymous,
                attachments=ids,
            )
        except OrchestratorError as exc:
            self.p(f"无法开始：{exc}")
            return 2
        return await self.finish(result, details=details, reveal=reveal)

    async def finish(self, result: RunResult, *, details: bool, reveal: bool | None) -> int:
        result = await self.drive(result)
        for w in result.warnings:
            self.p(f"⚠ {w}")
        if result.error:
            self.p(f"无法开始：{result.error}")
        self.show(result.session_id, details=details)
        if result.status != "completed":
            if result.status in ("paused", "running", "awaiting_confirmation"):
                self.p(f"\n可以用 roundtable resume {result.session_id} 继续。")
            return 1
        if not self.rt.repo.session_row(result.session_id)["anonymous"]:
            self.print_identities(result.session_id, "上桌的模型与每次调用")
            return 0
        if reveal is None:
            raw = self.ask("\n按回车揭晓身份，输入 n 跳过：").strip().lower()
            reveal = raw not in ("n", "no", "否")
        if reveal:
            self.reveal_identities(result.session_id)
        else:
            self.p(f"\n稍后可用 roundtable reveal {result.session_id} 揭晓。")
        return 0

    def check_available(self) -> bool:
        if self.rt.router.available_models():
            return True
        self.p(
            "没有可用的模型：请在项目根目录的 .env 中填写至少一个渠道的 key（参考 .env.example）。"
        )
        for channel, reason in sorted(self.rt.unavailable_channels.items()):
            self.p(f"  {channel}：{reason}")
        return False

    def cmd_models(self) -> int:
        available = {m.id for m in self.rt.router.available_models()}
        self.p(f"渠道模式：{self.rt.router.mode}")
        for m in self.rt.config.models.models:
            plan = self.rt.router.plan(m.id)
            channels = "、".join(r.channel for r in plan.usable) or "无"
            mark = "✓" if m.id in available else "✗"
            self.p(
                f"  {mark} {m.id:<22} {m.tier or '-':<8} ${m.price.input:g}/${m.price.output:g}"
                f"  可用渠道：{channels}"
            )
        if self.rt.unavailable_channels:
            self.p("缺少 key 的渠道：" + "、".join(sorted(self.rt.unavailable_channels)))
        month, day = self.rt.budget.status()
        self.p(month.describe() + (f"；{day.describe()}" if day else ""))
        return 0

    def cmd_stats(self) -> int:
        """跨会话按模型统计贡献（只统计身份已公开的会话）。"""
        rows = self.rt.repo.contribution_history()
        if not rows:
            self.p("还没有可统计的讨论（匿名讨论揭晓后才计入）。")
            return 0
        by_model: dict[str, dict[str, int]] = {}
        sessions: dict[str, int] = {}
        for r in rows:
            by_model.setdefault(r["model_id"], {})[r["kind"]] = r["amount"]
            sessions[r["model_id"]] = max(sessions.get(r["model_id"], 0), r["sessions"])
        for model, counts in by_model.items():
            parts = [f"{KIND_NAMES[k]} {counts[k]}" for k in KIND_NAMES if k in counts]
            self.p(f"  {model:<22} {sessions[model]} 场  " + "，".join(parts))
        return 0

    def cmd_history(self, limit: int) -> int:
        for s in self.rt.repo.list_sessions(limit):
            q = s.question if len(s.question) <= 30 else s.question[:30] + "…"
            self.p(f"{s.created_at[:19]}  {s.id}  {s.status:<22} ${s.cost_usd:.4f}  {q}")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="roundtable", description="AI 圆桌命令行试用")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="提一道题并跑完整个圆桌")
    ask.add_argument("question", nargs="?", help="题目；省略时从 --file 或交互输入读取")
    ask.add_argument("--file", help="从 UTF-8 文本文件读取题目")
    ask.add_argument(
        "--attach",
        action="append",
        default=[],
        metavar="文件",
        help="附件（可多次使用）：图片、PDF、Word .docx、文本、mp3 / wav 音频",
    )
    lineup = ask.add_mutually_exclusive_group()
    lineup.add_argument(
        "--tier", help="成员档位（routing.yaml 的 plans，如 budget / flagship）：该档位全员上桌"
    )
    lineup.add_argument(
        "--models", help="自选：逗号分隔的模型 id，全部上桌（见 roundtable models）"
    )
    ask.add_argument("--coordinator", help="自选时指定由哪个模型当统筹（必须在 --models 中）")
    ask.add_argument(
        "--anonymous", action="store_true", help="匿名：结束前只显示代号，结束后可揭晓"
    )
    ask.add_argument(
        "--mode",
        choices=["discussion", "collab"],
        default="discussion",
        help="discussion 讨论（全员各自作答再互评汇总，默认）/ collab 协同（拆分子任务分工完成）",
    )
    ask.add_argument(
        "--media",
        choices=["image", "speech", "video"],
        help="输出类型（仅讨论模式）：商定提示词 → 生成图片 / 语音 / 视频 → 评审 → 重新生成",
    )
    ask.add_argument(
        "--media-tier",
        choices=["budget", "flagship"],
        help="媒体模型的档位（默认 budget）；协同模式下也适用于媒体子任务",
    )
    ask.add_argument("--seed", type=int, help="随机种子（用于复现）")
    ask.add_argument("--details", action="store_true", help="显示每位组员的答案、评审和修订稿")
    ask.add_argument(
        "--raw", action="store_true", help="保持模型输出的原样（不转换数学式、不去掉加粗符号）"
    )
    ask.add_argument(
        "--yes", action="store_true", help="花费 / 升级确认自动选择继续（预算确认仍会询问）"
    )
    reveal = ask.add_mutually_exclusive_group()
    reveal.add_argument("--reveal", action="store_true", default=None, help="结束后直接揭晓")
    reveal.add_argument("--no-reveal", dest="reveal", action="store_false", help="结束后不揭晓")

    sub.add_parser("models", help="查看模型、档位与可用渠道")
    sub.add_parser("stats", help="各模型的历史贡献（被采纳、有效问题、敷衍次数等）")
    history = sub.add_parser("history", help="最近的讨论")
    history.add_argument("--limit", type=int, default=20)
    for name, text in (("resume", "中断后继续"), ("show", "查看结果"), ("reveal", "揭晓身份")):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("session_id")
        if name == "show":
            cmd.add_argument("--details", action="store_true")
            cmd.add_argument("--raw", action="store_true")
            cmd.add_argument("--costs", action="store_true", help="显示每步与每次调用的花费")
    estimate = sub.add_parser("estimate", help="提交前预估：各模式 × 各档位的上桌人数与预计花费")
    estimate.add_argument("question", nargs="?", help="题目；省略时从 --file 读取")
    estimate.add_argument("--file", help="从 UTF-8 文本文件读取题目")
    estimate.add_argument("--tier", help="标出要用的档位")
    estimate.add_argument("--models", help="自选：逗号分隔的模型 id")
    estimate.add_argument("--coordinator", help="自选时指定统筹")
    estimate.add_argument("--anonymous", action="store_true", help="不显示上桌的模型名")
    estimate.add_argument("--media", choices=["image", "speech", "video"], help="输出类型")
    estimate.add_argument("--media-tier", choices=["budget", "flagship"], help="媒体模型的档位")
    estimate.add_argument("--seed", type=int, help="随机种子（与 ask --seed 相同时阵容一致）")
    files = sub.add_parser("files", help="把成员生成的文件保存到本地目录")
    files.add_argument("session_id")
    files.add_argument("-o", "--output", help="目录，默认 roundtable-<会话 id 前 8 位>-files")
    export = sub.add_parser("export", help="导出完整记录到 UTF-8 文本文件（含花费明细）")
    export.add_argument("session_id")
    export.add_argument("-o", "--output", help="输出文件，默认 roundtable-<会话 id 前 8 位>.txt")
    export.add_argument("--raw", action="store_true", help="保持模型输出的原样")
    return parser


async def run(
    argv: list[str] | None = None,
    *,
    runtime: Runtime | None = None,
    out: TextIO = sys.stdout,
    ask: Callable[[str], str] = input,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        rt = runtime or Runtime.build()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=out)
        return 2
    cli = CLI(
        rt,
        out=out,
        ask=ask,
        auto_confirm=getattr(args, "yes", False),
        plain_math=not getattr(args, "raw", False),
    )
    try:
        if args.command == "ask":
            question = args.question
            if args.file:
                with open(args.file, encoding="utf-8") as f:
                    question = f.read().strip()
            if not question:
                question = ask("请输入题目：").strip()
            if not question:
                cli.p("题目不能为空。")
                return 2
            if args.models:
                choice = UserChoice(
                    "custom",
                    models=tuple(m.strip() for m in args.models.split(",") if m.strip()),
                    coordinator=args.coordinator,
                    workflow=args.mode,
                    media=args.media,
                    media_tier=args.media_tier,
                )
            else:
                if args.coordinator:
                    cli.p("--coordinator 只能和 --models 一起使用。")
                    return 2
                choice = UserChoice(
                    args.tier, workflow=args.mode, media=args.media, media_tier=args.media_tier
                )
            return await cli.cmd_ask(
                question,
                choice,
                seed=args.seed,
                details=args.details,
                reveal=args.reveal,
                anonymous=args.anonymous,
                attach=args.attach,
            )
        if args.command == "models":
            return cli.cmd_models()
        if args.command == "stats":
            return cli.cmd_stats()
        if args.command == "history":
            return cli.cmd_history(args.limit)
        if args.command == "resume":
            result = await cli.orc.resume(args.session_id)
            return await cli.finish(result, details=False, reveal=None)
        if args.command == "show":
            cli.show(args.session_id, details=args.details)
            if args.costs:
                cli.print_costs(args.session_id)
            return 0
        if args.command == "estimate":
            question = args.question
            if args.file:
                with open(args.file, encoding="utf-8") as f:
                    question = f.read().strip()
            if not question:
                cli.p("题目不能为空。")
                return 2
            models = tuple(m.strip() for m in (args.models or "").split(",") if m.strip())
            extra = {"media": args.media, "media_tier": args.media_tier}
            choice = (
                UserChoice("custom", models, args.coordinator, **extra)
                if models
                else UserChoice(args.tier, **extra)
            )
            seed = args.seed if args.seed is not None else secrets.randbelow(2**31)
            cli.print_preview(question, choice, seed=seed, anonymous=args.anonymous)
            cli.p(f"随机种子：{seed}（roundtable ask --seed {seed} 时上桌名单与此一致）")
            return 0
        if args.command == "files":
            if not cli.latest_files(args.session_id):
                cli.p("这场讨论没有生成文件。")
                return 0
            root = cli.save_files(args.session_id, args.output)
            print(f"已保存到 {root.resolve()}", file=out)
            return 0
        if args.command == "export":
            target = cli.export(args.session_id, args.output)
            print(f"已导出到 {target.resolve()}", file=out)
            return 0
        if args.command == "reveal":
            cli.reveal_identities(args.session_id)
            return 0
    except RoutingError as exc:
        cli.p(f"无法开始：{exc}")
        return 2
    finally:
        if runtime is None:
            await rt.aclose()
    return 0


def _utf8_streams() -> None:
    """输出统一用 UTF-8（Windows 终端、PowerShell 管道重定向时也是）。

    errors="replace" 保证遇到无法编码的字符也不会中途报错、留下空文件。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    try:
        return asyncio.run(run(argv))
    except KeyboardInterrupt:
        print("\n已中断。可以用 roundtable history 找到会话，再用 roundtable resume <id> 继续。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
