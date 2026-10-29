"""事件总线：订阅（全部 / 前缀 / 精确）、出错隔离、存库、补算、清理。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.member.bus import Event, EventBus, matches, perception

from ..orchestrator.conftest import Env

NOW = datetime(2026, 10, 29, 10, tzinfo=UTC)


def test_matches():
    assert matches("*", "life.event")
    assert matches("life.*", "life.event") and not matches("life.*", "chat.message")
    assert matches("chat.message", "chat.message") and not matches("chat", "chat.message")


def test_publish_delivers_isolates_errors_and_persists():
    env = Env()
    bus = EventBus(env.rt)
    got: list[str] = []
    bus.subscribe("life.*", lambda e: got.append(f"life:{e.member}"))
    bus.subscribe("*", lambda e: got.append(f"all:{e.kind}"))

    def boom(e):
        raise RuntimeError("坏模块")

    bus.subscribe("life.event", boom)
    assert bus.publish(Event("life.event", NOW, "V1", {"place": "canteen"})) == 2
    assert got == ["life:V1", "all:life.event"]
    events = bus.replay(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert [(e.kind, e.member, e.data) for e in events] == [
        ("life.event", "V1", {"place": "canteen"})
    ]


def test_unsubscribe_and_replay_filter():
    env = Env()
    bus = EventBus(env.rt)
    got = []
    off = bus.subscribe("*", got.append)
    off()
    bus.publish(Event("chat.message", NOW, None))
    bus.publish(Event("life.event", NOW, "V2"))
    assert got == []
    span = (NOW - timedelta(minutes=1), NOW + timedelta(minutes=1))
    assert [e.kind for e in bus.replay(*span, kind="life.*")] == ["life.event"]


def test_prune_old_events():
    env = Env()
    bus = EventBus(env.rt)
    bus.publish(Event("old", NOW - timedelta(days=30)))
    bus.publish(Event("new", NOW))
    assert bus.prune(NOW) == 1
    assert [e.kind for e in bus.replay(NOW - timedelta(days=60), NOW + timedelta(days=1))] == [
        "new"
    ]


def test_perception_event_shape():
    env = Env()
    bus = EventBus(env.rt)
    got = []
    bus.subscribe("perceive.*", got.append)
    bus.publish(perception("video", NOW, "V1", "看了一段猫的视频", "https://example.test/v"))
    assert got[0].kind == "perceive.video" and got[0].member == "V1"
    assert got[0].data == {"card": "看了一段猫的视频", "source": "https://example.test/v"}
