"""命令行试用：用真实模型跑一场圆桌。

    roundtable ask "题目"                         # 节电模式（便宜档全员）上桌
    roundtable ask --tier flagship "题目"         # 全力模式（旗舰档全员）上桌
    roundtable ask --models a,b,c "题目"          # 自选上桌的模型
    roundtable ask --anonymous "题目"             # 匿名：结束前只显示代号
    roundtable ask --mode collab "题目"           # 协同：拆分子任务、分工完成、合并
    roundtable ask --attach 图.png --attach 讲义.pdf "题目"   # 带附件
    roundtable estimate "题目"                     # 提交前预估：各模式 × 各档位的花费
    roundtable models                             # 查看模型、档位、哪些渠道有 key
    roundtable models --check-updates             # 人才市场：价格变动、下架、新模型（只提示）
    roundtable history                            # 最近的讨论
    roundtable stats                              # 各模型的历史贡献
    roundtable performance [--reset]              # 办公室模式的员工绩效（--reset 清零）
    roundtable resume <会话 id>                   # 中断后继续
    roundtable show <会话 id>                     # 查看结果（--costs 显示花费明细）
    roundtable export <会话 id> [-o 文件]         # 导出完整记录（UTF-8 文本文件，含花费明细）
    roundtable export <会话 id> --format docx     # 只导出成果：md/docx/pptx/pdf/html，不调用模型
    roundtable files <会话 id> [-o 目录]          # 保存成员生成的文件（代码、图片、文档等）
    roundtable reveal <会话 id>                   # 揭晓身份（匿名讨论）

匿名讨论在揭晓前，终端上不会出现任何模型名、厂商名或渠道名。
发给模型的内容无论是否匿名都只用代号。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import secrets
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from roundtable.cli_commands import CommandsMixin
from roundtable.cli_labels import (
    JOB_STATE,
    LENGTH_NAMES,
    MEDIA_NAMES,
    SOURCE_NAMES,
    TOOL_NAMES,
    TOOL_STATUS,
)
from roundtable.cli_report import ReportMixin
from roundtable.core.allocation import coordinator_name
from roundtable.core.config import ConfigError
from roundtable.core.display import format_rice
from roundtable.core.export import FORMATS as EXPORT_FORMATS
from roundtable.core.export.style import RATIOS as EXPORT_RATIOS
from roundtable.core.export.style import SIZES as EXPORT_SIZES
from roundtable.core.export.style import THEMES as EXPORT_THEMES
from roundtable.core.orchestrator import Orchestrator, RunResult
from roundtable.core.routing import (
    RoutingError,
    UserChoice,
)
from roundtable.core.runtime import PROJECT_ROOT, Runtime
from roundtable.core.service import ServiceError
from roundtable.core.steps import Event, restore_state
from roundtable.core.steps.office import GROUP_MODES
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
    "discuss": "分工讨论",
    "work": "完成子任务",
    "cross_review": "交叉审查",
    "rework": "按审查修改",
    "accept": "逐条验收",
    "merge": "统筹合并",
    "ideate": "成员发散",
    "expand": "互相拓展",
    "crossbreed": "碰撞杂交",
    "vote": "匿名投票",
    "organize": "统筹整合",
    "attachments": "处理附件",
    "media": "生成媒体",
    "style": "提取风格规范",
    "style_gate": "风格校验",
    "intake": "经理分诊",
    "handle": "员工办理",
    "check": "经理过目",
    "fix": "员工修改",
    "speak": "员工发言",
    "decide": "经理拍板",
    "office_review": "复核经理验收",
    "office_revise": "按复核修改",
}


class CLI(ReportMixin, CommandsMixin):
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
        self.office = False  # 当前会话是办公室模式（统筹称为经理）

    def p(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def t(self, text: str) -> str:
        """模型输出：数学式转纯文本、去掉加粗符号（--raw 时保持原样）。"""
        return to_terminal(text) if self.plain_math else text

    def label(self, code: str | None, table_no: int = 0) -> str:
        model = self.names.get((table_no, code))
        if code:
            base = f"{self.prefix}{code}"
        elif model:  # 匿名关闭 / 已揭晓：统筹显示为"昵称·模式（统筹）"，办公室模式为"（经理）"
            base = coordinator_name(model, self.rt.config, office=self.office)
        else:
            base = "经理" if self.office else "统筹"
        return f"{base}（{model}）" if model else base

    def rice(self, tokens: int) -> str:
        """token 用量显示为"大米"（金额仍是美元）。"""
        return format_rice(tokens, self.rt.config.roundtable.display.rice)

    def load_names(self, sid: str) -> None:
        """身份可以显示时（匿名关闭，或已揭晓）记下各座位对应的模型。"""
        self.names = {}
        self.office = self.rt.repo.session_row(sid)["workflow"] == "office"
        if not self.rt.repo.is_revealed(sid):
            return
        for seat in self.rt.repo.seats(sid):
            code = seat["code"] if seat["role"] == "member" else None
            self.names[(seat["table_no"], code)] = seat["model_id"]

    # --- 实时进度（事件只含代号，不含模型身份） --------------------------------------

    def office_mode(self, sid: str, table_no: int | None) -> str | None:
        """办公室模式第 1 桌经理定下的办法（含老板改成单人办）；其他情况为 None。"""
        if not self.office or table_no != 0:
            return None
        intake = restore_state(self.rt.repo, sid, 0).office.intake or {}
        return intake.get("mode")

    def step_name(self, step: str) -> str:
        """步骤的中文名；办公室模式里统筹就是经理、成员就是员工。"""
        name = STEP_NAMES.get(step, step)
        return name.replace("统筹", "经理").replace("成员", "员工") if self.office else name

    def who(self, role: str | None, code: str | None, table_no: int = 0) -> str:
        """一次调用是谁：规划员、附件预处理、复核经理（另一位经理）或桌上的成员 / 统筹。"""
        extra = {"planner": "规划员", "preprocess": "附件预处理", "reviewer": "复核经理"}
        return extra.get(role or "") or self.label(code, table_no)

    def _people(self, sid: str, members: int) -> str:
        if self.rt.repo.session_row(sid)["workflow"] == "office":
            return f"经理 + {members} 位在岗员工"
        return f"{members} 位组员 + 统筹"

    def on_event(self, sid: str, e: Event) -> None:
        d = e.data
        if e.type == "routed":
            plan = self.plan_labels.get(d["plan"], d["plan"])
            absent = f"，{d['absent']} 个模型缺席（无可用渠道）" if d.get("absent") else ""
            self.p(
                f"档位：{plan}（{self._people(sid, d['members'])}{absent}）"
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
            if e.table_no > 0 and "office_review" in (d.get("pipeline") or ()):
                team = len(d["members"])
                self.p(f"\n=== 项目组：{team} 位员工，经理拆分、合并，另一位经理验收 ===")
            elif e.table_no > 0:
                self.p(
                    f"\n=== 第 {e.table_no + 1} 张桌子：{self.plan_labels.get(d['plan'], '')} ==="
                )
            if self.rt.repo.session_row(sid)["workflow"] == "collab":
                self.p("模式：协同（拆分子任务 → 自荐 → 分工完成 → 交叉审查 → 合并）")
            members = "、".join(self.label(c, e.table_no) for c in d["members"])
            self.p(f"上桌：{members} + {self.label(None, e.table_no)}")
        elif e.type == "step_started" and e.step != "reveal":
            mode = self.office_mode(sid, e.table_no) if e.step in ("handle", "check") else None
            if mode == "meeting" and e.step == "handle":
                self.p("\n▶ 开会（参会员工依次发言，经理拍板）")
            elif mode not in GROUP_MODES:  # 开会不再过目；项目分工的办理、过目交给项目组桌子
                self.p(f"\n▶ {self.step_name(e.step)}")
        elif e.type == "call_done":
            who = self.who(d.get("role"), e.code, e.table_no)
            self.p(f"  {who} 完成（${d.get('cost', 0):.4f}）")
        elif e.type == "call_failed":
            self.p(f"  {self.who(d.get('role'), e.code, e.table_no)} 调用失败")
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
        if self.auto_confirm and card["kind"] in ("cost", "escalation", "members", "triage"):
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
    ask.add_argument(
        "--no-style-ref",
        action="store_true",
        help="图片附件只当普通附件，不作为风格参考（默认图片都是风格参考）",
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
        choices=["office", "discussion", "collab", "solo", "brainstorm", "auto"],
        default="office",
        help=(
            "office 办公室（经理分诊：单人办、开会或项目分工，经理把关；默认）"
            "/ discussion 讨论（全员各自作答再互评汇总）/ collab 协同（拆分子任务分工完成）"
            "/ solo 个人作答（档位内挑最便宜的一位直接回答）"
            "/ brainstorm 头脑风暴（抽签发散、碰撞杂交、匿名投票，整合成点子榜和思维导图）"
            "/ auto 自动（先请分诊小组给建议，你确认后再开始）"
        ),
    )
    ask.add_argument(
        "--media",
        choices=["image", "speech", "video", "music"],
        help="输出类型（仅讨论模式）：商定提示词 → 生成图片 / 语音 / 视频 / 音乐 → 评审 → 重新生成",
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

    models = sub.add_parser("models", help="查看模型、档位与可用渠道")
    models.add_argument(
        "--check-updates",
        action="store_true",
        help="查询聚合渠道的模型列表：价格变动、下架、可能更合适的新模型（只提示，不改配置）",
    )
    bench = sub.add_parser(
        "bench", help="对比实验：同一批题用个人 / 讨论 / 协同各跑一遍，比较正确率、花费、耗时"
    )
    bench.add_argument(
        "--questions", default=str(PROJECT_ROOT / "benchmarks"), help="题库文件或目录"
    )
    bench.add_argument(
        "--modes",
        default="solo:budget,discussion:budget,collab:budget",
        help="模式:档位，逗号分隔，如 solo:budget,collab:flagship；办公室模式写 office:budget；"
        "自洽投票基线写 vote:budget[:次数]（个人作答跑几次取多数，默认 5 次）",
    )
    bench.add_argument("--category", action="append", help="只跑这些类别（可重复）")
    bench.add_argument("--limit", type=int, help="只跑前 N 题")
    bench.add_argument("--max-usd", type=float, default=2.0, help="花费上限，默认 2 美元")
    bench.add_argument("--seed", type=int)
    bench.add_argument("--batch", help="实验名称，默认按时间生成")
    bench.add_argument("--list", action="store_true", help="列出历次实验")
    bench.add_argument("--report", metavar="BATCH", help="只查看某次实验的汇总")
    bench.add_argument("-o", "--output", help="导出 CSV 或 JSON（按后缀）")
    sub.add_parser("stats", help="各模型的历史贡献（被采纳、有效问题、敷衍次数等）")
    stickers = sub.add_parser("stickers", help="成员专属表情包：查看状态、画（新画的待审）")
    stickers.add_argument("member", nargs="?", help="成员（personas.yaml 里的键，如 OpenAI）")
    stickers.add_argument(
        "--draw", action="append", default=[], metavar="表情名", help="画这一张（可重复）"
    )
    stickers.add_argument("--all", action="store_true", help="画所有还没画的")
    stickers.add_argument("-o", "--output", help="把画好的图保存到这个目录")
    stickers.add_argument(
        "--whiten", action="store_true", help="把已画好的表情浅灰底色统一刷成纯白（不重画）"
    )
    stickers.add_argument(
        "--prompts", metavar="目录", help="导出提示词和参考图，拿去别处画（不花钱）"
    )
    stickers.add_argument(
        "--import",
        dest="import_dir",
        metavar="目录",
        help="把别处画好的图收进来（文件名=表情名，动图给四格图），待审",
    )
    spacks = sub.add_parser(
        "sticker-packs",
        help="外部表情包（dsh-meme 格式）：在线目录、装到本机、给某位成员专用或通用",
    )
    spacks.add_argument(
        "action", choices=["list", "catalog", "install", "assign", "remove"], help="操作"
    )
    spacks.add_argument("pack", nargs="?", help="包 id（如 whale-girl-emoji）")
    spacks.add_argument("--for", dest="owner", metavar="成员", help="给这位成员专用（如 DeepSeek）")
    spacks.add_argument("--common", action="store_true", help="改成大家通用")
    spacks.add_argument("--as", dest="label", metavar="名字", help="聊天里的套装名（1～6 字）")
    spacks.add_argument("--file", metavar="压缩包", help="装本地的 zip（不用下载）")
    perf = sub.add_parser("performance", help="办公室模式的员工绩效（件数、退回、通过率等）")
    perf.add_argument("--reset", action="store_true", help="清零：之后只统计从现在起的会话")
    evolve = sub.add_parser("evolve", help="革新例会：成员读资料卡提建议、投票，导出采纳单")
    evolve.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "scout", "meet", "export", "plugins"],
    )
    evolve.add_argument("session_id", nargs="?", help="export / plugins 时：例会的会话 id")
    kind = evolve.add_mutually_exclusive_group()
    kind.add_argument(
        "--major",
        dest="kind",
        action="store_const",
        const="major",
        help="meet 时手动改成大版本（列前 10 个选项；不加 = 按时间自动判断）",
    )
    kind.add_argument(
        "--minor", dest="kind", action="store_const", const="minor", help="meet 时手动改成小版本"
    )
    evolve.add_argument("--theme", help="大版本的主题，例如 '群聊更自然'（不给就自动轮换）")
    history = sub.add_parser("history", help="最近的讨论")
    history.add_argument("--limit", type=int, default=20)
    note = sub.add_parser("note", help="讨论进行中补充意见（下一个步骤开始时带上）")
    note.add_argument("session_id")
    note.add_argument("text", help="补充的内容")
    for name, text in (("resume", "中断后继续"), ("show", "查看结果"), ("reveal", "揭晓身份")):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("session_id")
        if name == "show":
            cmd.add_argument("--details", action="store_true")
            cmd.add_argument("--raw", action="store_true")
            cmd.add_argument("--costs", action="store_true", help="显示每步与每次调用的花费")
    sim = sub.add_parser(
        "sim",
        help="成员系统加速时间模拟：临时数据库里快进几天到几个月，出报告（默认假模型，不花钱）",
    )
    sim.add_argument("--days", type=int, default=30, help="模拟多少天（默认 30）")
    sim.add_argument("--out", help="输出目录（默认 sim_runs/<时间>；不能在 data/ 下）")
    sim.add_argument("--seed", type=int, default=0)
    sim.add_argument("--start", help="从哪天开始（YYYY-MM-DD，默认 2026-11-02 星期一）")
    sim.add_argument("--tz", default="Asia/Shanghai", help="模拟用的主时区")
    sim.add_argument("--user-per-day", type=float, default=6.0, help="模拟用户每天大约发几条")
    sim.add_argument("--cards", help="人物卡 Markdown（导入成员自我档案；不给就没有档案）")
    sim.add_argument("--real", action="store_true", help="用 .env 里的真实 key（会花钱）")
    sim.add_argument("--max-usd", type=float, default=0.0, help="真模型的花费硬上限（必填）")
    sim.add_argument(
        "--resume", action="store_true", help="接着上次断开的地方跑（要给同一个 --out）"
    )
    sim.add_argument(
        "--scenario",
        help="开跑前摆好的场景（sim.yaml 的 scenarios，如 birthday：认识一个月、你过生日）",
    )
    longrun = sub.add_parser(
        "sim-growth",
        help="半年快进：只推进成长相关的部件，几分钟跑完 90～180 天，看会不会越长越像、走极端、"
        "被你带偏、口癖用光（假模型，不花钱）",
    )
    longrun.add_argument("--days", type=int, default=180, help="快进多少天（默认 180）")
    longrun.add_argument("--out", help="输出目录（默认 sim_runs/growth-<时间>；不能在 data/ 下）")
    longrun.add_argument("--seed", type=int, default=0)
    longrun.add_argument(
        "--pressure",
        default="push",
        choices=["push", "none"],
        help="push：假模型每周都想把她们往同一个方向改（默认）；none：随手改改",
    )
    longrun.add_argument("--cards", help="人物卡 Markdown（不给就用编的 8 份起始档案）")
    backup = sub.add_parser(
        "backup", help="整库备份：看列表、马上备份、存档点、恢复（docs/BACKUP.md，不花钱）"
    )
    backup.add_argument(
        "action", nargs="?", default="list", choices=["list", "now", "checkpoint", "restore"]
    )
    backup.add_argument(
        "target", nargs="?", default="", help="checkpoint 的标签 / restore 的文件名"
    )
    backup.add_argument("--yes", action="store_true", help="恢复时不再问一遍")
    doctor = sub.add_parser(
        "doctor",
        help="出问题点这里：只读自检（起不来 / 卡住 / 撤销更新 / 数据不见了），不改任何东西",
    )
    doctor.add_argument(
        "--report", action="store_true", help="输出可以整段转交的体检报告（不含聊天内容和 key）"
    )
    doctor.add_argument("-o", "--output", help="把体检报告存到这个文件（UTF-8）")
    gptlogin = sub.add_parser(
        "chatgpt-login",
        help="朋友圈配图用 ChatGPT 网页：弹出浏览器，你自己登录一次（圆桌不经手账号密码）",
    )
    gptlogin.add_argument(
        "--wait", type=float, default=600.0, help="最多等你登录多少秒（默认 600）"
    )
    gptlogin.add_argument(
        "--off", action="store_true", help="关掉 ChatGPT 网页出图（不删登录状态）"
    )
    kit = sub.add_parser(
        "kit-check", help="工作台质检：三种屏宽 × 浅色 / 深色，量出不达标的地方（不调用模型）"
    )
    kit.add_argument("target", help="本地 .html 文件或 http(s) 地址")
    kit.add_argument("-o", "--output", help="截图和 report.json 存到这个目录")
    kit.add_argument("--json", action="store_true", help="只输出 JSON 报告")
    estimate = sub.add_parser("estimate", help="提交前预估：各模式 × 各档位的上桌人数与预计花费")
    estimate.add_argument("question", nargs="?", help="题目；省略时从 --file 读取")
    estimate.add_argument("--file", help="从 UTF-8 文本文件读取题目")
    estimate.add_argument("--tier", help="标出要用的档位")
    estimate.add_argument("--models", help="自选：逗号分隔的模型 id")
    estimate.add_argument("--coordinator", help="自选时指定统筹")
    estimate.add_argument("--anonymous", action="store_true", help="不显示上桌的模型名")
    estimate.add_argument("--media", choices=["image", "speech", "video", "music"], help="输出类型")
    estimate.add_argument("--media-tier", choices=["budget", "flagship"], help="媒体模型的档位")
    estimate.add_argument("--seed", type=int, help="随机种子（与 ask --seed 相同时阵容一致）")
    files = sub.add_parser("files", help="把成员生成的文件保存到本地目录")
    files.add_argument("session_id")
    files.add_argument("-o", "--output", help="目录，默认 roundtable-<会话 id 前 8 位>-files")
    mindmap = sub.add_parser("mindmap", help="保存头脑风暴的思维导图（.svg 图 / .md 大纲 / .mmd）")
    mindmap.add_argument("session_id")
    mindmap.add_argument(
        "-o", "--output", help="输出文件，默认 roundtable-<会话 id 前 8 位>-mindmap.svg"
    )
    export = sub.add_parser("export", help="导出完整记录到 UTF-8 文本文件（含花费明细）")
    export.add_argument("session_id")
    export.add_argument("-o", "--output", help="输出文件，默认 roundtable-<会话 id 前 8 位>.txt")
    export.add_argument("--raw", action="store_true", help="保持模型输出的原样")
    export.add_argument(
        "--format",
        choices=["txt", *EXPORT_FORMATS],
        default="txt",
        help="txt 为完整记录（默认）；md / docx / pptx / pdf / html 只导出最终成果"
        "（本地转换，不调用模型）",
    )
    export.add_argument("--size", choices=list(EXPORT_SIZES), help="字号：small / medium / large")
    export.add_argument("--theme", choices=list(EXPORT_THEMES), help="配色模板")
    export.add_argument("--ratio", choices=list(EXPORT_RATIOS), help="PPT 页面比例")
    export.add_argument("--no-question", action="store_true", help="开头不附题目")
    export.add_argument("--process", action="store_true", help="文末附上过程（各人的初稿与修订）")
    return parser


async def run(
    argv: list[str] | None = None,
    *,
    runtime: Runtime | None = None,
    out: TextIO = sys.stdout,
    ask: Callable[[str], str] = input,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "kit-check":  # 不需要配置和模型
        return await asyncio.to_thread(cmd_kit_check, args, out)
    if args.command == "doctor":  # 圆桌起不来时也要能用：不建运行时、不迁移数据库
        return cmd_doctor(args, out)
    if args.command == "chatgpt-login":  # 只开浏览器、改本机开关，不建运行时
        return await cmd_chatgpt_login(args, out)
    if args.command == "sim":  # 自己建临时数据库，不打开真实数据
        return await cmd_sim(args, out)
    if args.command == "sim-growth":  # 同上
        return await cmd_sim_growth(args, out)
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
        if args.command == "backup":
            return cmd_backup(rt, args, out, ask)
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
                style_ref=not args.no_style_ref,
            )
        if args.command == "bench":
            return await cli.cmd_bench(args)
        if args.command == "models":
            if args.check_updates:
                return await cli.cmd_model_updates()
            return cli.cmd_models()
        if args.command == "stats":
            return cli.cmd_stats()
        if args.command == "stickers":
            return await cli.cmd_stickers(
                args.member,
                args.draw,
                args.all,
                args.output,
                args.whiten,
                args.prompts,
                args.import_dir,
            )
        if args.command == "sticker-packs":
            owner = "" if args.common else args.owner
            return cli.cmd_sticker_packs(args.action, args.pack, owner, args.label, args.file)
        if args.command == "performance":
            return cli.cmd_performance(args.reset)
        if args.command == "evolve":
            return await cli.cmd_evolve(args.action, args.session_id, args.kind, args.theme)
        if args.command == "note":
            return cli.cmd_note(args.session_id, args.text)
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
        if args.command == "mindmap":
            return cli.cmd_mindmap(args.session_id, args.output)
        if args.command == "export":
            if args.format != "txt":
                try:
                    options = {
                        "size": args.size,
                        "theme": args.theme,
                        "ratio": args.ratio,
                        "question": not args.no_question,
                        "process": args.process,
                    }
                    target = cli.export_document(args.session_id, args.format, args.output, options)
                except ServiceError as exc:
                    print(str(exc), file=out)
                    return 1
                print(f"已导出到 {target.resolve()}", file=out)
                return 0
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


async def cmd_sim(args: argparse.Namespace, out: TextIO) -> int:
    from datetime import datetime as _dt

    from roundtable.core.simlab import SimError, Simulation

    if args.resume and not args.out:
        print("接着跑要用 --out 指定上次的输出目录", file=out)
        return 2
    folder = args.out or f"sim_runs/{_dt.now():%Y%m%d-%H%M%S}"
    start = _dt.fromisoformat(args.start).replace(hour=7) if args.start else None
    try:
        sim = Simulation(
            Path(folder),
            days=args.days,
            seed=args.seed,
            start=start,
            tz=args.tz,
            real=args.real,
            max_usd=args.max_usd,
            user_per_day=args.user_per_day,
            cards=Path(args.cards).read_text(encoding="utf-8") if args.cards else None,
            resume=args.resume,
            scenario=args.scenario,
        )
        print(f"模拟 {args.days} 天（{'真模型' if args.real else '假模型'}）……", file=out)
        res = await sim.run()
    except (SimError, ConfigError, OSError) as exc:
        print(f"模拟没跑成：{exc}", file=out)
        return 2
    m = res.meta
    passed = sum(c["ok"] for c in res.checks)
    print(
        f"完成：{m['days']} 天，用时 {m['wall_seconds']} 秒，花费 ${m['spent_usd']}"
        f"（每天约 ${m['usd_per_day']}），检查 {passed}/{len(res.checks)} 通过",
        file=out,
    )
    if m.get("stopped"):
        print(f"提前停了：{m['stopped']}（修好后可用 --resume 接着跑）", file=out)
    exams = [e for e in res.exams if e["ok"] is not None]
    if exams:
        print(f"考题 {sum(e['ok'] for e in exams)}/{len(exams)} 通过", file=out)
    if res.problems:
        print(f"问题清单 {len(res.problems)} 条（要紧的在前）", file=out)
    print(f"报告：{sim.out / 'report.md'}", file=out)
    return 0 if res.ok else 1


async def cmd_sim_growth(args: argparse.Namespace, out: TextIO) -> int:
    """半年快进：全部指标正常返回 0，有超标或出错返回 1，没跑成返回 2。"""
    from datetime import datetime as _dt

    from roundtable.core.simlab import SimError
    from roundtable.core.simlong import LongRun

    folder = args.out or f"sim_runs/growth-{_dt.now():%Y%m%d-%H%M%S}"
    try:
        run = LongRun(
            Path(folder),
            days=args.days,
            seed=args.seed,
            pressure=args.pressure,
            cards=Path(args.cards).read_text(encoding="utf-8") if args.cards else None,
        )
        print(
            f"快进 {args.days} 天（假模型，{'施压' if args.pressure == 'push' else '不施压'}）……",
            file=out,
        )
        res = await run.run()
    except (SimError, ConfigError, OSError) as exc:
        print(f"快进没跑成：{exc}", file=out)
        return 2
    print(f"完成：{res.days} 天，用时 {res.seconds:.0f} 秒", file=out)
    for p in res.problems:
        print(f"  超标：{p}", file=out)
    if res.errors:
        print(f"  出错 {len(res.errors)} 次（见报告）", file=out)
    if res.ok:
        print("全部指标正常", file=out)
    print(f"报告：{res.out / 'report.md'}", file=out)
    return 0 if res.ok else 1


def cmd_kit_check(args: argparse.Namespace, out: TextIO) -> int:
    """工作台质检：通过返回 0，不通过返回 1，没法检查返回 2。"""
    from roundtable.core.benchkit.quality import QualityError, run_check

    try:
        report = run_check(args.target, args.output)
    except QualityError as exc:
        print(f"没法质检：{exc}", file=out)
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=out)
    else:
        print(report.summary(), file=out)
        if args.output:
            print(f"截图和报告在：{args.output}", file=out)
    return 0 if report.passed else 1


def _utf8_streams() -> None:
    """输出统一用 UTF-8（Windows 终端、PowerShell 管道重定向时也是）。

    errors="replace" 保证遇到无法编码的字符也不会中途报错、留下空文件。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def cmd_backup(
    rt: Runtime, args: argparse.Namespace, out: TextIO, ask: Callable[[str], str]
) -> int:
    """整库备份（docs/BACKUP.md）。恢复前先关掉网页服务，恢复后再打开。"""
    from roundtable.core.backup import BackupError, DbBackup

    rules = rt.config.roundtable.backup
    box = DbBackup(
        rt.repo.conn,
        rt.backup_dir,
        enabled=True,  # 命令行里手动「now」不看自动备份开关
        keep_daily=rules.keep_daily,
        keep_checkpoints=rules.keep_checkpoints,
    )
    now = datetime.now()
    try:
        if args.action == "now":
            got = box.daily(now) or box.checkpoint("手动", now)
            print(f"已备份：{got.name}", file=out)
            return 0
        if args.action == "checkpoint":
            got = box.checkpoint(args.target or "手动", now)
            print(f"已存档点：{got.name}", file=out)
            return 0
        if args.action == "restore":
            if not args.target:
                print("要恢复哪一份？先用 roundtable backup 看列表，再写文件名。", file=out)
                return 2
            f = box.check(args.target)
            if not args.yes:
                print("恢复会用这份备份覆盖现在的全部数据。", file=out)
                print("（会先自动存一个「恢复前」存档点，反悔可以再恢复它。）", file=out)
                print("请先关掉圆桌网页服务。", file=out)
                if ask(f"确定恢复 {f.name}？输入 yes 继续：").strip().lower() != "yes":
                    print("没有恢复。", file=out)
                    return 1
            got = box.restore(f.name, now)
            print(f"已恢复：{got['restored']['name']}", file=out)
            print(f"恢复前的数据存在：{got['before']['name']}（反悔可以再恢复它）", file=out)
            print("请重新打开圆桌。", file=out)
            return 0
    except BackupError as exc:
        print(f"备份出错：{exc}", file=out)
        return 1
    files = box.files()
    print(f"备份目录：{box.folder or '（内存数据库，不备份）'}", file=out)
    if not files:
        print("还没有备份。", file=out)
    for f in files:
        kind = "每天" if f.kind == "daily" else f"存档点·{f.label}"
        print(f"  {f.name}  {kind}  {f.size / 1048576:.1f} MB", file=out)
    return 0


