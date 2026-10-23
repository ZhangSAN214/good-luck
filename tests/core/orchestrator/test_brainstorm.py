"""头脑风暴（R16）：按抽到的手法发散 → 碰撞杂交 → 匿名投票 → 整合成导图与点子榜（旧流程：拓展）。"""

from __future__ import annotations

import json
import re

from roundtable.core.routing import Question, UserChoice
from roundtable.core.steps import final_answer, restore_state
from roundtable.core.steps.brain_schemas import (
    Idea,
    parse_ballot,
    parse_board_extras,
    parse_crossbreed,
    parse_expansions,
    parse_ideas,
    parse_mindmap,
)

from .conftest import Env
from .test_media import script

TOPIC = "如何降低钢件淬火开裂的风险？帮我拓展思路"
IDEATE, CROSS, VOTE, ORGANIZE = (
    "其他人这一轮看不到你的点子",
    "让点子互相碰撞",
    "请投票",
    "你是头脑风暴的统筹",
)
# 旧流程（ideate/v1 → expand → organize/v1）
IDEATE_V1, EXPAND = "其他人这一轮看不到你的想法", "请在它们的基础上继续拓展思路"
LEGACY = {
    "brainstorm_pipeline": ["ideate", "expand", "organize", "reveal"],
}


def code_of(msgs) -> str:
    return re.search(r"代号「(.+?)」", msgs[0].content).group(1)


def ideate_reply(model, msgs):
    code = code_of(msgs)
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


def cross_reply(model, msgs):
    code = code_of(msgs)
    ids = re.findall(r'<idea id="(I\d+)">', msgs[-1].content)
    return json.dumps(
        {
            "items": [
                {"kind": "hybrid", "on": ids[0], "with": ids[1], "text": f"{code}的杂交点子"},
                {"kind": "fix", "on": ids[0], "text": "挑出致命问题并补救"},
                {"kind": "flip", "on": "I999", "text": "无效编号"},
            ]
        },
        ensure_ascii=False,
    )


def vote_reply(model, msgs):
    ids = re.findall(r'<idea id="([IE]\d+)">', msgs[-1].content)
    picks = [
        {"id": i, "novelty": 5 - n % 3, "feasibility": 3, "value": 4} for n, i in enumerate(ids)
    ]
    return json.dumps({"picks": picks[:3], "dark_horse": ids[-1]}, ensure_ascii=False)


def organize_reply(model, msgs):
    sys = msgs[0].content if msgs else ""
    ids = [f"I{n}" for n in range(1, 9)] + [f"E{n}" for n in range(1, 9)]
    plan = re.search(r"为编号 (\S+) 写行动方案", sys)
    return json.dumps(
        {
            "title": "降低开裂",
            "branches": [{"name": "工艺", "ideas": [{"text": "整合想法", "refs": ["I1", "I3"]}]}],
            "compare": [
                {"id": i, "novelty": "新", "feasibility": "易", "cost": "低", "risk": "小"}
                for i in ids
            ],
            "stories": [{"id": i, "story": f"{i} 的故事"} for i in ids],
            "plan": {
                "id": plan.group(1) if plan else "",
                "experiment": "做一次小试",
                "steps": ["一", "二", "三"],
            },
            "dark_horse_why": "很大胆",
            "next_steps": ["先小试"],
        },
        ensure_ascii=False,
    )


def make(organize=organize_reply, legacy=False, **kw) -> Env:
    if legacy:
        kw["rt_update"] = LEGACY
    env = Env(confirm_threshold_usd=100.0, **kw)
    if legacy:
        prompts = {**env.config.roundtable.prompts, "ideate": "v1", "organize": "v1"}
        rt = env.config.roundtable.model_copy(update={"prompts": prompts})
        env.config = env.config.model_copy(update={"roundtable": rt})
        env.rt.config = env.config
    script(
        env,
        **{
            IDEATE: ideate_reply,
            IDEATE_V1: ideate_reply,
            EXPAND: expand_reply,
            CROSS: cross_reply,
            VOTE: vote_reply,
            ORGANIZE: organize,
        },
    )
    return env


async def run(env, anonymous=False):
    return await env.orc.start(
        Question(TOPIC), UserChoice("budget", workflow="brainstorm"), seed=3, anonymous=anonymous
    )


def outputs(env, sid, kind):
    return [o for o in env.rt.repo.outputs(sid) if o["kind"] == kind]


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


