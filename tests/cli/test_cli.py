"""命令行：用 Fake 模型跑完整流程；匿名讨论揭晓前输出中不得出现任何模型 / 厂商 / 渠道名。"""

from __future__ import annotations

import io

import pytest

from roundtable.cli import run
from roundtable.core.runtime import Runtime

from ..core.orchestrator.conftest import MEDIUM, SHORT, Env


class Answers:
    """依次返回预设的输入，并记录提示语。"""

    def __init__(self, *values: str):
        self.values = list(values)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.values.pop(0) if self.values else ""


async def cli(env: Env, *argv: str, answers: Answers | None = None) -> tuple[int, str]:
    out = io.StringIO()
    code = await run(list(argv), runtime=env.rt, out=out, ask=answers or Answers())
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
    assert "组员甲" in text and "【最终答案】" in text and "最大值 2，最小值 -2" in text
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
    assert "组员甲（" in text and "统筹（" in text
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
    # 卡片选项：1 继续、2 改用便宜档全员、3 停止
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
    assert "统筹（f1）" in text


async def test_flagship_tier_flag():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(env, "ask", SHORT, "--tier", "flagship", "--anonymous", "--no-reveal")
    assert code == 0 and "旗舰档全员（4 位组员 + 统筹）" in text


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
    assert code == 0 and "组员甲 = " in text


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
    assert "组员甲 的答案" in content  # 导出包含每位成员的产出


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
    assert "提交前预估" in text and "▶ 讨论 · 便宜档全员" in text and "协同 · 旗舰档全员" in text
    assert "上桌：" in text and "随机种子：5" in text
    code, text = await cli(env, "estimate", MEDIUM, "--anonymous")
    assert "上桌：" not in text
    for term in identity_terms(env):
        assert not mentions(text, term), term
    code, text = await cli(env, "ask", MEDIUM, "--mode", "collab", "--seed", "5")
    assert code == 0 and "提交前预估" in text and "▶ 协同 · 便宜档全员" in text
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
