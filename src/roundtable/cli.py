"""命令行试用：用真实模型跑一场圆桌。

    roundtable ask "题目"                         # 便宜档全员上桌
    roundtable ask --tier flagship "题目"         # 旗舰档全员上桌
    roundtable ask --models a,b,c "题目"          # 自选上桌的模型
    roundtable ask --anonymous "题目"             # 匿名：结束前只显示代号
    roundtable models                             # 查看模型、档位、哪些渠道有 key
    roundtable history                            # 最近的讨论
    roundtable resume <会话 id>                   # 中断后继续
    roundtable show <会话 id>                     # 查看结果
    roundtable reveal <会话 id>                   # 揭晓身份（匿名讨论）

匿名讨论在揭晓前，终端上不会出现任何模型名、厂商名或渠道名。
发给模型的内容无论是否匿名都只用代号。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from roundtable.core.config import ConfigError
from roundtable.core.orchestrator import Orchestrator, RunResult
from roundtable.core.routing import Question, RoutingError, UserChoice
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
}
SOURCE_NAMES = {"rule": "规则判断", "model": "规划员判断", "default": "默认"}
LENGTH_NAMES = {"simple": "短", "medium": "中等", "hard": "长", None: "未判断"}


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
        elif e.type == "budget_warning":
            self.p(f"⚠ {d['message']}")

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
        tables = sorted({o.table_no for o in view.outputs})
        for table_no in tables:
            outputs = [o for o in view.outputs if o.table_no == table_no]
            if len(tables) > 1:
                self.p(f"\n—— 第 {table_no + 1} 张桌子 ——")
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
        if view.error:
            self.p(f"\n错误：{view.error}")

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
    ) -> int:
        if not self.check_available():
            return 2
        self.p(f"题目：{question}\n")
        result = await self.orc.start(Question(question), choice, seed=seed, anonymous=anonymous)
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
    history = sub.add_parser("history", help="最近的讨论")
    history.add_argument("--limit", type=int, default=20)
    for name, text in (("resume", "中断后继续"), ("show", "查看结果"), ("reveal", "揭晓身份")):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("session_id")
        if name == "show":
            cmd.add_argument("--details", action="store_true")
            cmd.add_argument("--raw", action="store_true")
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
                )
            else:
                if args.coordinator:
                    cli.p("--coordinator 只能和 --models 一起使用。")
                    return 2
                choice = UserChoice(args.tier)
            return await cli.cmd_ask(
                question,
                choice,
                seed=args.seed,
                details=args.details,
                reveal=args.reveal,
                anonymous=args.anonymous,
            )
        if args.command == "models":
            return cli.cmd_models()
        if args.command == "history":
            return cli.cmd_history(args.limit)
        if args.command == "resume":
            result = await cli.orc.resume(args.session_id)
            return await cli.finish(result, details=False, reveal=None)
        if args.command == "show":
            cli.show(args.session_id, details=args.details)
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


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Windows 终端下保证中文正常输出
    try:
        return asyncio.run(run(argv))
    except KeyboardInterrupt:
        print("\n已中断。可以用 roundtable history 找到会话，再用 roundtable resume <id> 继续。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
