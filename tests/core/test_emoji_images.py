"""emoji 显示成图：用到才下载、存本机、下载不到退回系统字体（不联网，用假下载）。"""

from __future__ import annotations

from roundtable.core.emoji_images import EmojiImages, candidates

from .orchestrator.conftest import Env

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 10


def fake(have: set[str], calls: list[str]):
    def fetch(url: str) -> bytes | None:
        calls.append(url)
        return PNG if any(url.endswith(f"/{c}.png") for c in have) else None

    return fetch


def test_downloads_once_then_reads_from_disk(tmp_path):
    env = Env()
    calls: list[str] = []
    em = EmojiImages(env.rt, tmp_path, fake({"1f602"}, calls))
    assert em.status()["enabled"] is True
    assert em.image("1F602") == PNG
    assert (tmp_path / "1f602.png").read_bytes() == PNG
    assert EmojiImages(env.rt, tmp_path, fake(set(), calls)).image("1f602") == PNG  # 新实例读盘
    assert len(calls) == 1


def test_tries_with_and_without_variation_selector_and_remembers_misses(tmp_path):
    env = Env()
    calls: list[str] = []
    em = EmojiImages(env.rt, tmp_path, fake({"2764"}, calls))
    assert em.image("2764-fe0f") == PNG
    assert [c.rsplit("/", 1)[1] for c in calls] == ["2764-fe0f.png", "2764.png"]
    assert em.image("1f9ff") is None and em.image("1f9ff") is None
    assert len(calls) == 4  # 没有的只问一轮（两种写法），不写盘
    assert not (tmp_path / "1f9ff.png").exists()
    assert candidates("1f602") == ["1f602", "1f602-fe0f"]


def test_bad_codes_and_switched_off(tmp_path):
    env = Env()
    calls: list[str] = []
    em = EmojiImages(env.rt, tmp_path, fake({"1f602"}, calls))
    for bad in ("../x", "1f602.png", "zz", ""):
        assert em.image(bad) is None
    rules = env.rt.config.roundtable.chat.actions.emoji_images.model_copy(update={"enabled": False})
    acts = env.rt.config.roundtable.chat.actions.model_copy(update={"emoji_images": rules})
    chat = env.rt.config.roundtable.chat.model_copy(update={"actions": acts})
    rt_cfg = env.rt.config.roundtable.model_copy(update={"chat": chat})
    env.rt.config = env.rt.config.model_copy(update={"roundtable": rt_cfg})
    assert em.image("1f602") is None and em.status() == {"enabled": False, "style": ""}
    assert calls == []


def test_url_must_be_https_template():
    import pytest

    from roundtable.core.config.schema import EmojiImageRules

    with pytest.raises(ValueError):
        EmojiImageRules(enabled=True, url="http://x/{code}.png")
    with pytest.raises(ValueError):
        EmojiImageRules(enabled=True, url="https://x/a.png")
