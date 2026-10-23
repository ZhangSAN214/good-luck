"""工作群：办公室里的事自动写进工作群（不调用模型）；匿名未揭晓不写名字；群里交办。"""

from __future__ import annotations

from roundtable.core.chat_work import office_message, short_title
from roundtable.core.service import RoundtableService

from .orchestrator.conftest import MEDIUM, Env


async def run_office(env: Env, **kw) -> tuple[RoundtableService, str]:
    svc = RoundtableService(env.rt)
    sid = svc.create(MEDIUM, seed=2, workflow="office", **kw)
    async for _ in svc.events(sid):
        pass
    return svc, sid


def work_texts(env: Env) -> list[str]:
    return [r["text"] for r in env.rt.repo.chat_messages("work")]


async def test_office_run_posts_assignment_and_delivery_without_model_calls():
    env = Env(confirm_threshold_usd=100.0)
    svc, sid = await run_office(env)
    texts = work_texts(env)
    assert len(texts) == 2
    assigned, delivered = texts
    assert assigned.startswith("阿") and "（经理）把「求函数" in assigned
    assert assigned.endswith("派给了阿一·节电。")
    assert "交付了" in delivered and "$" in delivered
    rows = env.rt.repo.chat_messages("work")
    assert all(r["author"] == "system" and r["meta"]["session_id"] == sid for r in rows)
    # 只是记录：工作群的隐藏会话没有任何调用
    work_session = env.rt.repo.chat_session("work")
    n = env.rt.repo.conn.execute(
        "SELECT COUNT(*) FROM calls WHERE session_id = ?", (work_session,)
    ).fetchone()[0]
    assert n == 0
    # 办公室的通知算未读（普通的系统提示不算）
    work = next(r for r in svc.chat_rooms()["rooms"] if r["id"] == "work")
    assert work["unread"] == 2 and delivered.startswith(work["last"]["text"])


async def test_anonymous_office_names_nobody_before_reveal():
    env = Env(confirm_threshold_usd=100.0)
    _, sid = await run_office(env, anonymous=True)
    assigned = work_texts(env)[0]
    assert assigned.startswith("经理把") and assigned.endswith("派给了一位员工。")
    for m in env.config.models.models:
        assert m.id not in "".join(work_texts(env))
    assert "阿" not in "".join(work_texts(env))


async def test_meeting_lists_attendees_and_other_workflows_are_ignored():
    env = Env(confirm_threshold_usd=100.0)
    sid = env.rt.repo.create_session(MEDIUM, seed=1, anonymous=False, workflow="office")
    data = {"mode": "meeting", "attendees": ["阿一·节电", "阿二·节电"]}
    text = office_message(env.rt, sid, "office_assigned", data, "阿一·节电")
    assert text.startswith("经理为「") and text.endswith("召集开会：阿一·节电、阿二·节电参加。")
    data = {"mode": "project", "attendees": ["阿一·节电", "阿二·节电"]}
    assert "项目组：阿一·节电、阿二·节电" in office_message(
        env.rt, sid, "office_assigned", data, None
    )
    assert "等你拍板" in office_message(env.rt, sid, "checkpoint", {}, None)
    assert "没办成" in office_message(env.rt, sid, "failed", {}, None)
    assert office_message(env.rt, sid, "step_started", {}, None) == ""
    other = env.rt.repo.create_session(MEDIUM, seed=1, anonymous=False, workflow="discussion")
    assert office_message(env.rt, other, "completed", {}, None) == ""


def test_short_title():
    assert short_title("  一 二\n三 ") == "一 二 三"
    assert short_title("字" * 50) == "字" * 40 + "…"


async def test_handoff_from_the_work_group():
    env = Env(confirm_threshold_usd=100.0)
    svc = RoundtableService(env.rt)
    out = svc.chat_handoff("帮我写一份周报")
    async for _ in svc.events(out["session_id"]):
        pass
    rows = env.rt.repo.chat_messages("work")
    assert rows[0]["author"] == "user" and rows[0]["text"] == "帮我写一份周报"
    assert rows[0]["meta"] == {"session_id": out["session_id"], "event": "handoff"}
    assert env.rt.repo.session_row(out["session_id"])["workflow"] == "office"
    assert any("交付了" in r["text"] for r in rows[1:])
