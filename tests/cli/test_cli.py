"""命令行：用 Fake 模型跑完整流程；匿名讨论揭晓前输出中不得出现任何模型 / 厂商 / 渠道名。"""

from __future__ import annotations

import io
import re

import pytest

from roundtable.cli import run
from roundtable.core.config import load_config
from roundtable.core.runtime import Runtime

from ..core.orchestrator.conftest import MEDIUM, SHORT, Env

TAROT = set(load_config().personas.codes)


class Answers:
    """依次返回预设的输入，并记录提示语。"""

    def __init__(self, *values: str):
        self.values = list(values)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.values.pop(0) if self.values else ""


async def cli(env: Env, *argv: str, answers: Answers | None = None) -> tuple[int, str]:
    """命令行默认是办公室模式；这些测试针对讨论模式，没写 --mode 时按讨论模式运行。"""
    args = list(argv)
    if args and args[0] == "ask" and "--mode" not in args:
        args[1:1] = ["--mode", "discussion"]
    out = io.StringIO()
    code = await run(args, runtime=env.rt, out=out, ask=answers or Answers())
    return code, out.getvalue()


def identity_terms(env: Env) -> list[str]:
    terms = set(env.config.models.channels)
    for m in env.config.models.models:
        terms |= {m.id, m.vendor, *m.aliases}
    return sorted(terms)


def mentions(text: str, term: str) -> bool:
    """按整词匹配（合成模型 id 很短，可能出现在随机的会话 id 里）。"""
    import re

    return re.search(rf"(?<![0-9A-Za-z]){re.escape(term)}(?![0-9A-Za-z])", text) is not None


def before_reveal(text: str) -> str:
    return text.split("揭晓身份")[0]


async def test_ask_full_flow_anonymous_until_reveal():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(
        env, "ask", MEDIUM, "--anonymous", "--details", "--seed", "3", answers=Answers("n")
    )
    assert code == 0
    for term in identity_terms(env):
        assert not mentions(text, term), term
    assert (
        any(c in text for c in TAROT) and "【最终答案】" in text and "最大值 2，最小值 -2" in text
    )
    assert "【共识】" in text and "【分歧】" in text
    assert "稍后可用 roundtable reveal" in text


async def test_reveal_after_run_shows_models_and_channels():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(env, "ask", SHORT, "--anonymous", "--reveal")
    assert code == 0
    revealed = text.split("揭晓身份")[1]
    assert "渠道：c" in revealed and "规划员" not in revealed  # 简单题不调用规划员
    assert any(mentions(revealed, m.id) for m in env.config.models.models)
    assert not any(mentions(before_reveal(text), t) for t in identity_terms(env))


async def test_not_anonymous_by_default_shows_models_throughout():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(env, "ask", SHORT, "--details", "--seed", "3")
    assert code == 0
    progress = text.split("═")[0]  # 实时进度部分
    assert any(mentions(progress, m) for m in ("b1", "b2", "b3"))  # 上桌时就显示模型
    assert "·节电（" in text and "（统筹）（" in text  # 昵称·模式（统筹）（模型）
    assert "上桌的模型与每次调用" in text and "渠道：c" in text
    assert "按回车揭晓身份" not in text and "roundtable reveal" not in text
    code, text = await cli(env, "reveal", env.rt.repo.list_sessions()[0].id)
    assert code == 0 and "没有开启匿名" in text


async def test_checkpoint_enter_takes_recommendation():
    env = Env(confirm_threshold_usd=0.0001)
    answers = Answers("", "n")  # 回车 = 推荐（继续），然后不揭晓
    code, text = await cli(env, "ask", SHORT, "--tier", "flagship", answers=answers)
    assert code == 0 and "需要你确认" in text and "← 推荐" in text
    assert "推荐项 1" in answers.prompts[0]


