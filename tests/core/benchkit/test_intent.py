"""意图理解、分段规划、意图追踪表的纯函数（不调助手）。"""

from __future__ import annotations

from roundtable.core.benchkit import intent as it

REQS = [
    {"id": "r1", "text": "能导入照片", "windows": ["canvas"]},
    {"id": "r2", "text": "一键变好看", "windows": ["canvas", "tools"]},
    {"id": "r3", "text": "能导出", "windows": ["export"]},
    {"id": "b1", "text": "底线", "windows": []},
]
WIDS = ["canvas", "tools", "export", "history"]


def test_voices_are_kept_verbatim_with_ids():
    b: dict = {}
    it.add_voice(b, "开工", "  做个修图台  ")
    assert it.add_voice(b, "签字", "   ") is None
    it.add_voice(b, "变更单", "按钮放右边", ref="c1")
    it.add_voice(b, "乱写", "x")
    assert [(v["id"], v["src"], v["text"]) for v in b["voices"]] == [
        ("v1", "开工", "做个修图台"),
        ("v2", "变更单", "按钮放右边"),
        ("v3", "开工", "x"),
    ]
    assert "v2（变更单）按钮放右边" in it.voices_text(b)


def test_intent_levels_sources_and_limits():
    got = it.intent_of(
        {
            "ideal": "小白也能一键修好照片",
            "who": {"text": "摄影小白", "level": "确定"},
            "where": "电脑上",  # 只给字：默认推断
            "task": {"text": "", "level": "确定"},  # 空的丢掉
            "feel": {"text": "专业又简单", "level": "瞎写"},
            "expect": [{"text": "不破坏原图", "from": "对标"}, {"text": "前后对比", "from": "X"}]
            + [{"text": f"e{i}"} for i in range(10)],
            "assume": [{"text": "默认深色", "level": "确定"}],
        }
    )
    assert got["who"] == {"text": "摄影小白", "level": "确定"}
    assert got["where"]["level"] == "推断" and got["task"] is None
    assert got["feel"]["level"] == "推断"
    assert got["expect"][0]["from"] == "对标" and got["expect"][1]["from"] == "常识"
    assert len(got["expect"]) == 8
    assert got["assume"] == [{"text": "默认深色", "level": "假设"}]  # 假设永远是假设
    text = it.intent_text(got)
    assert "给谁用：摄影小白（确定）" in text and "隐含期待：不破坏原图（推断，来自对标）" in text
    assert it.intent_counts(got) == {"确定": 1, "推断": 10, "假设": 1}
    assert it.intent_of(None)["who"] is None


def test_questions_capped_and_answers_become_certain():
    qs = it.questions_of(
        [
            {
                "q": "小白还是专业？",
                "options": ["小白", "专业"],
                "recommend": "小白",
                "about": "who",
            },
            {"q": "手机还是电脑？", "options": ["手机", "电脑"], "recommend": "平板", "about": "?"},
            {"q": ""},
            {"q": "第三问", "options": ["a"]},
            {"q": "第四问不要"},
        ]
    )
    assert [q["id"] for q in qs] == ["q1", "q2", "q3"]
    assert qs[1]["recommend"] == "手机" and qs[1]["about"] == ""  # 推荐项必须是选项之一
    intent = it.intent_of({"who": {"text": "大众", "level": "推断"}})
    it.answer_intent(intent, qs, {"q1": "专业摄影师", "q2": ""})
    assert intent["who"] == {"text": "专业摄影师", "level": "确定"}
    assert any("按推荐「手机」" in a["text"] for a in intent["assume"])


def test_stages_cover_every_window_and_requirement():
    raw = [
        {
            "title": "最小可用",
            "goal": "导入到导出走通",
            "try": ["导入", "一键", "导出", "多余"],
            "windows": ["canvas", "export", "ghost"],
            "accept": ["r1", "r3", "zz"],
        },
        {"title": "主要功能", "windows": ["canvas", "tools"], "accept": ["r1", "r2"]},
    ]
    st = it.stages_of(raw, WIDS, REQS, hero="canvas")
    assert [s["n"] for s in st] == [1, 2]
    assert st[0]["windows"] == ["canvas", "export"] and st[0]["try"] == ["导入", "一键", "导出"]
    assert st[0]["accept"] == ["r1", "r3"]
    assert st[1]["accept"] == ["r2", "b1"]  # r1 只算第一次出现的段；底线放最后一段
    assert "history" in st[1]["windows"]  # 漏了的窗口放最后一段
    assert st[1]["goal"] == "主要功能"


