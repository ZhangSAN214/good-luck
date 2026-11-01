"""前端：关于你（成员系统 P8）。看证据、去看原话、锁定、不对、按领域关、自己写一条、两步清空。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from roundtable.core.member.about import AboutYou
from roundtable.core.storage.repository import iso


def open_about(srv, page):
    page.goto(srv.url + "/#m=chat")
    page.wait_for_selector("#gc-members button[data-name]")
    page.click("#gc-people")
    page.wait_for_selector("#people[open] .pp-who[data-member='__about']")
    page.click("#people .pp-who[data-member='__about']")
    page.wait_for_selector("#people #pp-about .ab-doms")


def seed(srv) -> AboutYou:
    repo = srv.env.rt.repo
    now = datetime.now(UTC)
    start = now - timedelta(hours=2)
    repo.add_chat_message("main", "user", "我周末一般去爬山，雷打不动", created_at=iso(start))
    repo.add_chat_message(
        "main", "user", "又熬夜赶作业了", created_at=iso(start + timedelta(minutes=5))
    )
    about = AboutYou(srv.env.rt)
    window = (iso(start - timedelta(minutes=1)), iso(now))
    clues = [
        {"text": "用户周末常去爬山", "quote": "周末一般去爬山", "domain": "兴趣爱好", "said": True},
        {"text": "用户常熬夜", "quote": "又熬夜赶作业", "domain": "作息", "said": False},
    ]
    about.observe("V2", now.date().isoformat(), window, clues)
    about.add("用户叫小张", "称呼与身份")
    return about


def test_about_you_page(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    about = seed(srv)
    open_about(srv, page)
    text = page.inner_text("#people #pp-about")
    assert "用户周末常去爬山" in text and "用户常熬夜" in text and "用户叫小张" in text
    assert "你说过" in text and "推测" in text and "你写的" in text
    climb = next(f for f in about.facts() if "爬山" in f["text"])
    page.click(f"#people [data-fid='{climb['id']}'] [data-ev]")
    page.wait_for_selector("#people .ab-ev q")
    assert "我周末一般去爬山，雷打不动" in page.inner_text("#people .ab-ev")
    assert "阿二 注意到" in page.inner_text("#people .ab-ev")
    if os.environ.get("ROUNDTABLE_SHOTS"):
        page.locator("#people #pp-about").screenshot(path=os.environ["ROUNDTABLE_SHOTS"])
    # 锁定
    page.click(f"#people [data-fid='{climb['id']}'] [data-lock]")
    page.wait_for_selector("#people [role=status]:has-text('锁住了')")
    assert next(f for f in about.facts() if f["id"] == climb["id"])["locked"]
    # 不对：删掉并进「不对」名单
    late = next(f for f in about.facts() if "熬夜" in f["text"])
    page.click(f"#people [data-fid='{late['id']}'] [data-reject]")
    page.wait_for_selector("#people .ab-rej")
    assert all("熬夜" not in f["text"] for f in about.facts())
    # 按领域关
    page.click("#people .ab-dom[data-dom='兴趣爱好']")
    page.wait_for_selector("#people .ab-dom[data-dom='兴趣爱好'][aria-pressed='false']")
    assert "兴趣爱好" in about.switches()["domains_off"]
    # 自己写一条
    page.fill("#people #ab-add input[name=text]", "用户不吃香菜")
    page.select_option("#people #ab-add select", "口味")
    page.click("#people #ab-add button")
    page.wait_for_selector("#people #pp-about li:has-text('用户不吃香菜')")
    # 去看原话：回到群聊（证据还展开着）
    page.click("#people [data-go]")
    page.wait_for_selector("#people", state="hidden")
    # 两步清空
    page.click("#gc-people")
    page.click("#people .pp-who[data-member='__about']")
    page.wait_for_selector("#people #ab-clear")
    page.click("#people #ab-clear")
    assert "再点一次" in page.inner_text("#people #ab-clear")
    page.click("#people #ab-clear")
    page.wait_for_selector("#people [role=status]:has-text('已清空')")
    assert about.facts() == []


def test_your_status_switches_and_timetable(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    open_about(srv, page)
    page.wait_for_selector("#people .ab-status")
    assert page.locator("#people .ab-tog.on").count() == 0  # 默认全关
    page.click("#people [data-st='timetable']")
    page.wait_for_selector("#people #ab-class")
    about = AboutYou(srv.env.rt)
    assert about.status_on("timetable") and not about.status_on("music")
    now = datetime.now(UTC)
    page.select_option("#people #ab-class select", str(now.weekday()))
    page.fill("#people #ab-class input[name=start]", "00:00")
    page.fill("#people #ab-class input[name=end]", "23:59")
    page.fill("#people #ab-class input[name=name]", "全天的课")
    page.click("#people #ab-class button")
    page.wait_for_selector("#people .ab-mini li:has-text('全天的课')")
    page.fill("#people #ab-ddl input[name=name]", "操作系统作业")
    due = (now + timedelta(days=2)).strftime("%Y-%m-%dT23:59")
    page.fill("#people #ab-ddl input[name=due]", due)
    page.click("#people #ab-ddl button")
    page.wait_for_selector("#people .ab-mini li:has-text('操作系统作业')")
    assert "操作系统作业" in page.inner_text("#people li[data-kind='timetable']")
    # 这台机器读不到歌：打开后写明原因
    page.click("#people [data-st='music']")
    page.wait_for_selector("#people li[data-kind='music'] .ab-tog.on")
    assert page.inner_text("#people li[data-kind='music'] .ab-meta")
    if os.environ.get("ROUNDTABLE_SHOTS"):
        page.locator("#people .ab-status").screenshot(
            path=os.environ["ROUNDTABLE_SHOTS"].replace(".png", "_status.png")
        )
    page.click("#people #ab-sch-clear")
    page.click("#people #ab-sch-clear")
    page.wait_for_selector("#people [role=status]:has-text('清空了')")
    assert about.schedule()["classes"] == []
