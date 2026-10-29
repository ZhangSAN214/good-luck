"""P8 第二块：你的状态（正在放的歌、电脑空闲、在玩游戏、课表 DDL、邮件栏）。每项默认关；
打开了才在私聊、生活群里带一句（8 位相同字数）；工作模式不挂载；读到的不写库。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.chat import WORK, ChatHub, dm_room, ensure_default_rooms
from roundtable.core.mail.box import ENABLED_KEY
from roundtable.core.member import WorkModeError
from roundtable.core.member.about import STATUS_KINDS, AboutError, AboutMissing, AboutYou
from roundtable.core.nowplaying import FakeMediaSource, Playing
from roundtable.core.service import RoundtableService
from roundtable.core.yourpc import FakePc, game_of

from ..orchestrator.conftest import MEDIUM, Env

MON_10 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)  # 星期一


def service(idle=0.0, fg="", playing="playing", **kw) -> tuple[Env, RoundtableService]:
    env = Env(**kw)
    env.rt.pc_source = FakePc(idle, fg)
    env.rt.nowplaying_source = FakeMediaSource(
        [Playing(app_id="QQMusic.exe", title="夜航星", artist="不才", status=playing)]
    )
    return env, RoundtableService(env.rt)


def turn_on(svc, *kinds):
    for k in kinds:
        svc.engine.about.set_status(k, True)


def test_everything_is_off_by_default():
    env, svc = service(idle=3600, fg=r"C:\Games\Genshin Impact\YuanShen.exe")
    assert all(v is False for v in svc.engine.about.switches()["status"].values())
    assert set(svc.engine.about.switches()["status"]) == set(STATUS_KINDS)
    assert env.rt.user_status.line(now=MON_10) == ""
    view = env.rt.user_status.view(MON_10)
    assert not any(i["on"] for i in view["items"])


def test_music_only_when_playing():
    env, svc = service()
    turn_on(svc, "music")
    assert "用户正在听《夜航星》（不才）" in env.rt.user_status.line(now=MON_10)
    env, svc = service(playing="paused")
    turn_on(svc, "music")
    assert env.rt.user_status.line(now=MON_10) == ""


def test_idle_and_game():
    env, svc = service(idle=15 * 60, fg=r"C:\Program Files\Genshin Impact\YuanShen.exe")
    turn_on(svc, "idle", "game")
    line = env.rt.user_status.line(now=MON_10)
    assert "15 分钟没人动了" in line and "用户在玩原神" in line
    env.rt.pc_source.state = type(env.rt.pc_source.state)(30, r"C:\Program Files\Chrome\chrome.exe")
    line = env.rt.user_status.line(now=MON_10)
    assert "用户正在用电脑" in line and "chrome" not in line.lower() and "在玩" not in line


def test_game_table_and_store_folders():
    games = {"YuanShen.exe": "原神"}
    assert game_of(r"D:\x\yuanshen.EXE", games) == "原神"
    assert game_of(r"D:\Steam\steamapps\common\Hades II\Hades2.exe", games) == "Hades II"
    assert game_of(r"C:\Program Files\Epic Games\Fortnite\bin\F.exe", games) == "Fortnite"
    assert game_of(r"C:\Windows\explorer.exe", games) is None
    assert game_of("", games) is None
    about = AboutYou(Env().rt)
    about.add_game("MyGame.exe", "我的游戏")
    assert about.games()["MyGame.exe"] == "我的游戏"
    with pytest.raises(AboutError):
        about.add_game(r"C:\x\a.exe", "带路径")
    about.del_game("MyGame.exe")
    with pytest.raises(AboutMissing):
        about.del_game("MyGame.exe")


def test_pc_unavailable_says_nothing_and_shows_reason():
    env = Env()
    env.rt.pc_source = FakePc(reason="只在 Windows 电脑上能读")
    svc = RoundtableService(env.rt)
    turn_on(svc, "idle", "game")
    assert env.rt.user_status.line(now=MON_10) == ""
    idle = next(i for i in env.rt.user_status.view(MON_10)["items"] if i["kind"] == "idle")
    assert idle["reason"] == "只在 Windows 电脑上能读" and idle["on"]


def test_timetable_and_ddl():
    env, svc = service()
    about = svc.engine.about
    about.add_class(0, "9:00", "10:50", "线性代数")
    about.add_class(0, "14:00", "15:50", "数据结构")
    about.add_ddl("操作系统作业", "2026-11-04T23:59")
    about.add_ddl("早就过了", "2026-10-01")
    about.add_ddl("太远了", "2026-12-30")
    turn_on(svc, "timetable")
    line = env.rt.user_status.line(now=MON_10)
    assert "用户正在上「线性代数」（10:50 下课）" in line
    assert "「操作系统作业」DDL 还有 2 天" in line
    assert "早就过了" not in line and "太远了" not in line
    noon = MON_10.replace(hour=12)
    assert "用户今天 14:00 还有「数据结构」" in env.rt.user_status.line(now=noon)
    for bad in ((7, "9:00", "10:00", "x"), (0, "9点", "10:00", "x"), (0, "10:00", "9:00", "x")):
        with pytest.raises(AboutError):
            about.add_class(*bad)
    with pytest.raises(AboutError):
        about.add_ddl("x", "下周三")
    cid = about.schedule()["classes"][0]["id"]
    about.del_schedule("class", cid)
    assert len(about.schedule()["classes"]) == 1
    assert about.clear_schedule() == 4
    assert about.schedule()["classes"] == [] and about.schedule()["ddls"] == []


def test_mail_needs_mail_bar_on():
    env, svc = service()
    turn_on(svc, "mail")
    assert env.rt.user_status.line(now=MON_10) == ""
    item = next(i for i in env.rt.user_status.view(MON_10)["items"] if i["kind"] == "mail")
    assert "邮件栏还没打开" in item["reason"]
    env.rt.repo.set_setting(ENABLED_KEY, "1")
    assert "用户邮箱今天收到 0 封，重要未读 0 封" in env.rt.user_status.line(now=MON_10)


def test_status_budget_and_work_mode():
    env, svc = service(idle=3600, fg=r"D:\Steam\steamapps\common\Some Very Long Game Name\g.exe")
    svc.engine.about.add_class(0, "9:00", "10:50", "一门名字很长很长很长很长的课")
    svc.engine.about.add_ddl("名字也很长很长很长很长的作业", "2026-11-03")
    turn_on(svc, *STATUS_KINDS)
    line = env.rt.user_status.line(now=MON_10)
    body = line.split("\n", 1)[1]
    assert len(body) <= svc.engine.about.rules.status_chars
    with pytest.raises(WorkModeError):
        env.rt.user_status.line(mode="work")


async def test_dm_prompt_carries_status_work_room_does_not():
    env, svc = service()
    ensure_default_rooms(env.rt)
    hub = ChatHub(env.rt)
    dm = hub.room(dm_room("V1"))
    msg, speakers = dm.post("在干嘛")
    await dm.replies(msg["id"], speakers)
    assert "夜航星" not in env.fake.calls[-1].messages[-1].content  # 默认关
    turn_on(svc, "music")
    n = len(env.fake.calls)
    msg, speakers = dm.post("你听歌吗")
    await dm.replies(msg["id"], speakers)
    assert any("用户正在听《夜航星》" in c.messages[-1].content for c in env.fake.calls[n:])
    n = len(env.fake.calls)
    work = hub.room(WORK)
    msg, speakers = work.post("在干嘛")
    await work.replies(msg["id"], speakers)
    assert all("夜航星" not in c.messages[-1].content for c in env.fake.calls[n:])


@pytest.mark.parametrize("workflow", ["discussion", "office", "solo", "collab"])
async def test_status_never_reaches_work_prompts(workflow):
    env, svc = service(confirm_threshold_usd=100.0)
    turn_on(svc, *STATUS_KINDS)
    sid = svc.create(MEDIUM, seed=3, workflow=workflow)
    async for _ in svc.events(sid):
        pass
    assert env.fake.calls
    sent = json.dumps([[m.content for m in c.messages] for c in env.fake.calls], ensure_ascii=False)
    assert "夜航星" not in sent and "用户此刻" not in sent


def test_status_api():
    env, svc = service()
    with TestClient(create_app(svc)) as c:
        v = c.get("/api/chat/about/status").json()
        assert [i["kind"] for i in v["items"]] == list(STATUS_KINDS)
        v = c.post("/api/chat/about/status", json={"kind": "music", "on": True}).json()
        assert next(i for i in v["items"] if i["kind"] == "music")["preview"].startswith(
            "用户正在听"
        )
        assert "夜航星" in v["line"]
        assert c.post("/api/chat/about/status", json={"kind": "x", "on": True}).status_code == 400
        assert c.post("/api/chat/about/status/player", json={"player": "spotify"}).json()
        assert c.post("/api/chat/about/status/player", json={"player": "x"}).status_code == 400
        v = c.post("/api/chat/about/games", json={"exe": "A.exe", "name": "甲"}).json()
        assert v["games"] == {"A.exe": "甲"}
        assert c.delete("/api/chat/about/games", params={"exe": "A.exe"}).json()["games"] == {}
        body = {"weekday": 0, "start": "09:00", "end": "10:00", "name": "高数"}
        v = c.post("/api/chat/about/schedule/classes", json=body).json()
        (cls,) = v["schedule"]["classes"]
        v = c.post("/api/chat/about/schedule/ddls", json={"name": "作业", "due": "2026-11-03"})
        assert v.json()["schedule"]["ddls"][0]["due"] == "2026-11-03"
        assert c.delete(f"/api/chat/about/schedule/class/{cls['id']}").status_code == 200
        assert c.delete("/api/chat/about/schedule/class/999").status_code == 404
        assert c.delete("/api/chat/about/schedule").json()["deleted"] == 1
