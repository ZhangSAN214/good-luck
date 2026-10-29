"""一起看的置顶悬浮窗：和圆桌的 HTTP 往来、未读数、显示哪几条、位置与透明度的记忆（不开窗口）。"""

from __future__ import annotations

import io
import json

from roundtable.desktop.overlay import DEFAULT_STATE, MIN_ALPHA, Feed, load_state, save_state


class FakeOpener:
    def __init__(self) -> None:
        self.requests: list = []
        self.messages = [
            {"id": 1, "author": "system", "name": "系统", "text": "开始一起看"},
            {"id": 2, "author": "V1", "name": "阿一", "text": "这段好燃"},
        ]
        self.running = True

    def open(self, req, timeout=0):
        self.requests.append(req)
        url = req.full_url
        if "/api/watch/messages" in url:
            after = int(url.split("after=")[1])
            body = {"messages": [m for m in self.messages if m["id"] > after], "typing": ["阿二"]}
        elif url.endswith("/api/watch/say"):
            text = json.loads(req.data)["text"]
            self.messages.append({"id": 3, "author": "user", "name": "我", "text": text})
            body = {"id": 3, "speakers": []}
        else:
            body = {"running": self.running, "paused": "", "error": ""}
        return io.BytesIO(json.dumps(body).encode())


def test_feed_pulls_counts_unread_and_says():
    op = FakeOpener()
    feed = Feed("http://127.0.0.1:8000/", opener=op)
    assert [m["id"] for m in feed.pull()] == [1, 2]
    assert feed.unread == 1 and feed.typing == ["阿二"]  # 系统消息不算未读
    assert feed.pull() == []  # 只拉新的
    assert op.requests[-1].full_url.endswith("after=2")
    feed.say("  @阿一 哪里燃  ")
    say = op.requests[-1]
    assert say.get_method() == "POST" and json.loads(say.data) == {"text": "@阿一 哪里燃"}
    feed.pull()
    assert feed.unread == 1  # 自己说的不算未读
    assert feed.lines()[-1] == ("me", "我", "@阿一 哪里燃")
    assert feed.lines()[0] == ("sys", "系统", "开始一起看")
    n = len(op.requests)
    feed.say("   ")  # 空的不发
    assert len(op.requests) == n
    op.running = False
    feed.status()
    assert feed.running is False


def test_state_is_remembered_and_sanitized(tmp_path):
    path = tmp_path / "desktop" / "watch_overlay.json"
    assert load_state(path) == DEFAULT_STATE
    save_state(path, {"x": 10, "y": 20, "alpha": 0.05, "collapsed": True})
    state = load_state(path)
    assert state["x"] == 10 and state["collapsed"] is True and state["alpha"] == MIN_ALPHA
    path.write_text("不是 json", encoding="utf-8")
    assert load_state(path) == DEFAULT_STATE
