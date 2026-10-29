"""投屏陪伴：画面来源（编码、变化判断、敏感判断、adb 输出解析）和一起看的推进。"""

from __future__ import annotations

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from roundtable.core.screen import (  # noqa: E402
    AdbSource,
    FakeScreenSource,
    ScreenUnavailable,
    ScreenWatch,
    SourceInfo,
    change_ratio,
    encode_frame,
    is_sensitive,
)

from ..orchestrator.conftest import Env  # noqa: E402


def img(color: int, size=(800, 450)) -> Image.Image:
    return Image.new("RGB", size, (color, color, color))


# ---------- 画面来源 ----------


def test_encode_frame_shrinks_and_signs():
    frame = encode_frame(img(10, (3000, 1500)), max_side=1024, quality=70)
    assert (frame.width, frame.height) == (1024, 512)
    assert frame.jpeg[:2] == b"\xff\xd8"
    assert len(frame.signature) == 32 * 18
    assert "jpeg" not in repr(frame)  # 截图内容不进日志


def test_change_ratio():
    a = encode_frame(img(0), 640, 60).signature
    b = encode_frame(img(255), 640, 60).signature
    assert change_ratio(None, a) == 1.0
    assert change_ratio(a, a) == 0.0
    assert change_ratio(a, b) > 0.9


def test_sensitive_keywords_ignore_case():
    assert is_sensitive("支付宝 - 收银台 | AlipayClient.exe", ["支付宝"]) == "支付宝"
    assert is_sensitive("My BANK online | chrome.exe", ["bank"]) == "bank"
    assert is_sensitive("B站 - 视频 | msedge.exe", ["bank", "支付宝"]) is None


class FakeAdb:
    def __init__(self, png: bytes) -> None:
        self.png = png
        self.cmds: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> bytes:
        self.cmds.append(cmd)
        if cmd[1:] == ["devices", "-l"]:
            return (
                b"List of devices attached\n"
                b"abc123 device product:x model:OnePlus_13 device:y\n"
                b"zzz unauthorized\n"
                b"192.168.1.5:5555 device model:OPPO_Pad\n"
            )
        if "dumpsys" in cmd:
            return b"  mCurrentFocus=Window{1f u0 com.eg.android.AlipayGphone/com.x.Main}\n"
        if "screencap" in cmd:
            return self.png
        raise AssertionError(cmd)


def test_adb_lists_devices_reads_foreground_and_grabs(monkeypatch, tmp_path):
    import io

    buf = io.BytesIO()
    img(80, (1080, 2400)).save(buf, "PNG")
    fake = FakeAdb(buf.getvalue())
    src = AdbSource("adb", runner=fake)
    monkeypatch.setattr(src, "_exe", lambda: "/usr/bin/adb")
    assert src.available() is None
    assert src.sources() == [
        SourceInfo("android:abc123", "android", "OnePlus 13"),
        SourceInfo("android:192.168.1.5:5555", "android", "OPPO Pad"),
    ]
    assert src.foreground("android:abc123") == "com.eg.android.AlipayGphone"
    frame = src.grab("android:abc123", 1024, 70)
    assert frame.height == 1024 and frame.foreground == "com.eg.android.AlipayGphone"
    # 只读：没有发过任何点按 / 输入
    assert not any("input" in c for c in fake.cmds)
    with pytest.raises(ScreenUnavailable):
        src.grab("android:bad serial;rm", 1024, 70)


def test_adb_missing_is_reported_not_raised(monkeypatch):
    src = AdbSource("adb-not-installed")
    assert "adb" in (src.available() or "")
    assert src.sources() == []


# ---------- 一起看 ----------


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def watch_env(images, foreground="", **rules):
    env = Env()
    if rules:
        chat = env.config.roundtable.chat
        w = chat.watch.model_copy(update=rules)
        rt = env.config.roundtable.model_copy(update={"chat": chat.model_copy(update={"watch": w})})
        env.rt.config = env.config.model_copy(update={"roundtable": rt})
    src = FakeScreenSource(images, foreground=foreground)
    events: list = []
    clock = Clock()
    from roundtable.core.chat import ChatHub

    hub = ChatHub(env.rt)
    w = ScreenWatch(env.rt, hub.everyone, {"pc": src}, publish=events.append, clock=clock)
    return env, w, src, events, clock


