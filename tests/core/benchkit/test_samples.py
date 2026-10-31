"""工作台生成器的本地示例素材包：清单校验、下载核对 sha256、复制进成品、给助手看的清单。"""

from __future__ import annotations

import asyncio

import pytest
import yaml

from roundtable.core.benchkit import samples as s
from roundtable.core.config.loader import ConfigError

DATA = b"fake-png-bytes"


def entry(**kw):
    base = {
        "id": "cat",
        "name": "猫",
        "kind": "photo",
        "file": "photos/cat.png",
        "url": "https://example.com/cat.png",
        "sha256": s.sha256_of(DATA),
        "license": "CC0",
        "author": "某人",
        "tags": ["宠物"],
    }
    base.update(kw)
    return base


def catalog(*entries):
    return s.SampleCatalog.model_validate({"samples": list(entries) or [entry()]})


def test_repo_catalog_is_valid_and_pins_every_file():
    cat = s.load_catalog()
    assert len(cat.samples) >= 10
    assert {x.kind for x in cat.samples} >= {"photo", "model"}
    for x in cat.samples:
        assert len(x.sha256) == 64 and x.url.startswith("https://")


def test_catalog_rejects_bad_entries(tmp_path):
    for bad in (
        entry(file="../etc/passwd.png"),
        entry(file="/abs/x.png"),
        entry(url="http://x/a.png"),
        entry(sha256="abc"),
        entry(extra="多余字段"),
    ):
        with pytest.raises(ValueError):
            catalog(bad)
    with pytest.raises(ValueError):
        catalog(entry(), entry(file="photos/other.png"))  # id 重复
    p = tmp_path / "bench_samples.yaml"
    p.write_text(yaml.safe_dump({"samples": [entry(kind="hologram")]}), encoding="utf-8")
    with pytest.raises(ConfigError, match="bench_samples.yaml"):
        s.load_catalog(p)
    assert s.load_catalog(tmp_path / "none.yaml").samples == []


def test_fetch_all_checks_sha_and_skips_existing(tmp_path):
    cat = catalog(entry(), entry(id="bad", file="photos/bad.png"), entry(id="err", file="x/e.png"))
    got: list[str] = []

    async def get(url):
        got.append(url)
        if len(got) == 2:
            return b"tampered"
        if len(got) == 3:
            raise OSError("网络断了")
        return DATA

    out = asyncio.run(s.fetch_all(cat, tmp_path, get))
    assert [x["state"] for x in out] == ["ok", "bad", "error"]
    assert (tmp_path / "_samples/photos/cat.png").read_bytes() == DATA
    assert not (tmp_path / "_samples/photos/bad.png").exists()
    assert "某人" in (tmp_path / "_samples/LICENSES.md").read_text(encoding="utf-8")
    again = asyncio.run(s.fetch_all(cat, tmp_path, get, only={"cat"}))
    assert [x["state"] for x in again] == ["have"] and len(got) == 3


def test_install_copies_pack_and_mine_into_product(tmp_path):
    root, folder = tmp_path / "bench", tmp_path / "bench" / "b1"
    (root / "_samples/photos").mkdir(parents=True)
    (root / "_samples/photos/cat.png").write_bytes(DATA)
    (root / "_samples/mine").mkdir()
    (root / "_samples/mine/我的.jpg").write_bytes(b"j")
    (root / "_samples/mine/note.txt").write_text("不收")
    items = s.install(root, folder, catalog(entry(), entry(id="dog", file="photos/dog.png")))
    assert [x["file"] for x in items] == [
        "assets/samples/photos/cat.png",
        "assets/samples/mine/我的.jpg",
    ]
    assert (folder / "assets/samples/photos/cat.png").read_bytes() == DATA
    assert (folder / "assets/samples/LICENSES.md").is_file()
    text = s.samples_text(items)
    assert "assets/samples/photos/cat.png：照片 · 猫（宠物；CC0）" in text
    assert "你自己的" in text


def test_samples_text_without_pack_forbids_online_images():
    assert "不要在线取图" in s.samples_text([])
