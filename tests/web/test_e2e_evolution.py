"""前端：革新例会——默认关闭时说明怎么开；下一场显示自动判断的结论；
本场结论「做 / 不做」；插件清单收在「更多」。"""

from __future__ import annotations


def test_evolution_dialog_when_off(serve, page):
    srv = serve()
    page.goto(srv.url)
    page.click("#evolve")
    page.wait_for_selector("#evolvedlg[open] .ev")
    text = page.inner_text("#evolvedlg")
    assert "没开启" in text and "evolution.enabled" in text
    assert "还没开过" in text
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
        "next": {
            "kind": "major",
            "label": "大版本",
            "top": 10,
            "reason": "11 月的第一场，距上次大版本 28 天，到了每月一次的大改",
            "theme": "群聊更自然",
            "theme_source": "auto",
            "major_theme": "群聊更自然",
        },
        "next_theme": None,
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
    page.wait_for_selector("#evolvedlg[open] .ev-ideas")
    assert page.locator("#evolvedlg .ev-ideas > li").count() == 10  # 得票前 10 名
    # 插件清单收在「更多」里，点开才看到
    assert not page.locator("#evolvedlg .ev-plugin >> nth=0").is_visible()
    page.click("#evolvedlg .ev-plugins > summary")
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


MSG = "document.querySelector('#evolvedlg .pp-msg').textContent"


def test_auto_kind_override_and_theme(serve, page):
    import json

    srv = serve()
    info = _info_with_plugins()
    info["cards"] = [{"file": "a.md", "title": "卡", "chars": 10}]
    sent, themes = [], []

    def meet(route):
        sent.append(route.request.post_data_json)
        route.fulfill(
            status=202,
            content_type="application/json",
            body=json.dumps({"session_id": "s2", "estimate_usd": 0.2, "kind": "minor"}),
        )

    def theme(route):
        themes.append(route.request.post_data_json)
        info["next_theme"] = route.request.post_data_json["theme"] or None
        route.fulfill(status=200, content_type="application/json", body=json.dumps(info["next"]))

    page.route(
        "**/api/evolution",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(info)
        ),
    )
    page.route("**/api/evolution/meetings", meet)
    page.route("**/api/evolution/next-theme", theme)
    page.goto(srv.url)
    page.click("#evolve")
    page.wait_for_selector("#evolvedlg[open] .ev-hero")
    # 没有手动选择：直接显示自动判断的结论和原因、主题
    hero = page.inner_text("#evolvedlg .ev-hero")
    assert "大版本" in hero and "10 个选项" in hero and "自动判断" in hero
    assert "「群聊更自然」" in hero and "自动轮换" in hero
    assert page.locator("#evolvedlg input[type=radio]").count() == 0
    # 简短说明：「怎么运作？」默认收起
    assert not page.locator("#evolvedlg .ev-how").is_visible()
    page.click("#evolvedlg .ev-help summary")
    assert "每月第一场" in page.inner_text("#evolvedlg .ev-how")
    for sel in ("[data-meet]", "[data-scout]", "[data-open]", "[data-decide]"):
        assert page.locator(f"#evolvedlg {sel}").first.get_attribute("title")
    # 改主题：点现成的 → 用这个主题
    page.click("#evolvedlg [data-edit-theme]")
    page.click("#evolvedlg .ev-chip[data-theme='更省钱更快']")
    assert page.input_value("#evolvedlg [data-theme-input]") == "更省钱更快"
    page.click("#evolvedlg [data-save-theme]")
    page.wait_for_function(
        "document.querySelector('#evolvedlg .pp-msg').textContent.includes('主题定为')"
    )
    assert themes == [{"theme": "更省钱更快"}]
    assert "你定的" in page.inner_text("#evolvedlg .ev-hero")
    # 手动改成小版本再开：发 kind=minor；不改就发 kind=null（后端自动判断）
    page.click("#evolvedlg [data-override='minor']")
    assert "手动改成了小版本" in page.inner_text("#evolvedlg .ev-hero")
    page.click("#evolvedlg [data-meet]")
    page.wait_for_function(
        "document.querySelector('#evolvedlg .pp-msg').textContent.includes('开始了')"
    )
    assert sent == [{"kind": "minor", "theme": None}]
    page.click("#evolvedlg [data-meet]")
    page.wait_for_timeout(200)
    assert sent[-1] == {"kind": None, "theme": None}