def is_describe(messages) -> bool:
    return "截图写成简短的文字" in messages[0].content


async def test_start_requires_consent_and_valid_watchers():
    env, w, *_ = watch_env([img(0)])
    with pytest.raises(ValueError, match="同意"):
        w.start("pc", "整个屏幕", ["V1"], consent=False)
    with pytest.raises(ValueError, match="至少选一位"):
        w.start("pc", "整个屏幕", ["nobody"], consent=True)
    with pytest.raises(ValueError, match="最多选 3 位"):
        w.start("pc", "整个屏幕", ["V1", "V2", "V3", "V4"], consent=True)
    w.start("pc", "整个屏幕", ["V1", "V3"], consent=True)
    assert w.status()["running"] and [x["id"] for x in w.status()["watchers"]] == ["V1", "V3"]
    room = env.rt.repo.chat_room(w.room)
    assert room["kind"] == "watch" and room["members"] == ["V1", "V3"]
    # 不出现在群聊列表里，也不能从群聊发言
    from roundtable.core.chat import ChatHub

    hub = ChatHub(env.rt)
    assert all(r["id"] != w.room for r in hub.rooms())
    with pytest.raises(KeyError):
        hub.room(w.room)


async def test_looks_only_when_screen_changes_and_reacts():
    env, w, src, events, clock = watch_env([img(0), img(0), img(255)])
    env.fake.queue("b2", "屏幕上是一个黑色的画面。")  # 看画面：最便宜的识图模型
    env.fake.queue("b1", "这是黑屏吗哈哈")  # V1 吐槽
    w.start("pc", "整个屏幕", ["V1"], consent=True)
    assert await w.tick() == "spoke"
    describe, speak = env.fake.calls[-2:]
    assert describe.model == "b2" and is_describe(describe.messages)
    assert describe.messages[-1].media[0].mime == "image/jpeg"
    assert speak.model == "b1" and "<screen>" in speak.messages[-1].content
    assert "黑色的画面" in speak.messages[-1].content
    assert not speak.messages[-1].media  # 成员读文字，不直接看图
    assert [e.kind for e in events] == ["perceive.screen"] and events[0].member == "V1"
    msgs = w.messages()
    assert msgs[-1]["author"] == "V1" and msgs[-1]["text"] == "这是黑屏吗哈哈"
    n = len(env.fake.calls)
    clock.t += 30
    assert await w.tick() == "same"  # 画面没变：不调用
    assert len(env.fake.calls) == n
    clock.t += 5
    assert await w.tick() == "looked"  # 变了，但离 V1 上次说话不到 90 秒：只看不说
    assert len(env.fake.calls) == n + 1
    # 花费挂在 watch 隐藏会话上，不出现在会话列表
    assert w.budget()["spent_usd"] > 0
    assert all("投屏" not in s.question for s in env.rt.repo.list_sessions())


async def test_describe_gap_and_silent_reply():
    env, w, src, events, clock = watch_env([img(0), img(255), img(0)])
    env.fake.queue("b1", "…")  # 不想说
    w.start("pc", "整个屏幕", ["V1"], consent=True)
    assert await w.tick() == "looked"
    assert [m["author"] for m in w.messages()] == ["system"]
    clock.t += 5
    assert await w.tick() == "wait"  # 变了，但离上次看不到 20 秒


async def test_quiet_mode_only_looks():
    env, w, *_ = watch_env([img(0)])
    w.start("pc", "整个屏幕", ["V1"], consent=True)
    w.set_quiet(True)
    assert await w.tick() == "looked"
    assert not any(c.model == "b1" for c in env.fake.calls)


