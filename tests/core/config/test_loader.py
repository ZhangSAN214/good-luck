"""配置加载与校验（阶段 1）。"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from roundtable.core.config import DEFAULT_CONFIG_DIR, ConfigError, load_config


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    shutil.copytree(DEFAULT_CONFIG_DIR, tmp_path / "config")
    return tmp_path / "config"


def edit(config_dir: Path, filename: str, change: Callable[[dict], None]) -> None:
    path = config_dir / filename
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def expect_error(config_dir: Path, *fragments: str) -> str:
    with pytest.raises(ConfigError) as info:
        load_config(config_dir)
    message = str(info.value)
    for fragment in fragments:
        assert fragment in message, message
    return message


# --- 仓库自带的配置 -----------------------------------------------------------


def test_repo_config_loads_with_defaults():
    cfg = load_config()
    rt = cfg.roundtable
    assert rt.seats == 12 and rt.reviews_per_answer == 3
    assert (rt.budget.monthly_usd, rt.budget.daily_usd, rt.budget.warn_ratio) == (20.0, 3.0, 0.8)
    assert rt.channel_mode == "auto"
    assert rt.pipeline == ["answer", "review", "revise", "synthesize", "reveal"]
    assert set(rt.prompts) == {
        "planner",
        "answer",
        "review",
        "revise",
        "synthesize",
        "redo",
        "decompose",
        "volunteer",
        "assign",
        "work",
        "cross_review",
        "rework",
        "merge",
        "attachments",
        "describe_image",
        "transcribe",
        "tools",
        "image_gen",
        "web_search",
        "web_fetch",
        "media_brief",
        "media_review",
        "media_refine",
        "work_media",
        "rework_media",
    }
    assert rt.collab_pipeline == [
        "decompose",
        "volunteer",
        "assign",
        "work",
        "cross_review",
        "rework",
        "merge",
        "reveal",
    ]
    assert rt.effort_check.enabled and rt.effort_check.redo
    # 每个档位的全部模型都能坐下（一个当统筹）
    for plan in cfg.routing.plans.values():
        tier_models = [m for m in cfg.models.enabled if m.tier in plan.tiers]
        assert rt.min_members + 1 <= len(tier_models) <= rt.seats + 1
    assert len(cfg.personas.codes) >= rt.seats


def test_repo_config_has_both_channel_kinds():
    cfg = load_config()
    kinds = {c.kind for c in cfg.models.channels.values()}
    assert {"aggregator", "direct"} <= kinds
    # 每个模型都至少能经由 OpenRouter 调用
    for m in cfg.models.models:
        assert any(r.channel == "openrouter" for r in m.routes), m.id


def test_route_price_falls_back_to_model_price():
    m = load_config().models.get("gemini-3.8-flash")
    assert m.price_for(m.routes[0]) == m.price


# --- 文件级错误 ---------------------------------------------------------------


def test_missing_file(config_dir):
    (config_dir / "personas.yaml").unlink()
    expect_error(config_dir, "找不到配置文件", "personas.yaml")


def test_invalid_yaml(config_dir):
    (config_dir / "models.yaml").write_text("models: [unclosed", encoding="utf-8")
    expect_error(config_dir, "models.yaml", "YAML")


def test_top_level_must_be_mapping(config_dir):
    (config_dir / "roundtable.yaml").write_text("- a\n- b\n", encoding="utf-8")
    expect_error(config_dir, "roundtable.yaml", "顶层")


# --- models.yaml -------------------------------------------------------------


def test_duplicate_model_id(config_dir):
    edit(config_dir, "models.yaml", lambda d: d["models"].append(dict(d["models"][0])))
    expect_error(config_dir, "模型 id 重复")


def test_unknown_tag(config_dir):
    edit(config_dir, "models.yaml", lambda d: d["models"][0]["tags"].append("cooking"))
    expect_error(config_dir, "未知标签", "cooking")


def test_unknown_channel(config_dir):
    def change(d):
        d["models"][0]["routes"].append({"channel": "nowhere", "model": "x"})

    edit(config_dir, "models.yaml", change)
    expect_error(config_dir, "未定义的渠道", "nowhere")


def test_duplicate_channel_in_routes(config_dir):
    def change(d):
        d["models"][0]["routes"].append(dict(d["models"][0]["routes"][0]))

    edit(config_dir, "models.yaml", change)
    expect_error(config_dir, "渠道不能重复")


def test_model_needs_a_route(config_dir):
    edit(config_dir, "models.yaml", lambda d: d["models"][0].update(routes=[]))
    expect_error(config_dir, "models.0.routes")


@pytest.mark.parametrize("field", ["vendor", "routes"])
def test_missing_model_field(config_dir, field):
    edit(config_dir, "models.yaml", lambda d: d["models"][0].pop(field))
    expect_error(config_dir, f"models.0.{field}")


def test_negative_price(config_dir):
    edit(config_dir, "models.yaml", lambda d: d["models"][0]["price"].update(input=-1))
    expect_error(config_dir, "models.0.price.input")


def test_typo_field_is_rejected(config_dir):
    edit(config_dir, "models.yaml", lambda d: d["models"][0].update(enabeld=False))
    expect_error(config_dir, "enabeld")


def test_bad_channel_kind(config_dir):
    edit(config_dir, "models.yaml", lambda d: d["channels"]["openai"].update(kind="proxy"))
    expect_error(config_dir, "channels.openai.kind")


def test_key_env_must_be_env_name(config_dir):
    # 防止有人把真实 key 填进 key_env
    edit(config_dir, "models.yaml", lambda d: d["channels"]["openai"].update(key_env="sk-abc"))
    expect_error(config_dir, "key_env")


def test_channel_without_key_is_allowed(config_dir):
    def change(d):
        d["channels"]["local"] = {
            "adapter": "openai_compat",
            "kind": "local",
            "base_url": "http://localhost:11434/v1",
            "key_env": None,
        }

    edit(config_dir, "models.yaml", change)
    assert load_config(config_dir).models.channels["local"].key_env is None


# --- roundtable.yaml ---------------------------------------------------------


@pytest.mark.parametrize(
    "change, fragment",
    [
        (lambda d: d.update(seats=1), "seats"),
        (lambda d: d.update(reviews_per_answer=0), "reviews_per_answer"),
        (lambda d: d.update(min_members=13), "min_members"),
        (lambda d: d["budget"].update(monthly_usd=0), "budget.monthly_usd"),
        (lambda d: d["budget"].update(daily_usd=-1), "budget.daily_usd"),
        (lambda d: d["budget"].pop("monthly_usd"), "budget.monthly_usd"),
        (lambda d: d["budget"].update(warn_ratio=1.2), "budget.warn_ratio"),
        (lambda d: d.update(token_threshold=-5), "token_threshold"),
        (lambda d: d.update(channel_mode="cheapest"), "channel_mode"),
        (lambda d: d["prompts"].update(answer="latest"), "v1"),
        (lambda d: d.update(pipeline=["answer", "answer"]), "不能重复"),
        (lambda d: d.update(pipeline=[]), "pipeline"),
        (lambda d: d["coordinator"].update(strategy="fixed", fixed_id=None), "fixed_id"),
        (lambda d: d["request"].update(failover_rounds=0), "failover_rounds"),
    ],
)
def test_invalid_roundtable(config_dir, change, fragment):
    edit(config_dir, "roundtable.yaml", change)
    expect_error(config_dir, "roundtable.yaml", fragment)


def test_daily_cap_can_be_disabled(config_dir):
    edit(config_dir, "roundtable.yaml", lambda d: d["budget"].update(daily_usd=None))
    assert load_config(config_dir).roundtable.budget.daily_usd is None


def test_channel_mode_defaults_to_auto(config_dir):
    edit(config_dir, "roundtable.yaml", lambda d: d.pop("channel_mode"))
    assert load_config(config_dir).roundtable.channel_mode == "auto"


@pytest.mark.parametrize("mode", ["openrouter", "direct", "auto"])
def test_channel_modes_accepted(config_dir, mode):
    edit(config_dir, "roundtable.yaml", lambda d: d.update(channel_mode=mode))
    assert load_config(config_dir).roundtable.channel_mode == mode


# --- personas.yaml 与交叉校验 -------------------------------------------------


def test_duplicate_codes(config_dir):
    edit(config_dir, "personas.yaml", lambda d: d.update(codes=["甲", "甲", "乙", "丙"]))
    expect_error(config_dir, "代号不能重复")


def test_codes_fewer_than_seats(config_dir):
    edit(config_dir, "personas.yaml", lambda d: d.update(codes=["甲", "乙", "丙", "丁"]))
    expect_error(config_dir, "代号池", "座位数")


def test_fixed_coordinator_must_exist(config_dir):
    edit(
        config_dir,
        "roundtable.yaml",
        lambda d: d["coordinator"].update(strategy="fixed", fixed_id="ghost"),
    )
    expect_error(config_dir, "ghost")


def test_persona_for_unknown_model(config_dir):
    edit(config_dir, "personas.yaml", lambda d: d.update(personas={"ghost": {"nickname": "x"}}))
    expect_error(config_dir, "personas", "ghost")


# --- 档位 ---------------------------------------------------------------------


def test_tier_values(config_dir):
    edit(config_dir, "models.yaml", lambda d: d["models"][0].update(tier="premium"))
    expect_error(config_dir, "models.0.tier")


def test_enabled_model_needs_tier(config_dir):
    edit(config_dir, "models.yaml", lambda d: d["models"][0].pop("tier", None))
    expect_error(config_dir, "缺少 tier")


def test_disabled_model_may_omit_tier(config_dir):
    edit(config_dir, "models.yaml", lambda d: d["models"][0].pop("tier", None))
    edit(config_dir, "models.yaml", lambda d: d["models"][0].update(enabled=False))
    assert load_config(config_dir).models.models[0].tier is None


@pytest.mark.parametrize("vendor", ["OpenAI", "Anthropic", "Google", "xAI", "DeepSeek", "Alibaba"])
def test_repo_vendors_have_flagship_and_budget(vendor):
    seated = [m for m in load_config().models.models if m.vendor == vendor and m.seat]
    tiers = sorted(m.tier for m in seated)
    assert tiers == ["budget", "flagship"]


def test_gemini_pro_prefers_google_direct():
    pro = next(
        m for m in load_config().models.models if m.vendor == "Google" and m.tier == "flagship"
    )
    assert [r.channel for r in pro.routes] == ["google", "openrouter"]


def test_tool_models_have_no_seat_and_image_gen():
    tools = [m for m in load_config().models.models if not m.seat]
    media_tags = {"image_gen", "image_edit", "tts", "stt", "video_gen", "music_gen"}
    assert tools and all(media_tags & set(m.tags) and m.tier for m in tools)
    # 媒体模型按秒 / 分钟 / 字符计价，或（图像）按 token 计价并给出一张图的典型 token 数
    assert all(m.media_price or m.image_tokens for m in tools)
    for kind_tag in ("image_gen", "tts", "stt", "video_gen"):
        assert any(kind_tag in m.tags for m in tools), kind_tag