async def cmd_chatgpt_login(
    args: argparse.Namespace,
    out: TextIO,
    *,
    login: Callable[..., Awaitable[bool]] | None = None,
    config_dir: Path | None = None,
) -> int:
    """ChatGPT 网页出图的一次性登录（docs/CHATGPT_WEB.md）：你自己在弹出的浏览器里登录，
    成功后在 config/models.local.yaml 打开 chatgpt-web。"""
    from roundtable.core.config import load_config
    from roundtable.core.config.loader import DEFAULT_CONFIG_DIR
    from roundtable.core.providers import chatgpt_web

    folder = config_dir or DEFAULT_CONFIG_DIR
    local = folder / "models.local.yaml"
    if args.off:
        chatgpt_web.switch_on(local, value=False)
        print("已关掉 ChatGPT 网页出图（朋友圈配图改用 GPT Image 2.5）。重启圆桌后生效。", file=out)
        return 0
    try:
        cfg = load_config(folder)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=out)
        return 2
    model = next((m for m in cfg.models.models if m.id == chatgpt_web.MODEL_ID), None)
    channel = cfg.models.channels.get("chatgpt_web")
    if model is None or channel is None:
        print("配置里没有 chatgpt-web 模型或 chatgpt_web 渠道。", file=out)
        return 2
    print("马上弹出一个浏览器窗口：请你自己登录 ChatGPT（圆桌不经手账号密码）。", file=out)
    print(f"登录好、看到对话输入框后窗口会自动关闭；最多等 {args.wait:g} 秒。", file=out)
    try:
        do_login = login or chatgpt_web.login
        ok = await do_login(channel.base_url, dict(model.params), wait_s=args.wait)
    except ImportError:
        print("没装 playwright：先运行 pip install playwright", file=out)
        print("再运行 playwright install chromium，然后再试一次。", file=out)
        return 1
    if not ok:
        print("没等到登录成功。可以再运行一次 roundtable chatgpt-login。", file=out)
        return 1
    chatgpt_web.switch_on(local)
    print("登录成功，已打开 ChatGPT 网页出图：电脑开着时朋友圈配图先用网页画，", file=out)
    print("画不了（没登录、次数用完、网页改版）时自动改用 GPT Image 2.5。", file=out)
    print("重启圆桌后生效。", file=out)
    return 0