async def test_sensitive_window_pauses_without_grabbing():
    env, w, src, *_ = watch_env([img(0)], foreground="支付宝 | Alipay.exe")
    w.start("pc", "整个屏幕", ["V1"], consent=True)
    assert await w.tick() == "paused"
    assert src.grabs == 0 and w.preview() is None and "暂停" in w.status()["paused"]
    src.fg = "哔哩哔哩 | msedge.exe"
    env.fake.queue("b1", "…")
    assert await w.tick() == "looked"
    assert w.status()["paused"] == "" and w.preview() is not None


async def test_user_says_and_watchers_reply_with_screen():
    env, w, *_ = watch_env([img(0)])
    w.start("pc", "整个屏幕", ["V1", "V2"], consent=True)
    w.set_quiet(True)
    await w.tick()
    msg, speakers = w.post("@阿二 这段怎么样")
    assert [m.id for m in speakers] == ["V2"]
    env.fake.queue("b2", "挺燃的！")
    out = await w.replies(msg["id"], speakers)
    assert out[0]["text"] == "挺燃的！" and out[0]["reply_to"] == msg["id"]
    last = env.fake.calls[-1].messages[-1].content
    assert "用户：@阿二 这段怎么样" in last and "<screen>" in last
    _, everyone = w.post("大家觉得呢")
    assert {m.id for m in everyone} == {"V1", "V2"}


async def test_failures_stop_and_budget_cap_stops():
    env, w, src, *_ = watch_env([img(0)], max_failures=2)
    w.start("pc", "整个屏幕", ["V1"], consent=True)
    src.fail = "窗口关了"
    assert await w.tick() == "failed"
    assert await w.tick() == "failed"
    assert not w.running and "自动停了" in w.messages()[-1]["text"]

    env, w, src, *_ = watch_env([img(0)], monthly_usd=0.0)
    with pytest.raises(ValueError, match="额度"):
        w.start("pc", "整个屏幕", ["V1"], consent=True)


async def test_stop_and_max_minutes():
    env, w, src, events, clock = watch_env([img(0)], max_minutes=5)
    w.start("pc", "整个屏幕", ["V1"], consent=True)
    clock.t += 6 * 60
    assert await w.tick() == "stopped"
    assert not w.running and w.preview() is None
    assert w.stop() is False
    with pytest.raises(ValueError, match="没在投屏"):
        w.post("还在吗")


async def test_describe_without_thinking_and_with_previous_and_title():
    """看画面不思考（思考会把字数花光、交回空白）；带上一段描述和当前窗口标题。"""
    env, w, src, events, clock = watch_env([img(0), img(255)])
    src.window_title = "玩《影之刃零》，看《叶问》_哔哩哔哩_bilibili"
    env.fake.queue("b2", "B站播放页，游戏画面。")
    env.fake.queue("b1", "…")
    w.start("pc:42", "哔哩哔哩 (゜-゜)つロ 干杯~-bilibili", ["V1"], consent=True)
    assert await w.tick() == "looked"
    first = next(c for c in env.fake.calls if is_describe(c.messages))
    assert first.params["reasoning"] == {"enabled": False}
    assert first.params["max_tokens"] == 800
    assert "影之刃零" in first.messages[-1].content and "这是第一张" in first.messages[-1].content
    assert w.status()["source"]["title"].startswith("玩《影之刃零》")
    speak = env.fake.calls[-1].messages[-1].content
    assert "<title>玩《影之刃零》" in speak and "<turn>画面</turn>" in speak
    assert "<voice>" in speak
    clock.t += 20
    env.fake.queue("b2", "白衣角色倒地了。")
    env.fake.queue("b1", "…")
    await w.tick()
    second = [c for c in env.fake.calls if is_describe(c.messages)][-1]
    assert "<previous>B站播放页，游戏画面。</previous>" in second.messages[-1].content


