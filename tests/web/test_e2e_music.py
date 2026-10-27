"""前端：终端首页的背景音乐（自选添加、排序、删除、循环方式、音量记忆、切页淡出、只存本机）。"""

from __future__ import annotations

import io
import math
import struct
import wave

from tests.web.conftest import wait_until

LAND_HUB = "try { localStorage.setItem('roundtable.landing', 'hub') } catch {}"


def _wav(seconds: float = 4.0, freq: float = 440.0) -> bytes:
    """一段很轻的正弦波，浏览器一定能放。"""
    rate = 8000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(rate * seconds)
        tone = (int(800 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))
        frames = b"".join(struct.pack("<h", v) for v in tone)
        w.writeframes(frames)
    return buf.getvalue()


def _song(name: str, freq: float = 440.0) -> dict:
    return {"name": name, "mimeType": "audio/wav", "buffer": _wav(freq=freq)}


def _names(page) -> list[str]:
    return page.eval_on_selector_all(
        "#hub .mu-list .mu-name span:last-child", "els => els.map(e => e.textContent)"
    )


def _open(page) -> None:
    if page.is_hidden("#hub .mu-panel"):
        page.click("#hub .mu-btn")
    page.wait_for_selector("#hub .mu-panel", state="visible")


def test_music_playlist_is_chosen_by_hand_and_remembered_locally(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    posts: list[str] = []
    page.on("request", lambda r: r.method != "GET" and posts.append(r.url))
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .mu-btn")
    assert page.inner_text("#hub .mu-btn") == "音乐"
    _open(page)
    assert "还没有音乐" in page.inner_text("#hub .mu-now")
    # 一次选好几首；不是音频的文件不收
    page.set_input_files(
        "#hub [data-mu-file]",
        [
            _song("晚风.wav"),
            _song("夜航.wav", 523.0),
            {"name": "笔记.txt", "mimeType": "text/plain", "buffer": b"hi"},
        ],
    )
    wait_until(lambda: len(_names(page)) == 2)
    assert _names(page) == ["晚风", "夜航"]
    assert "不是这台电脑的浏览器能放的音频" in page.inner_text("#hub .mu-panel .hb-err")
    page.set_input_files("#hub [data-mu-file]", [_song("雨夜.wav", 330.0)])
    wait_until(lambda: len(_names(page)) == 3)
    # 排序：把「雨夜」挪到最前；删除「夜航」
    page.click("#hub [data-mu-row='2'] [data-mu='up']")
    page.click("#hub [data-mu-row='1'] [data-mu='up']")
    assert _names(page) == ["雨夜", "晚风", "夜航"]
    page.click("#hub [data-mu-row='2'] [data-mu='del']")
    wait_until(lambda: _names(page) == ["雨夜", "晚风"])
    # 循环方式：列表循环 → 单曲循环 → 随机；音量
    page.click("#hub [data-mu='mode']")
    page.click("#hub [data-mu='mode']")
    assert page.inner_text("#hub .mu-mode") == "随机"
    page.fill("#hub [data-mu-vol]", "30")
    page.dispatch_event("#hub [data-mu-vol]", "change")
    page.check("#hub [data-mu-keep]")
    # 音乐不经服务器：添加、排序、删除都没有发任何写请求
    assert posts == []
    # 刷新后列表、顺序、设置都还在（本机浏览器里）
    page.reload()
    page.wait_for_selector("#hub .mu-btn")
    _open(page)
    wait_until(lambda: _names(page) == ["雨夜", "晚风"])
    assert page.inner_text("#hub .mu-mode") == "随机"
    assert page.input_value("#hub [data-mu-vol]") == "30"
    assert page.is_checked("#hub [data-mu-keep]")
    # Esc 收起面板
    page.keyboard.press("Escape")
    assert page.is_hidden("#hub .mu-panel")


def test_music_plays_on_click_and_fades_out_when_leaving_the_hub(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .mu-btn")
    page.wait_for_timeout(300)
    assert page.locator("#hub .mu.on").count() == 0  # 默认不自动播放
    _open(page)
    page.set_input_files("#hub [data-mu-file]", [_song("晚风.wav"), _song("夜航.wav", 523.0)])
    wait_until(lambda: len(_names(page)) == 2)
    page.click("#hub .mu-panel [data-mu='play']")
    wait_until(lambda: page.locator("#hub .mu.on").count() == 1)
    assert page.inner_text("#hub .mu-btn") == "晚风"
    page.click("#hub [data-mu='next']")
    wait_until(lambda: page.inner_text("#hub .mu-btn") == "夜航")
    # 切到办公室：淡出暂停；回到终端：接着放
    page.click("#entries button[data-m='work']")
    page.wait_for_selector("#main", state="visible")
    wait_until(lambda: page.locator(".mu.on").count() == 0)
    page.click("#entries button[data-m='hub']")
    page.wait_for_selector("#hub .mu-btn")
    wait_until(lambda: page.locator("#hub .mu.on").count() == 1)
    assert page.inner_text("#hub .mu-btn") == "夜航"
    # 勾上「切到其他页面时继续播放」后，离开终端不停
    _open(page)
    page.check("#hub [data-mu-keep]")
    page.click("#entries button[data-m='work']")
    page.wait_for_selector("#main", state="visible")
    page.wait_for_timeout(900)
    assert page.locator(".mu.on").count() == 1
    # 暂停
    page.click("#entries button[data-m='hub']")
    _open(page)
    page.click("#hub .mu-panel [data-mu='play']")
    wait_until(lambda: page.locator("#hub .mu.on").count() == 0)
