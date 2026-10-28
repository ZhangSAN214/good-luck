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


def _accent(page) -> str:
    return page.evaluate(
        "getComputedStyle(document.querySelector('#hub .mu-panel'))"
        ".getPropertyValue('--mu-accent').trim()"
    )


def _view(page, v: str) -> None:
    """卡片里切到播放列表 / 外观；再点同一个回到播放器。"""
    page.click(f"#hub .mu-panel [data-mu='view'][data-v='{v}']")


def test_music_playlist_is_chosen_by_hand_and_remembered_locally(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    posts: list[str] = []
    page.on("request", lambda r: r.method != "GET" and posts.append(r.url))
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .mu-btn")
    assert page.inner_text("#hub .mu-label") == "音乐"
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
    _view(page, "list")
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
    page.check("#hub [data-mu-keep]")
    # 循环方式：列表循环 → 单曲循环 → 随机；音量
    _view(page, "player")
    page.click("#hub [data-mu='mode']")
    page.click("#hub [data-mu='mode']")
    assert page.inner_text("#hub .mu-mode") == "随机"
    page.fill("#hub [data-mu-vol]", "30")
    page.dispatch_event("#hub [data-mu-vol]", "change")
    # 音乐不经服务器：添加、排序、删除都没有发任何写请求
    assert posts == []
    # 刷新后列表、顺序、设置都还在（本机浏览器里）
    page.reload()
    page.wait_for_selector("#hub .mu-btn")
    _open(page)
    assert page.inner_text("#hub .mu-mode") == "随机"
    assert page.input_value("#hub [data-mu-vol]") == "30"
    _view(page, "list")
    wait_until(lambda: _names(page) == ["雨夜", "晚风"])
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
    wait_until(lambda: page.inner_text("#hub .mu-now") in ("点播放开始", "晚风"))
    page.click("#hub .mu-panel [data-mu='play']")
    wait_until(lambda: page.locator("#hub .mu.on").count() == 1)
    assert page.inner_text("#hub .mu-label") == "晚风"
    page.click("#hub [data-mu='next']")
    wait_until(lambda: page.inner_text("#hub .mu-label") == "夜航")
    # 切到办公室：淡出暂停；回到终端：接着放
    page.click("#entries button[data-m='work']")
    page.wait_for_selector("#main", state="visible")
    wait_until(lambda: page.locator(".mu.on").count() == 0)
    page.click("#entries button[data-m='hub']")
    page.wait_for_selector("#hub .mu-btn")
    wait_until(lambda: page.locator("#hub .mu.on").count() == 1)
    assert page.inner_text("#hub .mu-label") == "夜航"
    # 勾上「切到其他页面时继续播放」后，离开终端不停
    _open(page)
    _view(page, "list")
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


def _png() -> bytes:
    """一张 1×1 的 png。"""
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


def test_music_follows_qq_music_on_this_computer(serve, page, tmp_path):
    """QQ 音乐：显示电脑上正在放的歌、封面、进度；按钮经系统媒体控制发给播放器。"""
    from roundtable.core.nowplaying import FakeMediaSource, Playing

    src = FakeMediaSource(
        [
            Playing(app_id="Chrome", title="浏览器里的视频", status="playing"),
            Playing(
                app_id="QQMusic.exe",
                title="夜航星",
                artist="不才",
                album="夜航星",
                status="paused",
                position=61.0,
                duration=240.0,
                can=frozenset({"play", "pause", "next", "prev", "seek"}),
                cover=_png(),
            ),
        ]
    )
    covers = tmp_path / "music_covers"
    covers.mkdir()
    (covers / "cover_claude.png").write_bytes(_png())

    def setup(env):
        env.rt.nowplaying_source = src
        env.rt.standee_file = tmp_path / "standee.json"

    srv = serve(confirm_threshold_usd=100.0, setup=setup)
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .mu-btn")
    _open(page)
    page.click("#hub .mu-seg [data-v='system']")
    wait_until(lambda: page.inner_text("#hub .mu-now") == "夜航星")
    assert "不才" in page.inner_text("#hub .mu-sub")
    assert page.inner_text("#hub .mu-cur") == "1:01"
    assert page.inner_text("#hub .mu-dur") == "4:00"
    assert "/api/nowplaying/cover" in page.get_attribute("#hub .mu-art img", "src")
    # 默认只跟 QQ 音乐：浏览器里在放什么不显示
    assert "浏览器里的视频" not in page.inner_text("#hub .mu-panel")
    page.click("#hub .mu-panel [data-mu='play']")
    wait_until(lambda: ("QQMusic.exe", "play", None) in src.calls)
    wait_until(lambda: page.locator("#hub .mu.on").count() == 1)
    assert page.inner_text("#hub .mu-label") == "夜航星"
    page.click("#hub .mu-panel [data-mu='next']")
    wait_until(lambda: ("QQMusic.exe", "next", None) in src.calls)
    # 连接设置里说明要做什么
    _view(page, "list")
    assert "QQ 音乐桌面版" in page.inner_text("#hub .mu-panel")
    assert "检测到：QQ 音乐" in page.inner_text("#hub .mu-panel")
    # 刷新后还是 QQ 音乐
    page.reload()
    page.wait_for_selector("#hub .mu-btn")
    wait_until(lambda: page.inner_text("#hub .mu-label") == "夜航星")


def test_music_card_colors_text_and_cover_are_customizable(serve, page, tmp_path):
    covers = tmp_path / "music_covers"
    covers.mkdir()
    (covers / "cover_claude.png").write_bytes(_png())
    (covers / "effects.json").write_text('{"claude": {"fx": "petals"}}', encoding="utf-8")
    srv = serve(
        confirm_threshold_usd=100.0,
        setup=lambda env: setattr(env.rt, "standee_file", tmp_path / "standee.json"),
    )
    posts: list[str] = []
    page.on("request", lambda r: r.method != "GET" and posts.append(r.url))
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .mu-btn")
    _open(page)
    _view(page, "skin")
    page.click("#hub .mu-preset[data-v='night']")
    style = _accent(page)
    assert style == "#8eb0e4"
    page.fill("#hub [data-mu-color='accent']", "#c0392b")
    page.dispatch_event("#hub [data-mu-color='accent']", "change")
    page.fill("#hub [data-mu-tag]", "晚安电台")
    page.click("#hub .mu-thumb[title='claude']")
    _view(page, "skin")  # 回到播放器
    assert page.inner_text("#hub .mu-tag") == "晚安电台"
    assert "/api/music/covers/cover_claude.png" in page.get_attribute("#hub .mu-art img", "src")
    assert _accent(page) == "#c0392b"
    # 成员封面按 effects.json 飘花瓣、头发那层会摆；外观里可以关掉
    assert page.locator("#hub .mu-art .mu-fx").count() == 1
    assert page.locator("#hub .mu-art .mu-hair").count() == 1
    wait_until(lambda: page.evaluate("document.querySelector('#hub .mu-fx').width") > 0)
    _view(page, "skin")
    page.select_option("#hub [data-mu-fx]", "off")
    _view(page, "skin")
    assert page.locator("#hub .mu-art .mu-hair").count() == 0
    assert posts == []  # 外观只存本机浏览器
    page.reload()
    page.wait_for_selector("#hub .mu-btn")
    _open(page)
    assert _accent(page) == "#c0392b"
    assert page.inner_text("#hub .mu-tag") == "晚安电台"


def test_music_mini_card_sits_under_the_clock_and_can_collapse(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .hb-lead .mu-mini")
    clock = page.locator("#hub .hb-lead .ck").bounding_box()
    mini = page.locator("#hub .mu-mini").bounding_box()
    assert mini["y"] >= clock["y"] + clock["height"]  # 在时钟下面
    assert abs(mini["x"] - clock["x"]) < 2
    assert abs(mini["width"] - clock["width"]) < 2  # 同宽、右边也对齐
    # 默认墨蓝：跟首页卡片同一层玻璃
    assert "--mu-bg:var(--hb-glass" in page.get_attribute("#hub .mu-mini", "style")
    # 小卡上的播放键直接放
    page.set_input_files("#hub [data-mu-file]", [_song("晚风.wav")])
    wait_until(lambda: page.inner_text("#hub .mu-label") == "晚风")
    page.click("#hub .mu-mini [data-mu='play']")
    wait_until(lambda: page.locator("#hub .mu.on").count() == 1)
    # 收起成小按钮，刷新后还记得；再展开
    page.click("#hub .mu-mini [data-mu='dock']")
    assert page.locator("#hub .mu-mini").count() == 0
    page.reload()
    page.wait_for_selector("#hub .mu-btn")
    assert page.locator("#hub .mu-mini").count() == 0
    page.click("#hub .mu [data-mu='dock']")
    page.wait_for_selector("#hub .mu-mini")
    # 点小卡打开完整播放器
    page.click("#hub .mu-mini .mu-btn")
    page.wait_for_selector("#hub .mu-panel", state="visible")
    page.wait_for_timeout(450)  # 等展开动画放完再量
    panel = page.locator("#hub .mu-panel").bounding_box()
    mini = page.locator("#hub .mu-mini").bounding_box()
    assert abs(panel["x"] - mini["x"]) < 2 and abs(panel["width"] - mini["width"]) < 2


def test_music_card_fits_the_window_and_follows_resizes(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.add_init_script(LAND_HUB)
    for w, h in ((1400, 900), (1280, 720), (1920, 1080)):
        page.set_viewport_size({"width": w, "height": h})
        page.goto(srv.url)
        page.wait_for_selector("#hub .mu-mini")
        page.click("#hub .mu-mini .mu-btn")
        page.wait_for_selector("#hub .mu-panel", state="visible")
        # 等展开动画放完（慢机器上 450ms 不一定够）
        wait_until(
            lambda: page.evaluate(
                "document.querySelector('#hub .mu-panel').getAnimations().length === 0"
            )
        )
        page.wait_for_timeout(100)
        box = page.locator("#hub .mu-panel").bounding_box()
        assert box["y"] + box["height"] <= h  # 不超出窗口底部
        # 整张卡片放得下，不出滚动条
        assert page.evaluate(
            "(e => e.scrollHeight <= e.clientHeight + 1)(document.querySelector('#hub .mu-panel'))"
        )
        clock = page.locator("#hub .hb-lead .ck").bounding_box()
        assert abs(box["x"] - clock["x"]) < 2 and abs(box["width"] - clock["width"]) < 2
    # 窗口变矮，封面跟着变小
    art = page.locator("#hub .mu-art").bounding_box()["height"]
    page.set_viewport_size({"width": 1920, "height": 760})
    page.wait_for_timeout(200)
    assert page.locator("#hub .mu-art").bounding_box()["height"] < art - 50