async def test_blank_describe_backs_off_instead_of_retrying_every_grab():
    env, w, src, events, clock = watch_env([img(0)], fail_gap_seconds=10, grab_seconds=3)
    env.fake.queue("b2", "", "", "屏幕上是黑色的画面。")
    w.start("pc", "整个屏幕", ["V1"], consent=True)
    w.set_quiet(True)
    assert await w.tick() == "failed"
    assert "空白" in w.status()["error"]
    n = len(env.fake.calls)
    for _ in range(3):  # 每 3 秒截一张，但 10 秒内不再看
        clock.t += 3
        assert await w.tick() == "wait"
    assert len(env.fake.calls) == n
    clock.t += 3
    assert await w.tick() == "failed"
    clock.t += 10
    assert await w.tick() == "looked"
    assert w.status()["screens"][0]["text"] == "屏幕上是黑色的画面。"


async def test_friends_pick_up_each_others_lines():
    """一位说完，别人接她的话（最多 max_followups 句）；接话的提示词标明「朋友」。"""
    env, w, src, events, clock = watch_env([img(0)], followup_chance=1.0, max_followups=2)
    env.fake.queue("b2", "屏幕上是黑色的画面。")
    w.start("pc", "整个屏幕", ["V1", "V3"], consent=True)
    v1 = next(m for m in w._watchers() if m.id == "V1")
    v3 = next(m for m in w._watchers() if m.id == "V3")
    w.rng.choice = lambda xs: v1 if v1 in xs else xs[0]  # 主动说的是 V1
    w.rng.random = lambda: 0.4  # 第二句的几率减半成 0.5，仍接
    env.fake.queue(v1.model_id, "这黑屏是故意的吧")
    env.fake.queue(v3.model_id, "我觉得是在加载")
    env.fake.queue(v1.model_id, "加载这么久也太慢了")
    assert await w.tick() == "spoke"
    said = [m for m in w.messages() if m["author"] != "system"]
    assert [m["author"] for m in said] == ["V1", "V3", "V1"]
    assert said[1]["reply_to"] == said[0]["id"] and said[2]["reply_to"] == said[1]["id"]
    last = env.fake.calls[-1].messages[-1].content
    assert "<turn>朋友</turn>" in last and "我觉得是在加载" in last


async def test_no_followup_when_quiet_or_chance_zero_or_friend_passes():
    env, w, src, events, clock = watch_env([img(0)], followup_chance=0.0)
    w.start("pc", "整个屏幕", ["V1", "V3"], consent=True)
    w.set_quiet(True)
    await w.tick()
    msg, speakers = w.post("@" + w._watchers()[0].name + " 好看吗")
    env.fake.queue(speakers[0].model_id, "好看")
    out = await w.replies(msg["id"], speakers)
    assert [m["text"] for m in out] == ["好看"]
    # 几率为 1，但朋友选择不接（…）：就停
    env, w, src, events, clock = watch_env([img(0)], followup_chance=1.0)
    w.start("pc", "整个屏幕", ["V1", "V3"], consent=True)
    w.set_quiet(True)
    await w.tick()
    first, other = w._watchers()
    msg, speakers = w.post(f"@{first.name} 好看吗")
    env.fake.queue(first.model_id, "好看")
    env.fake.queue(other.model_id, "…")
    out = await w.replies(msg["id"], speakers)
    assert [m["text"] for m in out] == ["好看"]
    assert (
        "<turn>用户</turn>"
        in [c for c in env.fake.calls if c.model == first.model_id][-1].messages[-1].content
    )


def test_same_prompt_and_params_for_every_watcher():
    """中立：陪看的每位成员拿到同一套提示词模板和参数。"""
    env, w, *_ = watch_env([img(0)])
    assert w.params() == {"max_tokens": 1200, "reasoning": {"effort": "low"}}


# ---------- 窗口列表 ----------


