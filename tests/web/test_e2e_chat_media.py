"""前端：聊天里的表情包、图片、语音；朋友圈带图发动态、评论用表情包。文件只存本机临时目录。"""

from __future__ import annotations

import struct
import zlib

from tests.web.conftest import wait_until


def png_bytes(w=8, h=6) -> bytes:
    raw = b"".join(b"\x00" + b"\xc8\x78\x50" * w for _ in range(h))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def media_server(serve, tmp_path):
    def setup(env):
        env.rt.chat_media_dir = tmp_path / "chat_media"

    return serve(confirm_threshold_usd=100.0, setup=setup)


def test_sticker_image_and_lightbox_in_dm(serve, page, tmp_path):
    srv = media_server(serve, tmp_path)
    repo = srv.env.rt.repo
    page.goto(srv.url + "/#m=chat")
    page.wait_for_selector(".gc-room[data-room='dm:V1']")
    page.click(".gc-room[data-room='dm:V1']")
    # 表情面板：分类切换，表情包点一下直接发出（大号、没有气泡）
    page.click("#gc-tools [data-cm='panel']")
    page.wait_for_selector("#gc-tools .cm-panel:not([hidden]) .cm-emo")
    page.click("#gc-tools .cm-emo >> nth=0")
    assert page.input_value("#chat-input") == "😀"
    page.fill("#chat-input", "")
    page.click("#gc-tools [data-tab='pack:小黄脸']")  # 点 Emoji 后面板还开着
    page.click("#gc-tools [data-stk='小黄脸·抱抱']")
    page.wait_for_selector(".gc-msg.me .gc-bub.stk-only .stk.big")
    wait_until(
        lambda: (
            repo.chat_messages("dm:V1")
            and repo.chat_messages("dm:V1")[0]["text"] == "[表情:小黄脸·抱抱]"
        )
    )
    # 最近使用里有它
    page.click("#gc-tools [data-cm='panel']")
    page.click("#gc-tools [data-tab='recent']")
    page.wait_for_selector("#gc-tools [data-stk='小黄脸·抱抱']")
    page.keyboard.press("Escape")
    page.click("#gc-feed")
    # 选两张图 + 文字一起发
    page.set_input_files(
        "#gc-tools [data-cm-file='image']",
        [
            {"name": "a.png", "mimeType": "image/png", "buffer": png_bytes()},
            {"name": "b.png", "mimeType": "image/png", "buffer": png_bytes(6, 6)},
        ],
    )
    page.wait_for_selector("#gc-tray .cm-thumb.done >> nth=1")
    page.fill("#chat-input", "看看这个")
    page.keyboard.press("Enter")
    page.wait_for_selector(".gc-msg.me .cm-grid .cm-cell.img >> nth=1")
    assert "看看这个" in page.inner_text(".gc-msg.me .gc-bub.has-media")
    assert "[图片]" not in page.inner_text("#gc-feed")
    assert page.locator("#gc-tray .cm-thumb").count() == 0
    sent = [m for m in repo.chat_messages("dm:V1") if m["author"] == "user"][-1]
    assert sent["text"] == "看看这个\n[图片] [图片]" and len(sent["meta"]["media"]) == 2
    assert all((tmp_path / "chat_media" / m["id"]).is_file() for m in sent["meta"]["media"])
    # 点图看大图，左右翻
    page.click(".gc-msg.me .cm-cell.img >> nth=0")
    page.wait_for_selector("dialog.cm-lightbox[open] img.cm-lb-item")
    page.click("dialog.cm-lightbox [data-lb-next]")
    page.wait_for_selector("dialog.cm-lightbox .cm-lb-dots i.on:nth-child(2)")
    page.keyboard.press("Escape")
    page.wait_for_selector("dialog.cm-lightbox[open]", state="detached")


