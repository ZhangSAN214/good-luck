"""办公室模式匿名开启时：开会、项目分工在揭晓前，事件、会话详情、工资单和发给模型的内容
都不含模型 id、厂商名、渠道名或昵称；揭晓后才显示。"""

from __future__ import annotations

import json

import pytest

from .test_app import MEDIUM, client, leaks, sse_events


def nicknames(env) -> list[str]:
    return [n for n in env.config.personas.nicknames.values() if n]


@pytest.mark.parametrize("mode", ["meeting", "project"])
def test_office_group_modes_do_not_leak_identity_before_reveal(mode):
    env, c = client(confirm_threshold_usd=100.0)
    env.office_mode = mode
    with c:
        sid = c.post(
            "/api/sessions",
            json={"question": MEDIUM, "seed": 4, "anonymous": True, "workflow": "office"},
        ).json()["session_id"]
        events = sse_events(c, sid)
        assert events[-1]["status"] == "completed", events[-1]
        session = c.get(f"/api/sessions/{sid}")
        payslip = c.get(f"/api/sessions/{sid}/payslip")
        everything = json.dumps(events, ensure_ascii=False) + session.text + payslip.text
        assert leaks(env, everything) == []
        assert not [n for n in nicknames(env) if n in everything]
        kinds = {o["kind"] for o in session.json()["outputs"]}
        assert ("office_speech" in kinds) if mode == "meeting" else ("office_review" in kinds)
        # 发给模型的每条消息也不含别人的身份（题目本身不含这些名称；Fake 的回答带着自己的 id，
        # 自己的稿子回到自己手里时不遮蔽）
        for call in env.fake.calls:
            sent = "\n".join(m.content for m in call.messages)
            assert [t for t in leaks(env, sent) if t != call.model] == [], call.model
            assert not [n for n in nicknames(env) if n in sent]

        revealed = c.post(f"/api/sessions/{sid}/reveal")
        assert revealed.status_code == 200 and leaks(env, revealed.text)