def test_window_list_drops_background_and_overlay_windows():
    from roundtable.core.screen.capture import (
        WS_EX_APPWINDOW,
        WS_EX_NOACTIVATE,
        WS_EX_TOOLWINDOW,
        keep_window,
    )

    normal = {"ex_style": 0, "owned": False, "cloaked": False, "width": 1280, "height": 720}
    assert keep_window("哔哩哔哩 - Microsoft Edge", **normal)
    # 用户电脑上实际出现过的后台窗口
    for title in ("no_active_wnd", "AsHotplugCtrl", "NVIDIA GeForce Overlay", "Program Manager"):
        assert not keep_window(title, **normal), title
    assert not keep_window("   ", **normal)
    assert not keep_window("XBOX", **normal | {"cloaked": True})  # 系统藏起来的后台应用
    assert not keep_window("浮层", **normal | {"ex_style": WS_EX_TOOLWINDOW})
    assert keep_window("应用", **normal | {"ex_style": WS_EX_TOOLWINDOW | WS_EX_APPWINDOW})
    assert not keep_window("叠加层", **normal | {"ex_style": WS_EX_NOACTIVATE})
    assert not keep_window("另存为", **normal | {"owned": True})
    assert not keep_window("小窗", **normal | {"width": 60, "height": 20})
    assert not keep_window("Some Helper", **normal, ignore=["helper"])  # 配置里加的


# ---------- 安卓无线调试 ----------


class WirelessAdb:
    def __init__(self, paired_ok=True, mdns=True) -> None:
        self.paired_ok = paired_ok
        self.mdns = mdns
        self.cmds: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> bytes:
        self.cmds.append(cmd)
        if cmd[1] == "pair":
            if self.paired_ok:
                return b"Successfully paired to 192.168.1.5:41234 [guid=adb-abc]\n"
            return b"Failed: Wrong password or connection was dropped.\n"
        if cmd[1:] == ["mdns", "services"]:
            if not self.mdns:
                return b"List of discovered mdns services\n"
            return (
                b"List of discovered mdns services\n"
                b"adb-abc-1\t_adb-tls-pairing._tcp.\t192.168.1.5:41234\n"
                b"adb-abc-2\t_adb-tls-connect._tcp.\t192.168.1.5:37000\n"
            )
        if cmd[1] == "connect":
            if cmd[2] == "192.168.1.9:5555":
                return b"failed to connect to 192.168.1.9:5555\n"
            return f"connected to {cmd[2]}\n".encode()
        raise AssertionError(cmd)


def test_adb_wireless_pair_and_connect(monkeypatch):
    fake = WirelessAdb()
    src = AdbSource("adb", runner=fake)
    monkeypatch.setattr(src, "_exe", lambda: "/usr/bin/adb")
    assert src.pair("192.168.1.5", 41234, "123456") == "配对成功"
    assert fake.cmds[-1] == ["/usr/bin/adb", "pair", "192.168.1.5:41234", "123456"]
    assert src.discover() == ["192.168.1.5:37000"]  # 只要连接用的那一项
    assert src.connect() == ["192.168.1.5:37000"]
    assert src.connect("192.168.1.5", 37001) == ["192.168.1.5:37001"]
    with pytest.raises(ScreenUnavailable, match="连接没成功"):
        src.connect("192.168.1.9", 5555)
    with pytest.raises(ScreenUnavailable, match="6 位数字"):
        src.pair("192.168.1.5", 41234, "12ab")
    with pytest.raises(ScreenUnavailable, match="IP 地址或端口"):
        src.pair("1.2.3.4; rm -rf", 41234, "123456")
    with pytest.raises(ScreenUnavailable, match="IP 地址或端口"):
        src.connect("192.168.1.5", 70000)
    # 只建立连接：没有任何 shell / input 命令
    assert not any("shell" in c or "input" in c for c in fake.cmds)


def test_adb_wireless_failures(monkeypatch):
    src = AdbSource("adb", runner=WirelessAdb(paired_ok=False, mdns=False))
    monkeypatch.setattr(src, "_exe", lambda: "/usr/bin/adb")
    with pytest.raises(ScreenUnavailable, match="配对没成功"):
        src.pair("192.168.1.5", 41234, "123456")
    with pytest.raises(ScreenUnavailable, match="没找到设备"):
        src.connect()
    with pytest.raises(ScreenUnavailable, match="adb"):
        AdbSource("adb-not-installed").pair("192.168.1.5", 1, "123456")