async def test_checkpoint_number_and_invalid_input():
    env = Env(confirm_threshold_usd=0.0001)
    # 卡片选项：1 继续、2 改用节电模式、3 停止
    answers = Answers("9", "abc", "3")
    code, text = await cli(env, "ask", SHORT, "--tier", "flagship", answers=answers)
    assert code == 1 and text.count("无效的输入") == 2
    assert "状态：stopped" in text


async def test_yes_flag_auto_continues():
    env = Env(confirm_threshold_usd=0.0001)
    code, text = await cli(env, "ask", SHORT, "--tier", "flagship", "--yes", "--no-reveal")
    assert code == 0 and "自动选择继续" in text


async def test_custom_models_flag():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(
        env, "ask", SHORT, "--models", "b1,b2,f1", "--coordinator", "f1", "--no-reveal"
    )
    assert code == 0 and "自选（2 位组员 + 统筹）" in text
    assert "（统筹）（f1）" in text


async def test_flagship_tier_flag():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(env, "ask", SHORT, "--tier", "flagship", "--anonymous", "--no-reveal")
    assert code == 0 and "全力模式（4 位组员 + 统筹）" in text


async def test_unknown_model_or_tier_is_reported():
    env = Env()
    code, text = await cli(env, "ask", SHORT, "--models", "b1,b2,ghost", "--no-reveal")
    assert code == 1 and "无法开始" in text and "ghost" in text
    code, text = await cli(env, "ask", SHORT, "--tier", "giant", "--no-reveal")
    assert code == 1 and "未知的档位" in text
    code, text = await cli(env, "ask", SHORT, "--coordinator", "f1", "--no-reveal")
    assert code == 2 and "--models" in text


async def test_question_from_prompt_and_file(tmp_path):
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(env, "ask", "--no-reveal", answers=Answers(SHORT))
    assert code == 0 and f"题目：{SHORT}" in text
    f = tmp_path / "q.txt"
    f.write_text(SHORT + "\n", encoding="utf-8")
    code, text = await cli(env, "ask", "--file", str(f), "--no-reveal")
    assert code == 0
    code, text = await cli(env, "ask", "--no-reveal", answers=Answers("   "))
    assert code == 2 and "不能为空" in text


async def test_no_models_available(tmp_path):
    rt = Runtime.build(
        providers={}, unavailable={"openrouter": "缺少 key"}, db_path=tmp_path / "x.db"
    )
    out = io.StringIO()
    code = await run(["ask", SHORT], runtime=rt, out=out, ask=Answers())
    assert code == 2 and ".env" in out.getvalue() and "openrouter：缺少 key" in out.getvalue()


async def test_models_history_show_reveal_commands():
    env = Env(confirm_threshold_usd=100.0)
    await cli(env, "ask", SHORT, "--anonymous", "--no-reveal")
    code, text = await cli(env, "models")
    assert code == 0 and "✓ b1" in text and "本月已用" in text
    code, text = await cli(env, "history")
    sid = text.split()[1]
    assert code == 0 and "completed" in text
    code, text = await cli(env, "show", sid)
    assert code == 0 and not any(mentions(text, t) for t in identity_terms(env))
    code, text = await cli(env, "reveal", sid)
    assert code == 0 and re.search(r"组员\S+ = b\d", text)


async def test_show_contributions_and_stats():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(env, "ask", MEDIUM, "--anonymous", "--no-reveal", "--seed", "4")
    assert code == 0 and "【贡献】" in text and "被采纳的要点" in text
    assert not any(mentions(text, t) for t in identity_terms(env))
    code, text = await cli(env, "stats")
    assert code == 0 and "揭晓后才计入" in text
    await cli(env, "reveal", env.rt.repo.list_sessions()[0].id)
    code, text = await cli(env, "stats")
    assert code == 0 and "1 场" in text and "被采纳的要点" in text


