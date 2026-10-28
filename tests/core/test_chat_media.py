"""聊天媒体：只存本机，按内容哈希命名，检查类型和特征字节，附件清单只留确实存在的文件。"""

from __future__ import annotations

import pytest

from roundtable.core.chat_media import ChatMediaStore, MediaError, clean_items, placeholder

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 32


def test_save_and_load(tmp_path):
    store = ChatMediaStore(tmp_path / "m")
    a = store.save(PNG, "image/png")
    b = store.save(PNG, "image/png")
    assert a == b and a.kind == "image" and a.id.endswith(".png")
    assert store.load(a.id) == (PNG, "image/png")
    voice = store.save(WEBM, "audio/webm;codecs=opus")
    assert voice.kind == "voice" and voice.mime == "audio/webm"
    assert store.load("../x.png") is None and store.load("nope") is None


def test_rejects_bad_files(tmp_path):
    store = ChatMediaStore(tmp_path)
    with pytest.raises(MediaError, match="只支持"):
        store.save(PNG, "image/svg+xml")
    with pytest.raises(MediaError, match="不像"):
        store.save(b"<html>" + b"0" * 20, "image/png")
    with pytest.raises(MediaError, match="太大"):
        store.save(PNG + b"0" * (11 * 1024 * 1024), "image/png")
    with pytest.raises(MediaError, match="本地数据目录"):
        ChatMediaStore(None).save(PNG, "image/png")


def test_clean_items_and_placeholder(tmp_path):
    store = ChatMediaStore(tmp_path)
    img = store.save(PNG, "image/png").id
    voice = store.save(WEBM, "audio/webm").id
    items = clean_items(
        store, [{"id": img, "w": 640, "h": 480, "x": 1}, {"id": voice, "duration": 4.6}]
    )
    assert items == [
        {"id": img, "kind": "image", "w": 640.0, "h": 480.0},
        {"id": voice, "kind": "voice", "duration": 4.6},
    ]
    assert placeholder(items) == "[图片] [语音 5 秒]"
    with pytest.raises(MediaError, match="不存在"):
        clean_items(store, [{"id": "0" * 32 + ".png"}])
    with pytest.raises(MediaError, match="最多"):
        clean_items(store, [{"id": img}] * 10)
