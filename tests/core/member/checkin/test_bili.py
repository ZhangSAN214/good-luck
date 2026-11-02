"""B 站：下载取帧让成员「看过」；按额度自动写私信 / 动态，只存本机；一键总开关；不发到外部网站。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from roundtable.core.chat import ChatHub
from roundtable.core.life import LifeEngine
from roundtable.core.member.checkin import BiliUnavailable, Fetched, store
from roundtable.core.member.checkin import bili as bl
from roundtable.core.memory import MemoryKeeper
from roundtable.core.wander.wander import wander_room

from ...orchestrator.conftest import Env
from ...perception.media import make_video

NOW = datetime(2026, 11, 7, 20, 0, tzinfo=UTC)
BV = "BV1xx411c7mD"
URL = f"https://www.bilibili.com/video/{BV}"
CARD_NO = json.dumps({"can": False, "reject": "文字页"}, ensure_ascii=False)
SHARE = json.dumps(
    {"dm": "这个你肯定喜欢，海边那段特别安静", "post": "看完想去海边了"}, ensure_ascii=False
)


def setup(tmp_path, fetch=None):
    env = Env(with_media=True)
    env.rt.chat_media_dir = tmp_path / "media"
    fetched: list[str] = []

    async def fake_fetch(url: str) -> Fetched:
        fetched.append(url)
        return Fetched(make_video(4, audio=True), "海边小镇一日", 4.0)

    env.rt.bili_fetch = fetch or fake_fetch
    hub = ChatHub(env.rt)
    life = LifeEngine(env.rt, hub, MemoryKeeper(env.rt, hub.everyone))
    return env, life.bili, fetched


def visited(env: Env, member: str, url: str = URL, title: str = "海边小镇一日") -> None:
    env.rt.repo.ensure_chat_room(
        wander_room(member), kind="wander", title=f"{member}上网", members=[member]
    )
    meta = {"wander": {"trip": 1, "kind": "page", "site": "B站", "url": url, "title": title}}
    env.rt.repo.add_chat_message(wander_room(member), member, "看看", meta=meta)


def test_bv_and_url():
    assert bl.bv_of(URL + "?p=1") == BV and bl.bv_of("https://b23.tv/" + BV) == BV
    assert bl.bv_of("https://www.bilibili.com/game") == ""


async def test_watching_keeps_frames_and_what_she_saw_and_writes_local_dm_and_post(tmp_path):
    env, bili, fetched = setup(tmp_path)
    env.fake.queue("b1", CARD_NO, SHARE)
    rec = await bili.watch("V1", URL, NOW)
    assert fetched == [URL]
    assert rec["title"] == "海边小镇一日" and rec["seen"]  # 感官看过（宫格图 + 转写）
    assert len(rec["frames"]) == bl.FRAMES and all(f["id"] for f in rec["frames"])
    view = bili.view()
    assert view["external"] is False and view["auto"] is True
    kinds = {o["kind"]: o for o in view["outbox"]}
    assert set(kinds) == {"dm", "post"}
    assert kinds["dm"]["url"] == URL and kinds["dm"]["external"] is False
    # 记了一件生活小事（朋友圈、日记、记忆接着用）
    events = env.rt.repo.life_events_between("2026-11-07T00:00:00", "2026-11-08T00:00:00")
    assert any("刷B站看了个视频" in e["topic"] for e in events)
    # 看视频和写私信的花费都在 travel 步骤（算生活每日上限）
    steps = {r[0] for r in env.rt.repo.conn.execute("SELECT step FROM calls").fetchall()}
    assert steps == {"travel"}


async def test_auto_watches_only_what_she_browsed_today_within_quota(tmp_path):
    env, bili, fetched = setup(tmp_path)
    for m in ("V1", "V2", "V3"):
        visited(env, m, url=f"https://www.bilibili.com/video/BV1xx411c7m{m[-1]}")
    visited(env, "V1", url="https://www.bilibili.com/read/123")  # 不是视频页
    now = datetime.fromisoformat(env.rt.repo.clock())  # 逛的记录按数据库的时钟记
    n = await bili.tick(now)
    assert n == bl.WATCH_PER_DAY == len(fetched)
    assert await bili.tick(now) == 0  # 今天的额度用完了


async def test_kill_switch_stops_auto_watching_and_sharing(tmp_path):
    env, bili, fetched = setup(tmp_path)
    visited(env, "V1")
    store.set_switch(env.rt, store.AUTO_KEY, False)
    assert await bili.tick(datetime.fromisoformat(env.rt.repo.clock())) == 0 and not fetched
    # 你手动让她看的照样能看，但不自动写私信 / 动态
    env.fake.queue("b1", CARD_NO)
    rec = await bili.watch("V1", URL, NOW)
    assert rec and not bili.view()["outbox"]


async def test_dm_and_post_quota_per_member_per_day(tmp_path):
    env, bili, _ = setup(tmp_path)
    env.fake.queue("b1", CARD_NO, SHARE, CARD_NO, SHARE)
    await bili.watch("V1", URL, NOW)
    await bili.watch("V1", "https://www.bilibili.com/video/BV1xx411c7mE", NOW)
    out = bili.view()["outbox"]
    assert sorted(o["kind"] for o in out) == ["dm", "post"]  # 每位每天各 1 条


async def test_no_ytdlp_or_no_money_means_nothing_happens(tmp_path):
    async def broken(url: str) -> Fetched:
        raise BiliUnavailable("电脑上没有 yt-dlp")

    env, bili, _ = setup(tmp_path, fetch=broken)
    assert await bili.watch("V1", URL, NOW) is None and not env.fake.calls
    env2, bili2, fetched = setup(tmp_path)
    bili2.allow = lambda usd: False
    assert await bili2.watch("V1", URL, NOW) is None and not fetched


def test_bili_code_never_posts_to_external_sites():
    """私信和动态只存本机：B 站模块里没有登录、发消息、发动态的接口地址。"""
    from pathlib import Path

    text = Path(bl.__file__).read_text(encoding="utf-8")
    for word in (
        "send_msg",
        "upload_bfs",
        "dynamic/create",
        "api.vc.bilibili",
        "cookie",
        "SESSDATA",
    ):
        assert word not in text


def test_checkin_and_bili_stay_out_of_work_code():
    import re
    from pathlib import Path

    src = Path(bl.__file__).resolve().parents[3]
    pattern = re.compile(r"member\.checkin|member_checkin|member_bili")
    for pkg in ("orchestrator", "steps", "routing", "allocation", "prompts", "office", "budget"):
        for f in (src / "core" / pkg).rglob("*.py"):
            assert not pattern.search(f.read_text(encoding="utf-8")), f
