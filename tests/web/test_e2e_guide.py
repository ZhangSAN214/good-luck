"""设置与新手指导：顶栏「设置」一站式说明书、常用工具搬进来照常能用、每页第一次进入弹小卡片。"""

from __future__ import annotations

from tests.web.conftest import wait_until

TUCKED = ["#bootcfg", "#skin", "#lanbtn", "#gamemodebtn", "#backupbtn", "#starmapbtn", "#wanderbtn"]


def test_settings_lists_every_page_and_tucks_toolbar(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    # 顶栏只留设置、出问题点这里、一起看；其余小按钮在设置里
    for sel in TUCKED:
        assert page.locator(f".brand {sel}").count() == 0
        assert page.locator(f"#settingsdlg .gd-tools {sel}").count() == 1
    assert page.is_visible("#firstaidbtn") and page.is_visible("#watchbtn")
    page.click("#settingsbtn")
    page.wait_for_selector("#settingsdlg[open] .gd-list")
    groups = page.locator("#settingsdlg .gd-group summary b").all_inner_texts()
    assert groups == ["终端", "办公室", "公寓楼", "群聊", "工作台", "顶栏工具"]
    # 每个功能都有一句话和步骤
    items = page.locator("#settingsdlg .gd-item")
    assert items.count() > 25
    assert page.locator("#settingsdlg .gd-item ol li").count() >= items.count()
    # 搜索：只留命中的，并展开
    page.fill("#settingsdlg .gd-search", "拍一拍")
    assert page.locator("#settingsdlg .gd-item").count() == 1
    assert page.locator("#settingsdlg .gd-group[open]").count() == 1
    page.fill("#settingsdlg .gd-search", "没有这个功能")
    page.wait_for_selector("#settingsdlg .gd-none")
    # 「去打开」：关掉设置、切到群聊
    page.fill("#settingsdlg .gd-search", "拍一拍")
    page.click("#settingsdlg .gd-go")
    assert not page.locator("#settingsdlg[open]").count()
    wait_until(lambda: page.is_visible("#gchat"))
    # 常用工具：点了照常打开原来的面板，设置先让开
    page.click("#settingsbtn")
    page.click("#settingsdlg #backupbtn")
    page.wait_for_selector("#backupdlg[open]")
    assert not page.locator("#settingsdlg[open]").count()


def test_coach_shows_once_per_page_and_can_be_turned_off(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.evaluate("localStorage.setItem('roundtable.guide', '{}')")  # 清掉测试默认的「关闭」
    page.reload()
    # 停在办公室：弹办公室的小卡片，最多三句
    page.wait_for_selector(".coach[data-entry=work]")
    assert page.locator(".coach li").count() <= 3
    page.click(".coach [data-coach=ok]")
    assert page.locator(".coach").count() == 0
    # 第一次进群聊弹群聊的；回办公室不再弹
    page.click("#entries [data-m=chat]")
    page.wait_for_selector(".coach[data-entry=chat]")
    page.click("#entries [data-m=work]")
    wait_until(lambda: page.is_visible("#main"))
    assert page.locator(".coach").count() == 0
    # 「不再提示」：别的页面也不弹了
    page.click("#entries [data-m=home]")
    page.wait_for_selector(".coach[data-entry=home]")
    page.click(".coach [data-coach=off]")
    page.click("#entries [data-m=bench]")
    wait_until(lambda: page.is_visible("#bench"))
    assert page.locator(".coach").count() == 0
    # 设置里「重新看一遍」：清掉记录，当前页马上再弹
    page.click("#settingsbtn")
    assert not page.is_checked("#settingsdlg [data-coach-on]")
    page.click("#settingsdlg [data-coach-again]")
    page.wait_for_selector(".coach[data-entry=bench]")
    # 记录清掉了：重新打开时办公室也会再弹
    page.goto(srv.url)
    page.wait_for_selector(".coach[data-entry=work]")