async def test_effort_events_printed():
    env = Env(confirm_threshold_usd=100.0)
    original = env.reply

    def lazy(model, messages):
        if "独立完成同一道题" in messages[0].content and len(messages) == 2:
            return "略"  # 第一次作答敷衍，重做时（对话变长）正常回答
        return original(model, messages)

    env.fake._default = lazy
    code, text = await cli(env, "ask", MEDIUM, "--seed", "4")
    assert code == 0 and "打回重做：只有空话" in text


async def test_collab_mode():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(env, "ask", MEDIUM, "--mode", "collab", "--details", "--seed", "5")
    assert code == 0
    for marker in (
        "统筹拆分子任务",
        "成员自荐",
        "交叉审查",
        "【子任务与分工】",
        "【完整成果】",
        "【采纳情况】",
    ):
        assert marker in text, marker
    assert "全部采用" in text and "自荐被采纳" in text


async def test_resume_command():
    env = Env(confirm_threshold_usd=100.0)
    original = env.reply
    crashed = {"done": False}

    def flaky(model, messages):
        if "根据审阅意见修订" in messages[0].content and not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("断网")
        return original(model, messages)

    env.fake._default = flaky
    with pytest.raises(RuntimeError):
        await cli(env, "ask", MEDIUM, "--no-reveal")
    sid = env.rt.repo.list_sessions()[0].id
    code, text = await cli(env, "resume", sid, answers=Answers("n"))
    assert code == 0 and "【最终答案】" in text


def test_main_handles_parse_errors():
    from roundtable.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["ask", "--preset", "saver", "q"])  # 旧参数已移除
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ask", "--tier", "budget", "--models", "a", "q"])


async def test_latex_shown_as_plain_text_unless_raw():
    env = Env(confirm_threshold_usd=100.0)
    env.fake.queue("b1", r"**答案**是 \(\frac{1}{2}\)，即 $\boxed{0.5}$。")
    code, text = await cli(
        env, "ask", SHORT, "--models", "b1,b2,f1", "--coordinator", "f1", "--details"
    )
    assert code == 0 and "答案是 1/2，即 0.5。" in text and "\\frac" not in text
    assert "**" not in text
    sid = env.rt.repo.list_sessions()[0].id
    code, raw = await cli(env, "show", sid, "--raw", "--details")
    assert "\\frac{1}{2}" in raw and "**答案**" in raw


async def test_show_costs_and_export_utf8(tmp_path):
    env = Env(confirm_threshold_usd=100.0)
    await cli(env, "ask", MEDIUM, "--anonymous", "--seed", "3", answers=Answers("n"))
    sid = env.rt.repo.list_sessions()[0].id
    code, text = await cli(env, "show", sid, "--costs")
    assert code == 0 and "【花费明细】" in text and "【每次调用】" in text
    assert "规划员" in text and "第 1 桌：预估" in text and "独立作答：预估" in text
    for term in identity_terms(env):  # 匿名未揭晓：花费明细中也不显示模型与渠道
        assert not mentions(text.split("【花费明细】")[1], term), term

    target = tmp_path / "out.txt"
    code, text = await cli(env, "export", sid, "-o", str(target))
    assert code == 0 and "已导出到" in text
    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM：Windows 记事本 / PowerShell 能识别
    content = raw.decode("utf-8-sig")
    assert "【最终答案】" in content and "【花费明细】" in content
    assert re.search(r"组员\S+ 的答案", content)  # 导出包含每位成员的产出


def test_main_writes_utf8_even_when_stream_is_not(monkeypatch, capsys):
    import sys

    from roundtable import cli as cli_module

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="gbk")
    monkeypatch.setattr(sys, "stdout", stream)
    cli_module._utf8_streams()
    print("组员甲 ✓ x² 🙂", file=sys.stdout)
    sys.stdout.flush()
    assert raw.getvalue().decode("utf-8") == "组员甲 ✓ x² 🙂\n"


