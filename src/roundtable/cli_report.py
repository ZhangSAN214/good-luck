"""命令行：显示结果、花费、工资单、导出、提交前预估、各模式的产出。

从 cli.py 拆出（代码精简）。
"""

from __future__ import annotations

import io
import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from roundtable.cli_labels import (
    FILE_KINDS,
    JOB_STATE,
    KIND_LABELS,
    KIND_NAMES,
    LENGTH_NAMES,
    LEVEL_NAMES,
    MEDIA_NAMES,
    MODE_LABELS,
    SOURCE_NAMES,
    STANCE_NAMES,
    TOOL_NAMES,
    TOOL_STATUS,
    _group_line,
    _indent,
)
from roundtable.core import ideaboard
from roundtable.core.allocation import coordinator_name
from roundtable.core.office import payslip
from roundtable.core.routing import (
    CUSTOM,
    Question,
    UserChoice,
    attachment_tokens,
    collab_advice,
    confirm_threshold,
    detect_multi_media,
    preview_estimates,
)
from roundtable.core.service import RoundtableService
from roundtable.core.steps.office import group_estimates


class ReportMixin:
    """命令行：显示结果、花费、工资单、导出、提交前预估、各模式的产出。"""

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
            if any(o.kind == "office_review" for o in outputs):
                self.p("\n—— 项目组 ——")
            elif len(tables) > 1:
                self.p(f"\n—— 第 {table_no + 1} 张桌子 ——")
            self.print_style(outputs, table_no, details)
            if any(o.kind == "subtasks" for o in outputs):
                self.print_collab(outputs, table_no, details)
                self.print_office_review(outputs)
                continue
            if any(o.kind in ("ideas", "mindmap") for o in outputs):
                self.print_brainstorm(outputs, table_no, details)
                continue
            if any(o.kind == "office_intake" for o in outputs):
                self.print_office(outputs, table_no, details)
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
        """花费明细：每桌每步的预估与实际（调用次数、输入 / 输出用量，单位大米），以及每次调用。"""
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
                    f"    {self.step_name(step)}：预估 ${planned:.4f}"
                    f"，实际 ${sum(c.cost_usd for c in cs):.4f}"
                    f"（{len(cs)} 次调用{fail}，输入 {self.rice(tin)} / 输出 {self.rice(tout)}）"
                )
        self.p("\n【每次调用】")
        for c in view.calls:
            where = "准备" if c.table_no is None else f"第 {c.table_no + 1} 桌"
            who = self.who(c.role, c.code, c.table_no or 0)
            if c.role == "reviewer" and self.names:  # 身份可以显示时，复核经理也带上模型
                who += f"（{c.model_id}）"
            channel = f" · {c.channel}" if c.channel else ""
            error = f" · 失败：{c.error}" if c.error else ""
            self.p(
                f"  #{c.id} {where} {self.step_name(c.step)} {who}{channel}："
                f"输入 {self.rice(c.input_tokens)} / 输出 {self.rice(c.output_tokens)}，"
                f"${c.cost_usd:.4f}"
                + (f"，{c.latency_s:.1f}s" if c.latency_s is not None else "")
                + error
            )
        if view.workflow == "office":
            self.print_payslip(view)

    def print_payslip(self, view) -> None:
        """办公室模式的工资单：每位员工 / 经理这件事各领多少（token 就是工资），外包另列。"""
        revealed = bool(self.names)

        def lead(model_id: str | None) -> str:
            return coordinator_name(model_id, self.rt.config, office=True) if model_id else "经理"

        def member(code: str) -> str:
            return f"{self.prefix}{code}"

        slip = payslip(view.calls, view.seats, label=member, coordinator_label=lead)
        self.p("\n【工资单】")
        for line in slip.lines:
            model = f"（{line.model_id}）" if revealed and line.model_id else ""
            failed = f"，失败 {line.failed_calls}" if line.failed_calls else ""
            self.p(
                f"  {line.who}{model}：{line.calls} 次{failed}，"
                f"输入 {self.rice(line.input_tokens)} / 输出 {self.rice(line.output_tokens)}，"
                f"${line.cost_usd:.4f}"
            )
        self.p(f"  合计 ${slip.total_usd:.4f}")

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

    def export_document(
        self, sid: str, fmt: str, path: str | None, options: dict | None = None
    ) -> Path:
        """只导出最终成果（Word / PPT / PDF 等），由本地转换，不调用模型。

        path 是已有的文件夹时，用默认文件名存进去。"""
        data, name, _ = RoundtableService(self.rt).export_document(sid, fmt, options)
        target = Path(path or name)
        if target.is_dir():
            target = target / name
        target.write_bytes(data)
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
        kinds = self.rt.config.roundtable.collab.kinds
        self.p("\n【子任务与分工】")
        for s in subtasks:
            who = "、".join(self.label(c, table_no) for c in owners.get(s["id"], [])) or "—"
            kind = f"[{kinds[s['kind']].label}] " if s.get("kind") in kinds else ""
            self.p(f"  {s['id']} {kind}{s['title']}：{who}")
        if assignment and assignment.get("repaired"):
            self.p("  （统筹的分配不符合规则，已由代码补齐 / 调整）")
        info = json.loads(by_kind["subtasks"][0].content).get("info") or {}
        if info.get("source") == "template":
            self.p(
                "  （统筹两次都没有拆出合格的流水线，"
                f"已按模板「{info.get('template_label')}」生成）"
            )
        elif info.get("source") == "retry":
            self.p("  （统筹的第一次拆分不符合流水线规则，已按指出的问题重拆）")
        if by_kind.get("handoff"):
            self.p("\n【交接链】")
            for o in by_kind["handoff"]:
                h = json.loads(o.content)
                files = "、".join(f["name"] for f in h.get("files", []))
                tail = f"（文件：{files}）" if files else ""
                src = f"{h['from_subtask']} {self.label(h['from_code'], table_no)}"
                dst = f"{h['to_subtask']} {self.label(h['to_code'], table_no)}"
                self.p(f"  {src} 把〈{h['gives']}〉交给 {dst}{tail}")
        if by_kind.get("clarify"):
            self.p("\n【澄清提问】")
            for o in by_kind["clarify"]:
                q = json.loads(o.content)
                asker = f"{q['subtask']} {self.label(o.code, table_no)}"
                to = self.label(q["to_code"], table_no) if q.get("to_code") else "上游"
                self.p(f"  {asker} 问 {q['to_subtask']} {to}：{q['question']}")
                self.p(f"    答：{q['answer'] or '（没有答复，按自己的理解完成）'}")
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

    def question(self, question: str, choice: UserChoice, ids: Sequence[str]) -> Question:
        rows = [self.rt.repo.attachment(i) for i in ids]
        params = self.rt.config.routing.estimate
        return Question(
            question,
            tuple(r["kind"] for r in rows),
            attachment_tokens(rows, params),
            choice.media,
            choice.media_tier,
        )

    def preview(self, question: str, choice: UserChoice, ids: Sequence[str], *, seed: int):
        return preview_estimates(
            self.question(question, choice, ids),
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
        mode_names = {
            "office": "办公室",
            "discussion": "讨论",
            "collab": "协同",
            "solo": "个人",
            "brainstorm": "头脑风暴",
            "auto": "自动",
        }
        length = LENGTH_NAMES.get(assessment.difficulty, assessment.difficulty)
        source = SOURCE_NAMES.get(assessment.source, assessment.source)
        self.p(f"提交前预估（答案长度：{length}，{source}；不含规划员）：")
        for o in options:
            if brief and choice.workflow != "auto" and o.workflow != choice.workflow:
                continue
            mark = "▶" if o.plan == plan and o.workflow == choice.workflow else " "
            head = f" {mark} {mode_names.get(o.workflow, o.workflow)} · {o.label}"
            if not o.available or o.estimate is None or o.lineup is None:
                reason = self.rt.scrubber.scrub(o.reason) if anonymous else o.reason
                self.p(f"{head}：不可用（{reason}）")
                continue
            e = o.estimate
            over = (
                "  超过确认门槛"
                if e.total_usd > confirm_threshold(self.rt.config, o.workflow)
                else ""
            )
            absent = f"，缺席 {len(o.lineup.absent)}" if o.lineup.absent else ""
            office = o.workflow == "office"
            member, lead = ("员工", "经理") if office else ("组员", "统筹")
            coord = f" + {lead}" if o.lineup.coordinator else ""
            self.p(
                f"{head}：{len(o.lineup.members)} 位{member}{coord}{absent}，"
                f"预计 ${e.total_usd:.4f}（最多约 ${e.max_usd:.4f}）{over}"
            )
            if office:
                groups = group_estimates(
                    self.rt.config, self.rt.router, self.question(question, choice, ids), o.lineup
                )
                if groups:
                    self.p(
                        "      经理如果决定"
                        + "；".join(_group_line(m, g) for m, g in groups.items())
                    )
            if not anonymous and not brief:
                seated = "在岗" if office else "上桌"
                self.p(
                    f"      {seated}：{'、'.join(o.lineup.members)}；{lead} {o.lineup.coordinator}"
                )
            if not brief:
                steps = "，".join(
                    f"{self.step_name(st.step)} ${st.cost_usd:.4f}"
                    for st in e.steps
                    if st.step != "reveal"
                )
                self.p(f"      {steps}")
        hint = detect_multi_media(question, self.rt.config.routing.multi_media)
        if hint is not None and choice.workflow == "discussion":
            self.p(f"⚠ {collab_advice(hint)['message']}（加 --mode collab 即可）")
        self.p("")

    def latest_files(self, sid: str) -> list[dict[str, Any]]:
        latest: dict[tuple[int, str | None, str], dict[str, Any]] = {}
        for row in self.rt.repo.files(sid):
            latest[(row["table_no"], row["code"], row["path"])] = row
        return list(latest.values())

    def print_style(self, outputs, table_no: int, details: bool) -> None:
        """风格规范（有风格参考图又要画图时）与风格校验的结果。"""
        spec = next((o for o in outputs if o.kind == "style_spec"), None)
        if spec is None:
            return
        data = json.loads(spec.content)
        how = "文字版" if data["text_only"] else f"{len(data['extractors'])} 位成员看了原图"
        self.p(f"\n【风格规范】（{how}；风格清单 {len(data['checklist'])} 条）")
        if data.get("warning"):
            self.p(f"  ⚠ {data['warning']}")
        if details:
            self.p(_indent(self.t(data["spec"])))
            for i, item in enumerate(data["checklist"], 1):
                self.p(f"  {i}. {self.t(item)}")
        gates = [
            json.loads(o.content) | {"code": o.code} for o in outputs if o.kind == "style_gate"
        ]
        for g in gates:
            if not details and not g["final"]:
                continue
            who = self.label(g["code"], table_no)
            failed = [c for c in g["checks"] if not c["ok"]]
            verdict = "通过" if g["passed"] else "不通过：" + "；".join(c["item"] for c in failed)
            tail = "" if g["passed"] or not g["final"] else "（重画次数用完，保留最后一版）"
            self.p(f"  风格校验 · {who} · {g['subtask']} 第 {g['attempt']} 次：{verdict}{tail}")

    def print_media(self, sid: str, revealed: bool, details: bool) -> None:
        """媒体生成：每次生成的轮次、状态、花费、文件（--details 时显示提示词；揭晓后显示模型）。"""
        jobs = self.rt.repo.media_jobs(sid)
        if not jobs:
            return
        scrub = (lambda t: t) if revealed else self.rt.scrubber.scrub
        self.p("\n【媒体生成】")
        for j in jobs:
            lead = "经理" if self.office else "统筹"
            who = self.label(j["code"], j["table_no"]) if j["code"] else lead
            where = f"{who} · {j['subtask']}" if j["subtask"] else self.step_name(j["step"])
            model = f" · {j['model_id']}（{j['channel']}）" if revealed and j["channel"] else ""
            self.p(
                f"  {MEDIA_NAMES.get(j['kind'], j['kind'])} · {where} · 第 {j['round']} 轮"
                f"（尝试 {j['attempt']}）：{JOB_STATE.get(j['state'], j['state'])}"
                f" · ${j['cost_usd']:.4f}{model}"
                + (f" · 参考图 {j['reference_count']} 张" if j["reference_count"] else "")
            )
            if j["params"].get("warning"):
                self.p(f"    ⚠ {j['params']['warning']}")
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
                    f"\n· {who} · {self.step_name(c['step'])} · 第 {c['round']} 轮"
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

    def print_office(self, outputs, table_no: int, details: bool) -> None:
        """办公室模式：经理怎么派的活、员工的成果、经理过目的意见，最后是交付的版本。"""
        by_kind = {o.kind: o for o in outputs}
        intake = json.loads(by_kind["office_intake"].content)
        if "office_approval" in by_kind:
            intake.update(json.loads(by_kind["office_approval"].content))
        if intake["mode"] == "meeting":
            self.print_meeting(outputs, intake, table_no, details)
            return
        if intake["mode"] == "project":
            who = "、".join(self.label(c, table_no) for c in intake["attendees"])
            self.p(f"\n【经理分诊】项目分工，项目组：{who}（经理拆分、合并，另一位经理验收）")
            self.print_budget(intake)
            if intake.get("reason"):
                self.p(f"  理由：{self.t(intake['reason'])}")
            return
        who = self.label(intake["assignee"], table_no)
        how = MODE_LABELS.get(intake["mode"], intake["mode"])
        search = "，需要查资料" if intake.get("needs_search") else ""
        if intake.get("declined"):
            how = f"{MODE_LABELS.get(intake['declined'], '')}（你选择改为单人办）"
        self.p(f"\n【经理分诊】经理建议{how}，派给 {who}{search}")
        self.print_budget(intake)
        if intake.get("by_rule"):
            self.p(f"  （{intake.get('rule_reason') or '按规则派活'}）")
        elif intake.get("reason"):
            self.p(f"  理由：{self.t(intake['reason'])}")
        if details and intake.get("brief"):
            self.p(f"  工作说明：{self.t(intake['brief'])}")
        work = by_kind.get("office_work")
        fix = by_kind.get("office_fix")
        check = by_kind.get("office_check")
        if details and work and fix:
            draft = json.loads(work.content)["text"]
            self.p(f"\n【{self.label(work.code, table_no)} 的初稿】\n{self.t(draft)}")
        if check:
            c = json.loads(check.content)
            verdict = "通过" if c["verdict"] == "pass" else "退回修改一次"
            if c.get("degraded"):
                verdict = "没有完成，直接交付"
            self.p(f"\n【经理过目】{verdict}" + (f"：{self.t(c['note'])}" if c.get("note") else ""))
            for i, issue in enumerate(c.get("issues") or [], 1):
                self.p(f"  {i}. {self.t(issue)}")
        final = fix or work
        if final:
            self.p(f"\n【交付】\n{self.t(json.loads(final.content)['text'])}")

    def print_office_review(self, outputs) -> None:
        """办公室模式的项目组：复核经理的验收意见；退回改过的版本作为交付。"""
        review = next((o for o in outputs if o.kind == "office_review"), None)
        if review is None:
            return
        r = json.loads(review.content)
        if r.get("skipped"):
            self.p("\n【复核经理验收】没有第二位经理可以复核，直接交付合并的成果")
        elif r.get("degraded"):
            self.p("\n【复核经理验收】没有完成，直接交付合并的成果")
        else:
            verdict = "通过" if r["verdict"] == "pass" else "退回修改一次"
            note = f"：{self.t(r['note'])}" if r.get("note") else ""
            self.p(f"\n【复核经理验收】{verdict}{note}")
            for i, issue in enumerate(r.get("issues") or [], 1):
                self.p(f"  {i}. {self.t(issue)}")
        revised = next((o for o in outputs if o.kind == "office_revised"), None)
        if revised:
            self.p(f"\n【交付（按验收意见修改后）】\n{self.t(json.loads(revised.content)['text'])}")

    def print_budget(self, intake) -> None:
        """预算单：经理分诊后按选定的方式和人选估计还要花多少。"""
        if intake.get("budget_usd") is not None:
            how = MODE_LABELS.get(intake["mode"], intake["mode"])
            self.p(f"  预算单：{how}预计 ${intake['budget_usd']:.4f}")

    def print_meeting(self, outputs, intake, table_no: int, details: bool) -> None:
        """办公室模式·开会：谁参会、各人发言（详情里）、经理拍板的结论与纪要。"""
        who = "、".join(self.label(c, table_no) for c in intake["attendees"])
        self.p(f"\n【经理分诊】开会，参会：{who}（依次发言，经理拍板）")
        self.print_budget(intake)
        if intake.get("attendees_by_rule"):
            self.p("  （经理点名的人不够，按规则补齐）")
        elif intake.get("reason"):
            self.p(f"  理由：{self.t(intake['reason'])}")
        if details and intake.get("brief"):
            self.p(f"  会议议题：{self.t(intake['brief'])}")
        if details:
            for o in outputs:
                if o.kind == "office_speech":
                    text = json.loads(o.content)["text"]
                    self.p(f"\n【{self.label(o.code, table_no)} 发言】\n{self.t(text)}")
        decision = next((o for o in outputs if o.kind == "office_decision"), None)
        if decision:
            d = json.loads(decision.content)
            title = "【交付】"
            if d.get("degraded"):
                title = "【交付（经理没能拍板，以下是各位的发言）】"
            self.p(f"\n{title}\n{self.t(d['text'])}")

    def print_brainstorm(self, outputs, table_no: int, details: bool) -> None:
        """头脑风暴：详情里列出每人的点子、拓展 / 碰撞杂交与投票；最后给出点子榜和整合后的大纲。"""
        kinds = {
            "build": "深化",
            "combine": "组合",
            "challenge": "质疑改进",
            "new": "全新想法",
            "hybrid": "杂交",
            "flip": "反转放大",
            "fix": "挑刺补救",
        }
        if details:
            for o in outputs:
                who = self.label(o.code, table_no)
                if o.kind == "ideas":
                    data = json.loads(o.content)
                    items = data["ideas"]
                    lens = f"（抽到「{data['lens']}」）" if data.get("lens") else ""
                    self.p(f"\n【{who} 的想法{lens}】")
                    for i in items:
                        detail = f"：{self.t(i['detail'])}" if i["detail"] else ""
                        self.p(f"  {i['id']} {self.t(i['title'])}{detail}")
                elif o.kind == "expansions":
                    items = json.loads(o.content)["items"]
                    if items:
                        what = "碰撞杂交" if o.step == "crossbreed" else "拓展"
                        self.p(f"\n【{who} 的{what}】")
                    for x in items:
                        pair = f"{x['on']} × {x['also']}" if x.get("also") else x["on"]
                        on = f"（针对 {pair}）" if x["on"] else ""
                        self.p(f"  [{kinds.get(x['kind'], x['kind'])}]{on} {self.t(x['text'])}")
                elif o.kind == "votes":
                    d = json.loads(o.content)
                    picks = "、".join(
                        f"{p['id']}（{p['novelty'] + p['feasibility'] + p['value']}）"
                        for p in d["picks"]
                    )
                    dark = f"；黑马 {d['dark_horse']}" if d.get("dark_horse") else ""
                    self.p(f"\n【{who} 投票】{picks or '没有有效的票'}{dark}")
        mm = next((o for o in outputs if o.kind == "mindmap"), None)
        if mm is None:
            return
        data = json.loads(mm.content)
        if data.get("board"):
            self.p("\n" + self.t(ideaboard.to_markdown(ideaboard.Board.from_dict(data["board"]))))
        if data["map"].get("degraded"):
            self.p("\n（统筹没能整合出导图，以下按成员分别列出各自的想法）")
        self.p("\n【思维导图（大纲）】\n" + self.t(data["markdown"]))
        self.p("保存图片或代码：roundtable mindmap <会话 id> -o 导图.svg（也可用 .md / .mmd）")

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
        lead = "经理" if view.workflow == "office" else "统筹"
        self.p("\n" + "═" * 40 + "\n" + title)
        for seat in view.seats:
            who = self.label(seat.code) if seat.role == "member" else lead
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
                else {"planner": "规划员", "reviewer": "复核经理"}.get(c.role, lead)
            )
            self.p(
                f"  {self.step_name(c.step):<6} {who:<6} {c.model_id}  渠道：{trail or '-'}"
                f"  ${c.cost_usd:.4f}"
            )
        usage = self.rt.repo.spent_by_channel()
        month, day = self.rt.budget.status()
        self.p("\n" + month.describe() + (f"；{day.describe()}" if day else ""))
        self.p("按渠道累计：" + "；".join(f"{k} ${v.cost_usd:.4f}" for k, v in usage.items()))
