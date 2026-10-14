"""头脑风暴（R16）：发散 → 拓展 → 整合成思维导图。"""

from __future__ import annotations

import json
import re

from roundtable.core.routing import Question, UserChoice
from roundtable.core.steps import final_answer, restore_state
from roundtable.core.steps.brain_schemas import (
    Idea,
    parse_expansions,
    parse_ideas,
    parse_mindmap,
)

from .conftest import Env
from .test_media import script

TOPIC = "如何降低钢件淬火开裂的风险？帮我拓展思路"
IDEATE, EXPAND, ORGANIZE = (
    "其他人这一轮看不到你的想法",
    "请在它们的基础上继续拓展思路",
    "你是头脑风暴的统筹",
)


def ideate_reply(model, msgs):
    code = re.search(r"代号「(.+?)」", msgs[0].content).group(1)
    ideas = [{"title": f"{code}的想法{n}", "detail": "说明"} for n in (1, 2)]
    return json.dumps({"ideas": ideas}, ensure_ascii=False)


def expand_reply(model, msgs):
    ids = re.findall(r'<idea id="(I\d+)">', msgs[-1].content)
    return json.dumps(
        {
            "items": [
                {"on": ids[0], "kind": "build", "text": "补充具体做法"},
                {"on": "I999", "kind": "build", "text": "无效编号"},
            ]
        },
        ensure_ascii=False,
    )


def organize_reply(model, msgs):
    return json.dumps(
        {
            "title": "降低开裂",
            "branches": [{"name": "工艺", "ideas": [{"text": "整合想法", "refs": ["I1", "I3"]}]}],
            "next_steps": ["先小试"],
        },
        ensure_ascii=False,
    )


def make(organize=organize_reply, **kw) -> Env:
    env = Env(confirm_threshold_usd=100.0, **kw)
    script(env, **{IDEATE: ideate_reply, EXPAND: expand_reply, ORGANIZE: organize})
    return env


async def run(env, anonymous=False):
    return await env.orc.start(
        Question(TOPIC), UserChoice("budget", workflow="brainstorm"), seed=3, anonymous=anonymous
    )


# --- 解析 ------------------------------------------------------------------------


def test_parse_ideas_caps_and_rejects_empty():
    text = json.dumps({"ideas": [{"title": f"t{i}"} for i in range(9)] + [{"title": " "}]})
    assert len(parse_ideas(text, 4)) == 4
    try:
        parse_ideas('{"ideas": []}', 4)
    except ValueError:
        pass
    else:
        raise AssertionError("空想法应当报错")


def test_parse_expansions_drops_unknown_ids_and_keeps_new_ideas():
    text = json.dumps(
        {
            "items": [
                {"on": "i2", "kind": "challenge", "text": "有风险"},
                {"on": "I9", "kind": "build", "text": "编号不存在"},
                {"on": "", "kind": "new", "text": "全新的"},
                {"on": "I1", "kind": "乱写", "text": "种类不认识按深化"},
                {"on": "I1", "kind": "build", "text": ""},
            ]
        }
    )
    got = parse_expansions(text, ["I1", "I2"], 5)
    assert [(x.on, x.kind) for x in got] == [("I2", "challenge"), ("", "new"), ("I1", "build")]
    assert len(parse_expansions(text, ["I1", "I2"], 1)) == 1


def test_parse_mindmap_maps_refs_to_author_labels():
    text = organize_reply(None, None)
    m = parse_mindmap(
        text, {"I1": "a", "I3": "b"}, lambda c: f"「{c}」", max_branches=3,
        max_ideas_per_branch=3, max_next_steps=1,
    )  # fmt: skip
    assert m.branches[0].ideas[0].sources == ("「a」", "「b」") and m.next_steps == ("先小试",)


# --- 全流程 ------------------------------------------------------------------------


async def test_full_brainstorm_run_produces_mindmap_and_final_answer():
    env = make()
    r = await run(env)
    assert r.status == "completed"
    table = env.rt.repo.tables(r.session_id)[0]
    assert table["pipeline"] == ["ideate", "expand", "organize", "reveal"]
    members = set(table["members"])
    kinds = {
        k: [o for o in env.rt.repo.outputs(r.session_id) if o["kind"] == k]
        for k in ("ideas", "expansions", "mindmap")
    }
    assert (
        len(kinds["ideas"]) == len(members) == len(kinds["expansions"])
        and len(kinds["mindmap"]) == 1
    )
    saved = json.loads(kinds["mindmap"][0]["content"])
    assert {"map", "markdown", "mermaid", "svg"} <= set(saved) and saved["svg"].startswith("<svg")
    # 最终答案 = 大纲 + Mermaid
    assert "# 降低开裂" in r.final_answer and "```mermaid" in r.final_answer
    # 统筹不作答、不提想法
    assert table["coordinator"] not in {
        c.model for c in env.fake.calls if IDEATE in c.messages[0].content
    }


async def test_expand_never_shows_members_their_own_ideas_and_all_get_same_request_shape():
    env = make()
    r = await run(env)
    calls = [c for c in env.fake.calls if EXPAND in c.messages[0].content]
    assert len(calls) == 2  # 便宜档 3 个模型：1 个统筹 + 2 位组员
    for c in calls:
        mine = re.search(r"代号「(.+?)」", c.messages[0].content).group(1)
        assert f"{mine}的想法" not in c.messages[-1].content
    assert len({json.dumps(c.params, sort_keys=True) for c in calls}) == 1
    assert r.status == "completed"


async def test_expand_attributions_survive_in_organize_input_and_invalid_expansion_dropped():
    env = make()
    r = await run(env)
    org = next(c for c in env.fake.calls if ORGANIZE in c.messages[0].content)
    text = org.messages[-1].content
    assert 'kind="深化"' in text and "补充具体做法" in text and "无效编号" not in text
    assert r.status == "completed"


async def test_broken_organizer_output_falls_back_to_per_member_map():
    env = make(organize=lambda m, msgs: "不是 JSON")
    r = await run(env)
    assert r.status == "completed"
    saved = json.loads(env.rt.repo.outputs(r.session_id, kind="mindmap")[0]["content"])
    assert saved["map"]["degraded"] is True and saved["map"]["branches"]
    assert "的想法" in saved["map"]["branches"][0]["name"]


async def test_state_restores_from_database_for_resume():
    env = make()
    r = await run(env)
    state = restore_state(env.rt.repo, r.session_id, 0)
    assert state.brain.mindmap is not None and state.brain.svg.startswith("<svg")
    assert state.brain.ideas and all(
        isinstance(i, Idea) for v in state.brain.ideas.values() for i in v
    )
    assert state.brain.expansions
    assert final_answer(state) == r.final_answer


async def test_anonymous_brainstorm_prompts_and_outputs_carry_no_model_identity():
    env = make()
    r = await run(env, anonymous=True)
    terms = {t for m in env.config.models.models for t in (m.id, m.vendor)}
    sent = " ".join(m.content for c in env.fake.calls for m in c.messages)
    for t in terms:
        assert not re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", sent), t
    view = str(env.rt.repo.session_view(r.session_id))
    assert "'b1'" not in view and "V1" not in view