async def test_ask_with_attachments(tmp_path):
    from ..core.attachments.samples import PNG

    notes = tmp_path / "讲义.txt"
    notes.write_text("讲义：比较极值点和端点处的函数值。", encoding="gbk")
    image = tmp_path / "graph.png"
    image.write_bytes(PNG)
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(
        env, "ask", MEDIUM, "--attach", str(notes), "--attach", str(image), "--details"
    )
    assert code == 0
    assert "附件：讲义.txt（文本）" in text and "附件：graph.png（图片" in text
    assert "已生成文字版" in text and "graph.png 的文字版" in text

    code, text = await cli(env, "ask", MEDIUM, "--attach", str(tmp_path / "missing.pdf"))
    assert code == 2 and "无法读取附件" in text
    bad = tmp_path / "fake.png"
    bad.write_bytes(b"text")
    code, text = await cli(env, "ask", MEDIUM, "--attach", str(bad))
    assert code == 2 and "附件不可用" in text


async def test_tool_calls_and_files_command(tmp_path):
    from ..core.orchestrator.test_tools import scripted, with_tools

    env = Env(confirm_threshold_usd=100.0)
    with_tools(env)
    scripted(
        env, "独立完成同一道题", '<tool_call name="write_file" path="解答.md"># 解答</tool_call>'
    )
    code, text = await cli(env, "ask", MEDIUM, "--details", "--anonymous", "--no-reveal")
    assert code == 0
    assert "写文件：成功" in text and "【工具调用】" in text and "【生成的文件】" in text
    sid = env.rt.repo.list_sessions()[0].id
    code, text = await cli(env, "files", sid, "-o", str(tmp_path / "out"))
    assert code == 0 and "已保存到" in text
    saved = sorted(
        p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*.md")
    )
    assert len(saved) == 2 and all(p.startswith("组员") and p.endswith("/解答.md") for p in saved)
    for term in identity_terms(env):  # 匿名未揭晓：目录名只用代号
        assert not any(mentions(p, term) for p in saved)


async def test_estimate_command_and_ask_preview():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(env, "estimate", MEDIUM, "--seed", "5")
    assert code == 0 and env.fake.calls == []
    assert "提交前预估" in text and "▶ 讨论 · 节电模式" in text and "协同 · 全力模式" in text
    assert "上桌：" in text and "随机种子：5" in text
    code, text = await cli(env, "estimate", MEDIUM, "--anonymous")
    assert "上桌：" not in text
    for term in identity_terms(env):
        assert not mentions(text, term), term
    code, text = await cli(env, "ask", MEDIUM, "--mode", "collab", "--seed", "5")
    assert code == 0 and "提交前预估" in text and "▶ 协同 · 节电模式" in text
    assert "讨论 · " not in text.split("题目：")[1].split("档位：")[0]  # ask 只列所选模式


async def test_ask_media_image_shows_generation_and_costs():
    env = Env(confirm_threshold_usd=100.0, with_media=True)
    code, text = await cli(
        env,
        "ask",
        MEDIUM,
        "--models",
        "b1,b2,b3,f1",
        "--coordinator",
        "f1",
        "--media",
        "image",
        "--seed",
        "3",
        "--details",
    )
    assert code == 0
    assert "▶ 生成媒体" in text and "生成图片（第 1 轮）" in text
    assert "【媒体生成】" in text and "图片 · 生成媒体 · 第 1 轮" in text and "提示词" in text
    assert "【生成的文件】" in text and "image_r1.png" in text
    assert "提交前预估" in text


async def test_ask_video_always_asks_even_with_yes():
    env = Env(confirm_threshold_usd=100.0, with_media=True)
    answers = Answers("2")  # 选"不生成"
    code, text = await cli(
        env,
        "ask",
        MEDIUM,
        "--models",
        "b1,b2,b3,f1",
        "--coordinator",
        "f1",
        "--media",
        "video",
        "--yes",
        "--seed",
        "3",
        answers=answers,
    )
    assert code == 0 and answers.prompts  # --yes 不会自动同意视频生成
    assert "每次生成前都会先问你" in text and "【媒体生成】" not in text


