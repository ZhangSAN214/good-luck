"""前端：革新例会入口——默认关闭时说明怎么开，「现在开一场」按钮不可用。"""

from __future__ import annotations


def test_evolution_dialog_when_off(serve, page):
    srv = serve()
    page.goto(srv.url)
    page.click("#evolve")
    page.wait_for_selector("#evolvedlg[open] .ev")
    text = page.inner_text("#evolvedlg")
    assert "没有开启" in text and "evolution.enabled" in text
    assert "收件箱里还没有资料卡" in text and "还没开过" in text
    assert page.locator("#evolvedlg [data-meet]").is_disabled()
    page.click("#evolvedlg [data-close]")
    assert page.locator("#evolvedlg").is_hidden()
    page.click("#evolve")
    page.wait_for_selector("#evolvedlg[open] .ev")
    assert page.locator("#evolvedlg [data-scout]").is_disabled()  # 没开启时也不能找资料


def _info_with_plugins(n=22):
    scenes = ["办公室工作", "公寓生活", "群聊", "朋友圈", "头脑风暴", "记忆", "通用"]
    items = [
        {
            "name": f"插件{i + 1}",
            "scene": scenes[i % len(scenes)],
            "what": "做什么" * 3,
            "use": "有什么用",
            "pros": "优点",
            "cons": "缺点",
            "why": "为什么做",
            "source": "资料卡 1",
        }
        for i in range(n)
    ]
    counts = {s: sum(1 for p in items if p["scene"] == s) for s in scenes}
    ideas = [
        {
            "id": f"I{i}",
            "text": f"建议{i}",
            "score": 30 - i,
            "votes": 3,
            "decision": None,
            "job": None,
        }
        for i in range(1, 11)
    ]
    meeting = {
        "session_id": "s1",
        "week": "2026-W44",
        "started_at": "",
        "auto": False,
        "adopted": None,
        "status": "completed",
        "cost_usd": 0.18,
        "ideas": ideas,
        "kind": "major",
        "kind_label": "大版本",
        "theme": "群聊更自然",
        "top": 10,
        "plugins": {"items": items, "scenes": counts, "cost_usd": 0.012, "file": "2026-W44.md"},
    }
    return {
        "enabled": True,
        "auto": False,
        "plan_label": "节电模式",
        "anonymous": True,
        "cards": [],
        "notes": [],
        "meetings": [meeting],
        "kinds": {"minor": {"label": "小版本", "top": 3}, "major": {"label": "大版本", "top": 10}},
        "themes": ["群聊更自然", "更省钱更快"],
        "theme_chars": 200,
        "plugins": {"enabled": True, "count": 20, "scenes": scenes, "estimate_usd": 0.04},
        "scout": {},
    }


def test_meeting_shows_top_ideas_and_plugin_list_with_scene_filter(serve, page):
    import json

    srv = serve()
    info = _info_with_plugins()
    page.route(
        "**/api/evolution",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(info)
        ),
    )
    page.goto(srv.url)
    page.click("#evolve")
    page.wait_for_selector("#evolvedlg[open] .ev-plugin")
    assert page.locator("#evolvedlg .ev-ideas > li").count() == 10  # 得票前 10 名
    assert page.locator("#evolvedlg .ev-plugin").count() == 22
    card = page.inner_text("#evolvedlg .ev-plugin >> nth=0")
    for label in ("做什么", "有什么用", "优点", "缺点", "为什么做", "参考"):
        assert label in card
    page.click("#evolvedlg .ev-chip[data-scene='公寓生活']")
    assert page.locator("#evolvedlg .ev-plugin").count() == 3
    assert all(
        "公寓生活" in t for t in page.locator("#evolvedlg .ev-plugin .ev-tag").all_inner_texts()
    )
    page.click("#evolvedlg .ev-chip[data-scene='']")
    assert page.locator("#evolvedlg .ev-plugin").count() == 22


def test_guide_and_choosing_major_theme(serve, page):
    import json

    srv = serve()
    info = _info_with_plugins()
    info["cards"] = [{"file": "a.md", "title": "卡", "chars": 10}]
    sent = []

    def meet(route):
        sent.append(route.request.post_data_json)
        route.fulfill(
            status=202,
            content_type="application/json",
            body=json.dumps({"session_id": "s2", "estimate_usd": 0.2, "kind": "major"}),
        )

    page.route(
        "**/api/evolution",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(info)
        ),
    )
    page.route("**/api/evolution/meetings", meet)
    page.goto(srv.url)
    page.click("#evolve")
    page.wait_for_selector("#evolvedlg[open] .ev-guide")
    # 新手说明：大白话讲流程；每个按钮都有说明
    page.click("#evolvedlg .ev-guide summary")
    guide = page.inner_text("#evolvedlg .ev-guide")
    for word in ("小版本", "大版本", "做这条", "合并", "花钱吗"):
        assert word in guide
    for sel in ("[data-meet]", "[data-scout]", "[data-open]", "[data-decide]", "[data-scene]"):
        assert page.locator(f"#evolvedlg {sel}").first.get_attribute("title")
    row = page.inner_text("#evolvedlg .ev-list >> nth=1")
    assert "大版本" in row and "「群聊更自然」" in row
    # 默认小版本，没有主题框；选大版本后出现主题，不填主题不能开
    assert page.locator("#evolvedlg [data-theme-input]").count() == 0
    page.check("#evolvedlg input[name=evkind][value=major]")
    page.wait_for_selector("#evolvedlg [data-theme-input]")
    page.click("#evolvedlg [data-meet]")
    assert "先选或填一个主题" in page.inner_text("#evolvedlg .pp-msg") and sent == []
    page.click("#evolvedlg .ev-chip[data-theme='更省钱更快']")
    assert page.input_value("#evolvedlg [data-theme-input]") == "更省钱更快"
    page.fill("#evolvedlg [data-theme-input]", "让成员记性更好")
    page.click("#evolvedlg [data-meet]")
    page.wait_for_function(
        "document.querySelector('#evolvedlg .pp-msg').textContent.includes('开始了')"
    )
    assert sent == [{"kind": "major", "theme": "让成员记性更好"}]
