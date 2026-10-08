"""命令行：用 Fake 模型跑完整流程；揭晓前输出中不得出现任何模型 / 厂商 / 渠道名。"""

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
    code, text = await cli(env, "ask", MEDIUM, "--details", "--seed", "3", answers=Answers("n"))
    assert code == 0
    for term in identity_terms(env):
        assert not mentions(text, term), term
    assert "组员甲" in text and "【最终答案】" in text and "最大值 2，最小值 -2" in text
    assert "【共识】" in text and "【分歧】" in text
    assert "稍后可用 roundtable reveal" in text


async def test_reveal_after_run_shows_models_and_channels():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(env, "ask", SHORT, "--reveal")
    assert code == 0
    revealed = text.split("揭晓身份")[1]
    assert "渠道：c" in revealed and "规划员" not in revealed  # 简单题不调用规划员
    assert any(mentions(revealed, m.id) for m in env.config.models.models)
    assert not any(mentions(before_reveal(text), t) for t in identity_terms(env))


async def test_checkpoint_enter_takes_recommendation():
    env = Env(confirm_threshold_usd=0.0001)
    answers = Answers("", "n")  # 回车 = 推荐（继续），然后不揭晓
    code, text = await cli(env, "ask", SHORT, "--preset", "strongest", answers=answers)
    assert code == 0 and "需要你确认" in text and "← 推荐" in text
    assert "推荐项 1" in answers.prompts[0]


async def test_checkpoint_number_and_invalid_input():
    env = Env(confirm_threshold_usd=0.0001)
    # 卡片选项：1 继续、2 改用单人快答、3 改用小圆桌、4 停止
    answers = Answers("9", "abc", "4")
    code, text = await cli(env, "ask", SHORT, "--preset", "strongest", answers=answers)
    assert code == 1 and text.count("无效的输入") == 2
    assert "状态：stopped" in text


async def test_yes_flag_auto_continues():
    env = Env(confirm_threshold_usd=0.0001)
    code, text = await cli(env, "ask", SHORT, "--preset", "strongest", "--yes", "--no-reveal")
    assert code == 0 and "自动选择继续" in text


async def test_manual_members_flag():
    env = Env(confirm_threshold_usd=100.0)
    code, text = await cli(
        env, "ask", SHORT, "--members", "b1,b2", "--coordinator", "f1", "--no-reveal"
    )
    assert code == 0 and "2 位组员" in text


async def test_unknown_member_is_reported():
    env = Env()
    code, text = await cli(env, "ask", SHORT, "--members", "b1,ghost", "--no-reveal")
    assert code == 1 and "无法开始" in text and "ghost" in text


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
    await cli(env, "ask", SHORT, "--no-reveal")
    code, text = await cli(env, "models")
    assert code == 0 and "✓ b1" in text and "本月已用" in text
    code, text = await cli(env, "history")
    sid = text.split()[1]
    assert code == 0 and "completed" in text
    code, text = await cli(env, "show", sid)
    assert code == 0 and not any(mentions(text, t) for t in identity_terms(env))
    code, text = await cli(env, "reveal", sid)
    assert code == 0 and "组员甲 = " in text


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
        build_parser().parse_args(["ask", "--preset", "luxury", "q"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ask", "--preset", "saver", "--members", "a", "q"])