async def test_estimate_command_with_media():
    env = Env(with_media=True)
    _, plain = await cli(env, "estimate", MEDIUM, "--seed", "1")
    _, media = await cli(
        env, "estimate", MEDIUM, "--media", "video", "--media-tier", "flagship", "--seed", "1"
    )
    assert "生成媒体 $" not in plain and "生成媒体 $" in media


async def test_media_flag_rejected_in_collab():
    env = Env(with_media=True)
    code, text = await cli(env, "ask", MEDIUM, "--mode", "collab", "--media", "image")
    assert code == 2 and "协同模式" in text


async def test_collab_pipeline_shows_kinds_and_handoff_chain():
    """流水线：子任务带类型，show 里有交接链；token 用量显示为大米。"""
    from ..core.orchestrator.test_media import script
    from ..core.orchestrator.test_pipeline import WHOLE

    env = Env(confirm_threshold_usd=100.0, pipeline=True)
    script(
        env,
        **{"把任务拆成子任务": lambda m, msgs: WHOLE, "没有通过代码检查": lambda m, msgs: WHOLE},
    )
    code, text = await cli(env, "ask", MEDIUM, "--mode", "collab", "--details", "--seed", "5")
    assert code == 0
    assert "【子任务与分工】" in text and "[分析]" in text and "[整合]" in text
    assert "已按模板「文档」生成" in text
    assert "【交接链】" in text and "把〈提纲与要点〉交给" in text
    sid = env.rt.repo.list_sessions(1)[0].id
    code, costs = await cli(env, "show", sid, "--costs")
    assert (
        code == 0 and "token" not in costs and re.search(r"输入 [\d.]+ 粒 / 输出 [\d.]+ 粒", costs)
    )
    assert "$" in costs  # 金额仍是美元


async def test_style_reference_flow_in_the_cli(tmp_path):
    """图片附件默认是风格参考：show 里有风格规范、媒体任务显示参考图张数；--no-style-ref 则没有。"""
    from ..core.attachments.samples import PNG
    from ..core.orchestrator.test_media import make_env
    from ..core.orchestrator.test_style import with_style_replies

    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG)
    argv = ("ask", MEDIUM, "--models", "b1,b2,f1,b3", "--coordinator", "b3", "--media", "image")
    argv += ("--media-tier", "flagship", "--attach", str(ref), "--details", "--seed", "5")
    env = make_env()
    with_style_replies(env)
    code, text = await cli(env, *argv)
    assert code == 0
    assert "【风格规范】" in text and "风格清单 6 条" in text and "看了原图" in text
    assert "1. 清单第 1 条" in text  # --details 显示规范与清单
    assert "参考图 1 张" in text
    code, shown = await cli(env, "show", env.rt.repo.list_sessions(1)[0].id)
    assert "【风格规范】" in shown and "清单第 1 条" not in shown  # 不带 --details 只有一行概述

    env2 = make_env()
    with_style_replies(env2)
    code, plain = await cli(env2, *argv, "--no-style-ref")
    assert code == 0 and "【风格规范】" not in plain and "参考图" not in plain
    assert not [c for c in env2.fake.calls if "独立、仔细地观察风格参考图" in c.messages[0].content]


async def test_estimate_warns_when_discussion_needs_many_media_files():
    env = Env()
    code, text = await cli(env, "estimate", "画四张不同风格的插画，然后拼成一张长图", "--seed", "1")
    assert code == 0 and "建议切换到协同模式" in text and "--mode collab" in text
    code, text = await cli(env, "estimate", MEDIUM, "--seed", "1")
    assert "建议切换到协同模式" not in text


async def test_note_command_records_and_validates():
    from roundtable.core.routing import Question

    env = Env()
    sid = env.orc.open(Question(SHORT), seed=1)
    code, out = await cli(env, "note", sid, "请注意单位")
    assert code == 0 and "已记下" in out
    assert [n["text"] for n in env.rt.repo.pending_notes(sid)] == ["请注意单位"]
    code, out = await cli(env, "note", sid, "   ")
    assert code == 1 and "不能为空" in out
    code, out = await cli(env, "note", "不存在", "嗨")
    assert code == 1 and "找不到" in out