def test_parse_crossbreed_validates_ids_and_downgrades_bad_hybrid():
    text = json.dumps(
        {
            "items": [
                {"kind": "hybrid", "on": "i1", "with": "I2", "text": "杂交"},
                {"kind": "hybrid", "on": "I1", "with": "I9", "text": "另一个编号无效按反转"},
                {"kind": "hybrid", "on": "I1", "with": "I1", "text": "自己和自己也按反转"},
                {"kind": "fix", "on": "I9", "text": "编号不存在"},
                {"kind": "build", "text": "种类不认识"},
                {"kind": "flip", "on": "I2", "text": ""},
            ]
        }
    )
    got = parse_crossbreed(text, ["I1", "I2"], 5)
    assert [(x.kind, x.on, x.also) for x in got] == [
        ("hybrid", "I1", "I2"),
        ("flip", "I1", ""),
        ("flip", "I1", ""),
    ]
    assert len(parse_crossbreed(text, ["I1", "I2"], 1)) == 1


def test_parse_ballot_clamps_scores_dedupes_and_rejects_unknown_ids():
    text = json.dumps(
        {
            "picks": [
                {"id": "i1", "novelty": 9, "feasibility": "2", "value": "很高"},
                {"id": "I1", "novelty": 1, "feasibility": 1, "value": 1},
                {"id": "I7", "novelty": 5, "feasibility": 5, "value": 5},
                {"id": "E1", "novelty": 0, "feasibility": 4, "value": 4},
            ],
            "dark_horse": "I7",
        }
    )
    b = parse_ballot(text, ["I1", "E1"], 5)
    assert [(p.id, p.novelty, p.feasibility, p.value) for p in b.picks] == [
        ("I1", 5, 2, 3),
        ("E1", 1, 4, 4),
    ]
    assert b.dark_horse == ""  # 不在给出的范围内（例如自己的点子）
    try:
        parse_ballot('{"picks": [{"id": "I7"}]}', ["I1"], 5)
    except ValueError:
        pass
    else:
        raise AssertionError("一张有效票都没有应当报错")


def test_parse_board_extras_keeps_only_requested_ids_in_rank_order():
    text = organize_reply(None, [type("M", (), {"content": "为编号 I2 写行动方案"})()])
    got = parse_board_extras(
        text, top_ids=["I3", "I1"], story_ids=["I3"], plan_id="I2", dark_horse=""
    )
    assert [c.id for c in got["compare"]] == ["I3", "I1"]
    assert got["stories"] == (("I3", "I3 的故事"),)
    assert got["plan"].steps == ("一", "二", "三") and got["dark_horse_why"] == ""
    assert (
        parse_board_extras("不是 JSON", top_ids=[], story_ids=[], plan_id="", dark_horse="") == {}
    )


# --- 全流程 ------------------------------------------------------------------------


async def test_full_brainstorm_run_produces_board_mindmap_and_final_answer():
    env = make()
    r = await run(env)
    assert r.status == "completed"
    table = env.rt.repo.tables(r.session_id)[0]
    assert table["pipeline"] == ["ideate", "crossbreed", "vote", "organize", "reveal"]
    members = set(table["members"])
    assert len(outputs(env, r.session_id, "ideas")) == len(members)
    assert len(outputs(env, r.session_id, "expansions")) == len(members)
    assert len(outputs(env, r.session_id, "votes")) == len(members)
    saved = json.loads(outputs(env, r.session_id, "mindmap")[0]["content"])
    assert {"map", "markdown", "mermaid", "svg", "board"} <= set(saved)
    board = saved["board"]
    assert board["ranking"] and board["compare"] and board["stories"]
    assert board["plan"]["id"] == board["ranking"][0]["id"]
    assert [c["id"] for c in board["compare"]] == [x["id"] for x in board["ranking"][:5]]
    assert len(board["lenses"]) == len(members)
    # 最终答案 = 点子榜 + 大纲 + Mermaid
    fa = r.final_answer
    assert "点子榜" in fa and "行动方案" in fa and "# 降低开裂" in fa and "```mermaid" in fa
    # 统筹不出点子、不投票
    assert table["coordinator"] not in {
        c.model
        for c in env.fake.calls
        if IDEATE in c.messages[0].content or VOTE in c.messages[0].content
    }


