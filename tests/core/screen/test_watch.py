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


def test_same_prompt_and_params_for_every_watcher():
    """中立：陪看的每位成员拿到同一套提示词模板和参数。"""
    env, w, *_ = watch_env([img(0)])
    assert w.params() == {"max_tokens": 1200, "reasoning": {"effort": "low"}}