async def test_bench_runs_reports_exports_and_lists(tmp_path):
    env = Env(confirm_threshold_usd=100.0)
    bank = tmp_path / "q.yaml"
    bank.write_text(
        "questions:\n  - {id: m-1, category: math, text: '" + MEDIUM + "', "
        "judge: {type: number, answer: -2}}\n",
        encoding="utf-8",
    )
    out_csv = tmp_path / "r.csv"
    code, text = await cli(
        env, "bench", "--questions", str(bank), "--modes", "solo:budget,discussion:budget",
        "--batch", "demo", "--seed", "4", "-o", str(out_csv),
    )  # fmt: skip
    assert code == 0
    assert "预估花费" in text and "solo/budget" in text and "每答对一题平均花费" in text
    assert "m-1" in text and "✓" in text
    assert out_csv.read_text(encoding="utf-8-sig").startswith("id,batch,question_id")
    code, text = await cli(env, "bench", "--list")
    assert code == 0 and "demo" in text
    code, text = await cli(env, "bench", "--report", "demo", "-o", str(tmp_path / "r.json"))
    assert code == 0 and (tmp_path / "r.json").exists()
    code, text = await cli(env, "bench", "--report", "nope")
    assert code == 2


async def test_bench_rejects_bad_bank_and_empty_filter(tmp_path):
    env = Env()
    bad = tmp_path / "bad.yaml"
    bad.write_text("questions: 3\n")
    code, text = await cli(env, "bench", "--questions", str(bad))
    assert code == 2 and "实验设置有误" in text
    code, text = await cli(env, "bench", "--category", "no-such-category")
    assert code == 2 and "没有符合条件" in text


async def test_ask_auto_mode_shows_panel_advice_and_yes_accepts_it():
    from ..core.orchestrator.test_media import script

    env = Env(confirm_threshold_usd=100.0)
    script(
        env,
        **{
            "分诊小组的一员": lambda m, msgs: (
                '{"difficulty": "simple", "workflow": "solo", "reason": "一步就能算出"}'
            )
        },
    )
    code, text = await cli(env, "ask", MEDIUM, "--mode", "auto", "--yes", "--seed", "3")
    assert code == 0
    assert "需要你确认" in text and "采用建议：个人作答" in text and "一步就能算出" in text
    assert "（--yes：自动选择继续）" in text
    assert env.models_called("独立完成同一道题") == ["b1"]


async def test_brainstorm_cli_and_mindmap_export(tmp_path):
    from ..core.orchestrator.test_brainstorm import make

    env = make()
    code, text = await cli(env, "ask", "怎样拓展思路", "--mode", "brainstorm", "--seed", "3")
    assert code == 0 and "降低开裂" in text
    sid = re.search(r"会话：(\w+)", text).group(1)
    svg = tmp_path / "m.svg"
    code, text = await cli(env, "mindmap", sid, "-o", str(svg))
    assert code == 0 and svg.read_text(encoding="utf-8").startswith("<svg")
    md = tmp_path / "m.md"
    assert (await cli(env, "mindmap", sid, "-o", str(md)))[0] == 0
    assert md.read_text(encoding="utf-8").startswith("# 降低开裂")
    mmd = tmp_path / "m.mmd"
    await cli(env, "mindmap", sid, "-o", str(mmd))
    assert mmd.read_text(encoding="utf-8").startswith("mindmap")


async def test_mindmap_command_without_mindmap_explains():
    env = Env(confirm_threshold_usd=100.0)
    await cli(env, "ask", SHORT, "--seed", "1", answers=Answers("n"))
    sid = re.search(r"会话：(\w+)", (await cli(env, "history"))[1])
    code, text = await cli(env, "mindmap", sid.group(1) if sid else "x")
    assert code == 2 or "没有思维导图" in text


