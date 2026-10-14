"""身份遮蔽：组员输出中的模型/厂商/别称在转给其他模型前被遮蔽。"""

from __future__ import annotations

import pytest

from roundtable.core.allocation import MASK, IdentityScrubber, identity_terms
from roundtable.core.config import load_config

MODELS = load_config().models
SCRUB = IdentityScrubber.from_config(MODELS)


def test_terms_come_from_config():
    terms = identity_terms(MODELS)
    for m in MODELS.models:
        assert m.id in terms and m.vendor in terms
        assert set(m.aliases) <= terms
        for r in m.routes:
            assert r.model in terms


@pytest.mark.parametrize(
    "text",
    [
        "作为 ChatGPT，我认为答案是 4。",
        "我是 Claude，由 Anthropic 训练。",
        "As a large language model from OpenAI, I think…",
        "我是通义千问。",
        "（本回答由 deepseek-v4-pro 生成）",
        "I'm GEMINI, built by google.",
        "model: qwen/qwen3.8-max-0902",
    ],
)
def test_self_identification_masked(text):
    out = SCRUB.scrub(text)
    assert MASK in out
    assert SCRUB.found(out) == []


def test_every_configured_name_is_masked():
    for term in identity_terms(MODELS):
        out = SCRUB.scrub(f"前文 {term} 后文")
        assert term not in out, term


def test_names_in_question_are_kept():
    question = "比较 Gemini 和 Claude 的上下文窗口"
    out = SCRUB.scrub("Gemini 更长，Claude 更短；我是 GPT。", context=question)
    assert "Gemini" in out and "Claude" in out
    assert "GPT" not in out


def test_ascii_terms_respect_word_boundaries():
    assert SCRUB.scrub("grokking 现象与 GPTQ 量化") == "grokking 现象与 GPTQ 量化"


def test_plain_text_untouched():
    text = "设 f(x) = x^2，则 f'(x) = 2x。"
    assert SCRUB.scrub(text) == text


def test_custom_terms():
    s = IdentityScrubber(["Foo", "foo-2"])
    assert s.scrub("I am foo-2 by Foo") == f"I am {MASK} by {MASK}"
    assert IdentityScrubber([]).scrub("anything") == "anything"