def test_stages_fallback_merge_and_split():
    fb = it.stages_of(None, WIDS, REQS, hero="")
    assert fb[0]["windows"] == ["canvas"] and fb[0]["accept"] == ["r1"]
    assert fb[1]["windows"] == ["tools", "export", "history"]
    assert fb[1]["accept"] == ["r2", "r3", "b1"]
    many = [{"title": f"段{i}", "windows": [w]} for i, w in enumerate(WIDS * 2)]
    assert len(it.stages_of(many, WIDS, REQS, max_stages=3)) == 3
    one = it.stages_of([{"windows": WIDS}], WIDS, REQS, hero="tools")
    assert [s["windows"] for s in one] == [["tools"], ["canvas", "export", "history"]]


def test_each_stage_has_at_most_nine_acceptance_items():
    reqs = [{"id": f"r{i}", "text": "x", "windows": []} for i in range(1, 13)]
    st = it.stages_of(
        [{"windows": ["canvas"], "accept": [r["id"] for r in reqs]}, {"windows": ["tools"]}],
        WIDS,
        reqs,
    )
    assert len(st[0]["accept"]) == 9 and st[1]["accept"][:3] == ["r10", "r11", "r12"]


def test_stage_task_says_goal_accept_and_what_not_to_do():
    st = it.stages_of(
        [
            {
                "title": "最小可用",
                "goal": "走通",
                "try": ["导入", "导出"],
                "windows": ["canvas"],
                "accept": ["r1"],
                "skip": "滤镜",
            },
            {"windows": ["tools"]},
        ],
        WIDS,
        REQS,
    )
    text = it.stage_task(st[0], REQS, 2)
    assert "第 1/2 段「最小可用」：走通" in text and "r1 能导入照片" in text
    assert "导入 → 导出" in text and "这段不做：滤镜" in text and "假按钮" in text
    assert "假按钮" not in it.stage_task(st[1], REQS, 2)


def test_link_voices_only_known_ids_and_fallback_to_first():
    reqs = [{"id": "r1"}, {"id": "r2"}, {"id": "b1"}]
    raw = [{"text": "a", "voices": ["v2", "v9"]}, {"text": "b"}, {"id": "b1", "voices": ["v1"]}]
    it.link_voices(reqs, raw, [{"id": "v1"}, {"id": "v2"}])
    assert reqs[0]["voices"] == ["v2"] and reqs[1]["voices"] == ["v1"]
    assert "voices" not in reqs[2]


def test_trace_follows_each_voice_to_its_state():
    b = {
        "voices": [
            {"id": "v1", "src": "开工", "text": "修图台"},
            {"id": "v2", "src": "访谈", "text": "给小白"},
            {"id": "v3", "src": "变更单", "text": "按钮放右边", "ref": "c1"},
            {"id": "v4", "src": "开工", "text": "没对上的"},
            {"id": "v5", "src": "签字", "text": "验收清单：能导出"},
        ],
        "proposal": {
            "requirements": [
                {"id": "r1", "voices": ["v1"]},
                {"id": "r2", "voices": ["v1"]},
                {"id": "r3", "voices": ["v5"]},
            ],
            "milestones": [{"n": 1, "accept": ["r1"]}, {"n": 2, "accept": ["r2", "r3"]}],
        },
        "changes": [{"id": "c1", "status": "done"}],
        "rounds": [],
    }
    rows = {r["id"]: r for r in it.trace(b)}
    assert rows["v1"]["state"] == "待做" and rows["v1"]["stage"] == 1
    assert rows["v1"]["reqs"] == ["r1", "r2"]
    assert rows["v2"]["state"] == "已参考" and rows["v3"]["state"] == "改好了"
    assert rows["v4"]["state"] == "没对上" and rows["v5"]["stage"] == 2
    b["rounds"] = [
        {
            "requirements": [
                {"id": "r1", "met": True},
                {"id": "r2", "met": False},
                {"id": "r3", "met": True},
            ]
        }
    ]
    rows = {r["id"]: r for r in it.trace(b)}
    assert rows["v1"]["state"] == "没做到" and rows["v5"]["state"] == "做到"
    assert [r["id"] for r in it.open_items(list(rows.values()))] == ["v1", "v4"]


def test_trace_before_plan_is_pending_not_unmatched():
    b = {"voices": [{"id": "v1", "src": "开工", "text": "修图台"}]}
    assert it.trace(b)[0]["state"] == "待做"