async def test_ask_defaults_to_office_mode():
    """不写 --mode 时是办公室模式：经理派一位员工办理，经理过目后交付。"""
    env = Env(confirm_threshold_usd=100.0)
    out = io.StringIO()
    code = await run(["ask", SHORT, "--seed", "3"], runtime=env.rt, out=out, ask=Answers("n"))
    text = out.getvalue()
    assert code == 0, text
    assert "【经理分诊】" in text and "【经理过目】通过" in text and "【交付】" in text
    assert "预算单：单人办预计 $" in text
    assert "经理 + 3 位在岗员工" in text and "（经理）" in text and "统筹" not in text
    assert "最大值 2，最小值 -2" in text.split("【交付】")[1]
    assert "按规则派活" not in text  # 经理的分诊可用
    # 经理两次（分诊、过目），只有一位员工办理
    assert len(env.models_called("办公室的经理")) == 2
    assert len(env.models_called("办公室的员工")) == 1


async def test_models_check_updates_only_reports():
    env = Env()

    async def fetch(url):
        data = [
            {"id": m.id, "created": 1, "pricing": {"prompt": "0", "completion": "0"}}
            for m in env.config.models.models
            if m.id != "f5"
        ]
        return {"data": data}

    env.rt.catalog.fetch = fetch
    before = env.config.model_dump()
    code, text = await cli(env, "models", "--check-updates")
    assert code == 0 and "价格变动" in text and "b1" in text
    assert "找不到" in text and "f5" in text and "只提示" in text
    assert env.rt.config.model_dump() == before  # 不改配置

    async def broken(url):
        raise OSError("secret-ish detail")

    env.rt.catalog.fetch = broken
    code, text = await cli(env, "models", "--check-updates")
    assert code == 1 and "OSError" in text and "secret-ish" not in text


async def test_ask_office_meeting_shows_attendees_speeches_and_decision():
    """经理决定开会：命令行列出参会员工、（详情里的）发言，交付经理拍板的结论和纪要。"""
    env = Env(confirm_threshold_usd=100.0)
    env.office_mode = "meeting"
    out = io.StringIO()
    code = await run(
        ["ask", SHORT, "--seed", "3", "--details"], runtime=env.rt, out=out, ask=Answers("n")
    )
    text = out.getvalue()
    assert code == 0, text
    assert "【经理分诊】开会，参会：" in text and "发言】" in text
    assert "## 结论" in text.split("【交付】")[1]
    assert "【经理过目】" not in text
    assert len(env.models_called("参会人之一")) == 2


async def test_ask_office_project_shows_team_and_review():
    """经理决定项目分工：命令行列出项目组，项目组桌子走协同，最后是复核经理的验收。"""
    env = Env(confirm_threshold_usd=100.0)
    env.office_mode = "project"
    env.office_review = "revise"
    out = io.StringIO()
    code = await run(["ask", SHORT, "--seed", "3"], runtime=env.rt, out=out, ask=Answers("n"))
    text = out.getvalue()
    assert code == 0, text
    assert "【经理分诊】项目分工，项目组：" in text and "—— 项目组 ——" in text
    assert "【复核经理验收】退回修改一次" in text
    assert "修改后的项目成果" in text.split("【交付（按验收意见修改后）】")[1]


async def test_performance_command_lists_and_resets():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(env, "performance")
    assert code == 0 and "还没有绩效记录" in text
    await run(["ask", SHORT, "--seed", "3"], runtime=env.rt, out=io.StringIO(), ask=Answers("n"))
    code, text = await cli(env, "performance")
    assert code == 0 and "单人办 1 件，通过率 100%" in text
    code, text = await cli(env, "performance", "--reset")
    assert code == 0 and "已清零" in text
    code, text = await cli(env, "performance")
    assert "还没有绩效记录" in text