async def test_each_member_gets_a_different_lens_from_config_with_same_template_and_params():
    env = make()
    r = await run(env)
    calls = [c for c in env.fake.calls if IDEATE in c.messages[0].content]
    names = {x.name for x in env.config.roundtable.brainstorm.lenses}
    got = [re.search(r"你抽到的是「(.+?)」", c.messages[0].content).group(1) for c in calls]
    assert len(got) == 2 and len(set(got)) == 2 and set(got) <= names
    assert len({json.dumps(c.params, sort_keys=True) for c in calls}) == 1
    # 抽签结果存进产出，恢复时可读回
    state = restore_state(env.rt.repo, r.session_id, 0)
    assert sorted(state.brain.lenses.values()) == sorted(got)


async def test_lens_assignment_is_reproducible_for_the_same_seed():
    got = []
    for _ in range(2):
        env = make()
        await run(env)
        got.append(
            sorted(
                re.search(r"你抽到的是「(.+?)」", c.messages[0].content).group(1)
                for c in env.fake.calls
                if IDEATE in c.messages[0].content
            )
        )
    assert got[0] == got[1]


async def test_crossbreed_and_vote_never_show_members_their_own_ideas():
    env = make()
    r = await run(env)
    for phrase in (CROSS, VOTE):
        calls = [c for c in env.fake.calls if phrase in c.messages[0].content]
        assert len(calls) == 2  # 便宜档 3 个模型：1 个统筹 + 2 位组员
        for c in calls:
            mine = code_of(c.messages)
            assert f"{mine}的" not in c.messages[-1].content
        assert len({json.dumps(c.params, sort_keys=True) for c in calls}) == 1
    assert r.status == "completed"


async def test_hybrids_are_voted_on_and_attached_in_organize_input():
    env = make()
    r = await run(env)
    vote = next(c for c in env.fake.calls if VOTE in c.messages[0].content)
    assert re.search(r'<idea id="E\d+">', vote.messages[-1].content)  # 杂交出的点子也参加投票
    org = next(c for c in env.fake.calls if ORGANIZE in c.messages[0].content)
    text = org.messages[-1].content
    assert 'kind="杂交"' in text and 'with="I' in text and 'kind="挑刺补救"' in text
    assert "无效编号" not in text and "<ranking>" in text
    assert r.status == "completed"


async def test_broken_organizer_output_falls_back_but_keeps_vote_ranking():
    env = make(organize=lambda m, msgs: "不是 JSON")
    r = await run(env)
    assert r.status == "completed"
    saved = json.loads(outputs(env, r.session_id, "mindmap")[0]["content"])
    assert saved["map"]["degraded"] is True and saved["map"]["branches"]
    assert "的想法" in saved["map"]["branches"][0]["name"]
    assert saved["board"]["ranking"] and not saved["board"]["compare"]


async def test_state_restores_from_database_for_resume():
    env = make()
    r = await run(env)
    state = restore_state(env.rt.repo, r.session_id, 0)
    assert state.brain.mindmap is not None and state.brain.svg.startswith("<svg")
    assert state.brain.ideas and all(
        isinstance(i, Idea) for v in state.brain.ideas.values() for i in v
    )
    assert state.brain.expansions and state.brain.votes and state.brain.board is not None
    assert final_answer(state) == r.final_answer


async def test_legacy_flow_still_runs_with_v1_prompts():
    env = make(legacy=True)
    r = await run(env)
    assert r.status == "completed"
    table = env.rt.repo.tables(r.session_id)[0]
    assert table["pipeline"] == ["ideate", "expand", "organize", "reveal"]
    org = next(c for c in env.fake.calls if ORGANIZE in c.messages[0].content)
    assert 'kind="深化"' in org.messages[-1].content
    assert not any("你抽到的是" in c.messages[0].content for c in env.fake.calls)
    saved = json.loads(outputs(env, r.session_id, "mindmap")[0]["content"])
    assert "board" not in saved and "点子榜" not in r.final_answer


async def test_anonymous_brainstorm_prompts_and_outputs_carry_no_model_identity():
    env = make()
    r = await run(env, anonymous=True)
    terms = {t for m in env.config.models.models for t in (m.id, m.vendor)}
    sent = " ".join(m.content for c in env.fake.calls for m in c.messages)
    for t in terms:
        assert not re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", sent), t
    view = str(env.rt.repo.session_view(r.session_id))
    assert "'b1'" not in view and "V1" not in view