def cmd_doctor(args: argparse.Namespace, out: TextIO) -> int:
    """离线自检（docs/FIRSTAID.md）：服务开着时网页上「出问题点这里」看到的更全。"""
    from roundtable.core import firstaid
    from roundtable.core.changelog import load_changelog, numbered
    from roundtable.core.config import load_config
    from roundtable.core.providers import KeyRing
    from roundtable.core.runtime import BACKUP_DIR, data_dir, db_file

    error = None
    names: list[str] = []
    enabled = True
    try:
        cfg = load_config()
        names = [c.key_env for c in cfg.models.channels.values() if c.key_env]
        names += [p.key_env for p in cfg.models.search_providers.values() if p.key_env]
        enabled = cfg.roundtable.backup.enabled
    except ConfigError as exc:
        error = str(exc)
    keys = KeyRing.from_env(names, dotenv_path=PROJECT_ROOT / ".env")
    db = db_file()
    folder = db.parent / BACKUP_DIR if db is not None else None
    now = datetime.now().astimezone()
    found = firstaid.offline_findings(
        db_path=db,
        data=db.parent if db is not None else data_dir(),
        config_error=error,
        keys_present=[n for n in names if keys.has(n)],
        keys_missing=[n for n in names if not keys.has(n)],
        backup_files=firstaid.list_backups(folder),
        backup_folder=folder,
        backup_enabled=enabled,
        now=now.replace(tzinfo=None),
    )
    summary = firstaid.summarize(found)
    try:
        rows = numbered(load_changelog())
        version = rows[-1][0] if rows else None
    except (ConfigError, OSError, ValueError):
        version = None
    report = firstaid.render_report(summary, firstaid.meta_info(version, now))
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
        print(f"体检报告已存到 {args.output}", file=out)
    if args.report:
        print(report, file=out)
    elif not args.output:
        mark = {"ok": "✓", "info": "·", "warn": "!", "bad": "✗"}
        print(f"圆桌自检：{summary['headline']}", file=out)
        for f in summary["findings"]:
            line = f"  {mark[f['level']]} {f['title']}"
            if f["detail"]:
                line += f"：{f['detail']}"
            print(line, file=out)
            if f["fix"] and f["level"] in ("warn", "bad"):
                print(f"      怎么办：{f['fix']}", file=out)
        print("服务开着时，网页顶栏「出问题点这里」能看到更多（后台任务、渠道、预算）。", file=out)
        print("要发给我们：roundtable doctor --report", file=out)
    return 1 if summary["level"] == "bad" else 0


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    try:
        return asyncio.run(run(argv))
    except KeyboardInterrupt:
        print("\n已中断。可以用 roundtable history 找到会话，再用 roundtable resume <id> 继续。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