def test_voice_bubble_plays(serve, page, tmp_path):
    srv = media_server(serve, tmp_path)
    repo = srv.env.rt.repo
    ogg = b"OggS" + b"\x00" * 60
    page.goto(srv.url + "/#m=chat")
    page.wait_for_selector("#gc-members button[data-name]")
    up = page.evaluate(
        """async (bytes) => {
          const headers = {'Content-Type': 'audio/ogg'};
          const body = new Uint8Array(bytes);
          const r = await fetch('/api/chat/media', {method: 'POST', headers, body});
          return r.json();
        }""",
        list(ogg),
    )
    repo.add_chat_message(
        "main",
        "user",
        "[语音 5 秒]",
        meta={"media": [{"id": up["id"], "kind": "voice", "duration": 5}], "auto_text": True},
    )
    page.reload()
    page.wait_for_selector(".gc-bub.media-only .cm-voice")
    assert "5″" in page.inner_text(".cm-voice")
    assert page.locator(".cm-voice .cm-wave i").count() >= 12


def test_moment_with_photos_and_sticker_comment(serve, page, tmp_path):
    srv = media_server(serve, tmp_path)
    repo = srv.env.rt.repo
    page.goto(srv.url + "/#m=chat")
    page.wait_for_selector("#gc-moments")
    page.click("#gc-moments")
    page.wait_for_selector("dialog#moments[open]")
    page.click("[data-compose]")
    page.set_input_files(
        "#mo-new [data-cm-file='image']",
        [
            {"name": f"{i}.png", "mimeType": "image/png", "buffer": png_bytes(4 + i, 4)}
            for i in range(4)
        ],
    )
    page.wait_for_selector("#mo-new .cm-thumb.done >> nth=3")
    page.click("#mo-new [data-cm='panel']")
    page.click("#mo-new [data-tab='pack:小黄脸']")
    page.click("#mo-new [data-stk='小黄脸·开心']")  # 发动态时表情包插进文字里
    page.keyboard.type("周末")
    page.click("#mo-new [type=submit]")
    page.wait_for_selector(".mo .cm-grid.moment.four .cm-cell >> nth=3")
    assert page.locator(".mo .mo-text .stk").count() == 1
    row = repo.moments(author="user", limit=5)[0]
    assert row["text"].startswith("[表情:小黄脸·开心]周末") and len(row["meta"]["media"]) == 4
    # 评论：表情包插进评论框
    mid = row["id"]
    page.click(f"[data-menu='{mid}']")
    page.click(f"[data-reply='{mid}:']")
    page.click(f".mo-cf[data-mid='{mid}'] [data-cm='panel']")
    page.click(f".mo-cf[data-mid='{mid}'] [data-tab='pack:文字']")
    page.click(f".mo-cf[data-mid='{mid}'] [data-stk='文字·笑死']")
    assert page.input_value(f".mo-cf[data-mid='{mid}'] input[name=text]") == "[表情:文字·笑死]"
    page.click(f".mo-cf[data-mid='{mid}'] button[type=submit]")
    page.wait_for_selector(".mo-comments .stk")


def test_seen_mark_and_look_closer(serve, page, tmp_path):
    """发图后感官先快速看一遍（标「已看过 · 快速」）；点「细看」改用精细，再有一位成员回应。"""
    srv = media_server(serve, tmp_path)
    repo = srv.env.rt.repo
    page.goto(srv.url + "/#m=chat")
    page.wait_for_selector(".gc-room[data-room='dm:V1']")
    page.click(".gc-room[data-room='dm:V1']")
    page.set_input_files(
        "#gc-tools [data-cm-file='image']",
        [{"name": "a.png", "mimeType": "image/png", "buffer": png_bytes()}],
    )
    page.wait_for_selector("#gc-tray .cm-thumb.done")
    page.click("#gc-send")
    page.wait_for_selector(".gc-msg.me .gc-tag.seen:has-text('已看过 · 快速')")
    page.wait_for_selector(".gc-msg:not(.me) .gc-bub")
    page.click(".gc-msg.me [data-look]")
    page.wait_for_selector(".gc-msg.me .gc-tag.seen:has-text('已看过 · 精细')")
    assert page.locator(".gc-msg.me [data-look]").count() == 0  # 精细看过就不再显示按钮
    wait_until(lambda: len([m for m in repo.chat_messages("dm:V1") if m["author"] == "V1"]) == 2)
