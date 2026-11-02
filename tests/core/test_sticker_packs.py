"""外部表情包（dsh-meme 格式）：解析压缩包、起名、装 / 改 / 删、聊天里认得、提示词里只列几张。"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from roundtable.core.chat_actions import member_stickers, sticker_menu, sticker_packs
from roundtable.core.sticker_packs import PackError, StickerPacks, describe

from .orchestrator.conftest import Env

PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 16
GIF = b"GIF89a" + b"g" * 16


def make_pack(
    tmp: Path,
    pack_id: str = "whale",
    name: str = "鲸鱼表情包",
    rows=None,
    extra: dict[str, bytes] | None = None,
    license: str = "CC0-1.0",
) -> bytes:
    """造一个 dsh-meme 格式的压缩包：manifest.json + index.db + memes/…"""
    rows = rows or [
        ("memes/sad/1790141444477_754.png", "sad", "委屈道歉，我错了", "抱歉 对不起 鲸鱼"),
        ("memes/angry/1790141444508_299.png", "angry", "别催我，一脸不耐烦", "别催 烦 鲸鱼"),
        ("memes/happy/开心转圈.gif", "happy", "开心得转圈圈" * 5, "开心转圈 开心 转圈 鲸鱼"),
        ("memes/happy/1790141444999_1.png", "happy", "", "比耶 胜利 鲸鱼"),
    ]
    db = tmp / f"{pack_id}.db"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.execute(
        "create table memes (path text primary key, tag text, file_name text, caption text,"
        " keywords text, mtime real, captioned_at real)"
    )
    for path, tag, caption, keywords in rows:
        con.execute(
            "insert into memes values (?, ?, ?, ?, ?, 0, 0)",
            (path, tag, path.rsplit("/", 1)[1], caption, keywords),
        )
    con.commit()
    con.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"id": pack_id, "name": name, "version": "1.0.0", "license": license}),
        )
        zf.write(db, "index.db")
        for path, *_ in rows:
            zf.writestr(path, GIF if path.endswith(".gif") else PNG)
        for path, data in (extra or {}).items():
            zf.writestr(path, data)
    return buf.getvalue()


def packs_of(env: Env, tmp_path: Path, fetch=None) -> StickerPacks:
    env.rt.sticker_dir = tmp_path / "stickers"
    p = sticker_packs(env.rt)
    if fetch:
        p._fetch = fetch
    return p


def test_describe_names_uses_and_means():
    rows = [
        {"path": "memes/a/1790_1.png", "tag": "a", "caption": "别催我", "keywords": "别催 烦 鲸"},
        {"path": "memes/a/1790_2.png", "tag": "a", "caption": "", "keywords": "别催 急 鲸"},
        {
            "path": "memes/a/害羞失语啊啊.webp",
            "tag": "a",
            "caption": "长" * 30,
            "keywords": "害羞 脸红 鲸",
        },
        {"path": "memes/b/x.png", "tag": "b", "caption": "", "keywords": "鲸"},
    ]
    items = describe(rows)
    # 数字文件名用关键词，重名加数字；像词的文件名截到 6 字
    assert [i["name"] for i in items] == ["别催", "别催2", "害羞失语啊啊", "x"]
    # 整包大半都有的「鲸」不算意思词；说明短就当用法，长了用关键词
    assert items[0]["use"] == "别催我" and items[0]["means"] == ["烦"]
    assert items[2]["use"] == "害羞、脸红" and "鲸" not in items[2]["means"]


def test_install_from_zip_lists_items_and_serves_images(tmp_path):
    env = Env()
    packs = packs_of(env, tmp_path)
    s = packs.install("", "V1", "", make_pack(tmp_path))
    assert s["id"] == "whale" and s["label"] == "鲸鱼" and s["owner"] == "V1" and s["count"] == 4
    p = packs.pack("whale")
    assert [i["name"] for i in p["items"]] == ["别催", "比耶", "开心转圈", "抱歉"]
    assert p["license"] == "CC0-1.0"
    data, mime = packs.image("whale", 2)
    assert data == GIF and mime == "image/gif"
    with pytest.raises(PackError):
        packs.image("whale", 99)
    with pytest.raises(PackError):
        packs.image("../x", 0)
    # 图只在数据目录下，不在仓库里
    assert (tmp_path / "sticker_packs" / "whale" / "pack.json").is_file()


def test_unsafe_or_bad_entries_are_skipped(tmp_path):
    env = Env()
    packs = packs_of(env, tmp_path)
    extra = {
        "../evil.png": PNG,
        "memes/../../evil2.png": PNG,
        "memes/a/script.js": b"alert(1)",
        "memes/a/fake.png": b"not an image",
    }
    packs.install("", "", "", make_pack(tmp_path, extra=extra))
    names = [i["file"] for i in packs.pack("whale")["items"]]
    assert all(".." not in n for n in names) and "memes/a/script.js" not in names
    assert "memes/a/fake.png" not in names
    assert not (tmp_path / "evil.png").exists() and not (tmp_path.parent / "evil2.png").exists()
    with pytest.raises(PackError, match="zip"):
        packs.install("", "", "", b"not a zip")
    with pytest.raises(PackError, match="成员"):
        packs.install("", "Nobody", "", make_pack(tmp_path))


def test_labels_avoid_nicknames_and_common_packs(tmp_path):
    env = Env()
    packs = packs_of(env, tmp_path)
    # 默认套装名和成员昵称重了就加「包」
    s = packs.install("", "", "", make_pack(tmp_path, name="阿一表情包"))
    assert s["label"] == "阿一包"
    with pytest.raises(PackError, match="已经有了"):
        packs.assign("whale", label="小黄脸")
    with pytest.raises(PackError, match="1～6"):
        packs.assign("whale", label="太长太长太长了")
    assert packs.assign("whale", owner="V2", label="鲸")["owner"] == "V2"
    # 重装时不传就沿用原来的套装名和成员
    again = packs.install("", None, "", make_pack(tmp_path, name="阿一表情包"))
    assert again["label"] == "鲸" and again["owner"] == "V2"
    assert packs.remove("whale")["removed"] and packs.installed() == []


def test_catalog_install_checks_sha256_and_size(tmp_path):
    env = Env()
    zipped = make_pack(tmp_path)
    good = hashlib.sha256(zipped).hexdigest()
    catalog = {
        "packs": [
            {
                "id": "whale",
                "name": "鲸鱼",
                "license": "CC0-1.0",
                "count": 4,
                "bytes": len(zipped),
                "sha256": good,
                "archiveUrl": "https://x/whale.zip",
            },
            {
                "id": "bad",
                "name": "坏的",
                "count": 1,
                "bytes": 10,
                "sha256": "0" * 64,
                "archiveUrl": "https://x/bad.zip",
            },
            {"id": "big", "name": "太大", "bytes": 10**10, "archiveUrl": "https://x/big.zip"},
            {"id": "remote", "name": "只有清单", "manifestUrl": "https://x/m.json"},
        ]
    }

    def fetch(url, limit):
        if url.endswith("catalog.json"):
            return json.dumps(catalog).encode()
        return zipped

    packs = packs_of(env, tmp_path, fetch)
    rows = {p["id"]: p for p in packs.catalog()}
    assert rows["whale"]["installable"] and not rows["remote"]["installable"]
    assert packs.install("whale", "V1")["count"] == 4
    assert {p["id"]: p for p in packs.catalog()}["whale"]["installed"]
    with pytest.raises(PackError, match="校验"):
        packs.install("bad")
    with pytest.raises(PackError, match="上限"):
        packs.install("big")
    with pytest.raises(PackError, match="装不了"):
        packs.install("remote")
    with pytest.raises(PackError, match="没有"):
        packs.install("nope")
    offline = packs_of(env, tmp_path, lambda url, limit: None)
    with pytest.raises(PackError, match="联网"):
        offline.catalog()


def test_member_can_send_her_pack_and_common_packs(tmp_path):
    env = Env()
    packs = packs_of(env, tmp_path)
    packs.install("", "V1", "", make_pack(tmp_path))
    packs.install("", "", "", make_pack(tmp_path, pack_id="cat", name="猫猫头表情包"))
    v1 = member_stickers(env.rt, "V1", "阿一")
    v2 = member_stickers(env.rt, "V2", "阿二")
    assert "鲸鱼" in v1 and "猫猫头" in v1
    assert "鲸鱼" not in v2 and "猫猫头" in v2
    # 关掉就都没有
    rules = env.rt.config.roundtable.chat.actions.sticker_packs.model_copy(
        update={"enabled": False}
    )
    acts = env.rt.config.roundtable.chat.actions.model_copy(update={"sticker_packs": rules})
    chat = env.rt.config.roundtable.chat.model_copy(update={"actions": acts})
    rt_cfg = env.rt.config.roundtable.model_copy(update={"chat": chat})
    env.rt.config = env.rt.config.model_copy(update={"roundtable": rt_cfg})
    assert "鲸鱼" not in member_stickers(env.rt, "V1", "阿一")


def test_menu_lists_a_few_per_pack_with_uses_and_rotates(tmp_path):
    env = Env()
    packs = packs_of(env, tmp_path)
    rows = [(f"memes/a/表情{i:02d}.png", "a", f"用法{i}", f"词{i} 鲸鱼") for i in range(30)]
    packs.install("", "V1", "", make_pack(tmp_path, rows=rows))
    menu = sticker_menu(env.rt, "V1", "阿一")
    assert menu.startswith("你的专属：")
    k = env.rt.config.roundtable.chat.actions.sticker_packs.menu_size
    assert menu.count("[表情:鲸鱼·") == k
    assert "（用法" in menu
    a = packs.menu("V1", "2026-11-01T01")[0]["鲸鱼"]
    b = packs.menu("V1", "2026-11-01T02")[0]["鲸鱼"]
    assert a == packs.menu("V1", "2026-11-01T01")[0]["鲸鱼"] and a != b
    # 提示词里只列几张，聊天里认得整套
    assert len(member_stickers(env.rt, "V1", "阿一")["鲸鱼"]) == 30


def test_in_memory_runtime_has_no_packs():
    env = Env()
    env.rt.sticker_dir = None
    packs = sticker_packs(env.rt)
    assert packs.installed() == [] and packs.stickers("V1") == ({}, {})
    with pytest.raises(PackError):
        packs.install("", "", "", b"x")
