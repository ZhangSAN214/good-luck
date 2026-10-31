"""前端：「工作台」→「生成器」。助手、质检、找插件都是假的，不跑命令行、不联网。

一句话 → 进度条走完 → 窗口分工表、插件清单（直接用 / 只借思路）、每轮分数和截图 →
打开成品（沙箱 iframe）→ 成品里的对话窗经 postMessage 问到后端、拿到回答。
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import roundtable.core.service as service_mod
from roundtable.core.benchkit.builder import BenchBuilder
from roundtable.core.benchkit.quality import Report
from roundtable.core.benchkit.scout import Candidate
from roundtable.core.config.schema import BenchProject, BenchTeam
from roundtable.core.supercomputer.runner import RunResult

from ..core.benchkit.test_builder import FakeResearcher, ResearchSC, W
from ..core.benchkit.test_builder import render as fake_render

# 1×1 的 PNG：截图要能真的显示出来
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
ASK_WINDOW = (
    "export default async (el, ctx) => {"
    " el.innerHTML = '<p class=\"answer k-sub\">' + ctx.kit.esc('…') + '</p><p class=\"gen\"></p>';"
    " el.querySelector('.answer').textContent = await ctx.ask('第一天去哪');"
    " const got = await ctx.askJson('按杭州 3 天生成行程');"
    " el.querySelector('.gen').textContent ="
    " got.data ? '生成了 ' + got.data.days.length + ' 天' : got.text;"
    " };"
)


class SlowSC(ResearchSC):
    """写窗口时放一个会提问的窗口；调研和学习卡同 ResearchSC。"""

    async def step(self, kind, prompt, **kw):
        chosen, result = await super().step(kind, prompt, **kw)
        p = json.loads(prompt)
        if p["role"] == "bench_window" and p["file"].endswith("plan.js"):
            (kw["folder"] / p["file"]).write_text(ASK_WINDOW, encoding="utf-8")
        if p["role"] == "bench_ask":
            return W(chosen.id), RunResult(0, "先去浅草寺，再去上野")
        if p["role"] == "bench_review":  # 验收清单逐条判：都做到了
            out = json.loads(result.output)
            ids = [line[2:].split("（")[0] for line in p["requirements"].splitlines()]
            out["checks"] = [{"id": i, "met": True, "why": ""} for i in ids]
            return chosen, RunResult(0, json.dumps(out, ensure_ascii=False))
        return chosen, result


class FakeScout:
    async def search(self, need, keywords):
        return [
            Candidate(
                "github",
                "acme/maps",
                "https://github.com/acme/maps",
                stars=900,
                license="MIT",
                verdict="adopt",
                why="MIT，900 星",
                need=need,
            ),
            Candidate(
                "dsh",
                "solo/tiny",
                "https://github.com/solo/tiny",
                stars=12,
                license="MIT",
                verdict="idea",
                why="12 星，不到 100 星，只借思路",
                need=need,
            ),
        ]

    async def fetch(self, c, dest):
        dest.mkdir(parents=True, exist_ok=True)
        return {"repo": c.repo, "commit": "0" * 40, "sha256": "x", "files": 1}


def check(target, out_dir=None, **kw):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shots = []
    for name in ("电脑_light", "手机_dark"):
        (out / f"{name}.png").write_bytes(PNG)
        shots.append(str(out / f"{name}.png"))
    return Report(target=target, shots=shots, combos=2)


def test_benchgen_flow(serve, page, monkeypatch, tmp_path):
    def factory(rules, *, root, **kw):
        return BenchBuilder(
            rules.model_copy(
                update={
                    "baseline": ["主视觉占大半屏", "不自己画插图"],
                    "project": BenchProject(enabled=False),  # 这一条测一口气做完的老流程
                    "team": BenchTeam(enabled=False),
                }
            ),
            root=tmp_path / "bench",
            supercomputer=SlowSC(),
            render=fake_render,
            check=check,
            scout=FakeScout(),
            researcher=FakeResearcher(),
            seed=3,
        )

    def fake_status():
        async def fetch(url):
            return 403 if "frankfurter" in url else 200

        return fetch

    monkeypatch.setattr(service_mod, "BenchBuilder", factory)
    monkeypatch.setattr(service_mod, "http_status", fake_status)
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    pane = page.locator('#bench [data-pane="gen"]')
    pane.locator("[data-text]").wait_for()
    assert page.locator('#bench [data-tab="gen"]').get_attribute("aria-selected") == "true"
    pane.locator("[data-text]").fill("帮我做一个东京三天旅行的工作台")
    pane.locator("[data-submit]").click()
    pane.locator(".bg-status.bg-done").wait_for(timeout=15000)

    assert pane.locator(".bg-title").text_content() == "旅行台"
    assert pane.locator(".bg-steps li.past").count() == 6  # 一轮就过线：返工那格跳过
    skipped = pane.locator(".bg-steps li.skip").all_text_contents()
    assert skipped == ["方案会（跳过）", "返工（跳过）"]
    assert pane.locator(".sc-subs tr", has_text="写好了").count() == 3
    research = pane.locator(".bg-research")
    head = research.locator("summary").text_content()
    assert "GitHub 10 个" in head and "学习卡 3 张" in head and "资讯和视频 1 条" in head
    assert research.locator(".bg-gh tbody tr").count() == 10
    assert "借：时间轴拖拽" in research.locator(".bg-cards").text_content()
    assert "发布会" in research.locator(".bg-news").text_content()
    src = pane.locator(".bg-sources")
    assert "直接用 1 个，只借思路 1 个" in src.locator("summary").text_content()
    assert src.locator(".bg-v-adopt").text_content() == "直接用"
    rnd = pane.locator(".bg-round").first
    assert "过线" in rnd.locator("summary").text_content()
    assert "验收 2/2" in rnd.locator("summary").text_content()
    assert rnd.locator(".bg-reqs li.bg-met").count() == 2
    assert "不自己画插图" in rnd.locator(".bg-reqs").text_content()
    assert rnd.locator(".bg-scores li").count() == 7
    shot = rnd.locator(".bg-shot img").first
    page.wait_for_function(
        "(img) => img.complete && img.naturalWidth > 0", arg=shot.element_handle()
    )
    assert "手机 · 深色" in rnd.locator(".bg-shots").text_content()

    pane.locator("[data-netcheck]").click()
    net = pane.locator(".bg-net")
    net.wait_for()
    assert "能连上" in net.locator("summary").text_content()
    miss = net.locator("li.bg-miss")
    assert miss.count() == 1 and "被网络或代理拦下" in miss.text_content()

    pane.locator("button[data-preview]").click()
    frame = page.frame_locator("iframe.bg-frame")
    frame.locator(".answer", has_text="先去浅草寺").wait_for(timeout=10000)
    assert frame.locator("h1").text_content() == "旅行台"
    frame.locator(".gen", has_text="生成了 1 天").wait_for(timeout=10000)
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):  # 版本说明用的截图
        page.set_viewport_size({"width": 1280, "height": 2600})
        page.screenshot(path=str(Path(shots) / "benchgen.png"))


def test_benchgen_project_room(serve, page, monkeypatch, tmp_path):
    """项目室：回答访谈 → 改验收清单签字 → 里程碑演示提意见 → 通过 → 交付验收 → 下一版提案。"""
    from ..core.benchkit.test_builder import ProjectSC

    def factory(rules, *, root, **kw):
        return BenchBuilder(
            rules.model_copy(
                update={
                    "baseline": [],
                    "project": BenchProject(enabled=True),
                    "team": BenchTeam(enabled=False),
                }
            ),
            root=tmp_path / "bench",
            supercomputer=ProjectSC(),
            render=fake_render,
            check=check,
            seed=3,
        )

    monkeypatch.setattr(service_mod, "BenchBuilder", factory)
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    pane = page.locator('#bench [data-pane="gen"]')
    pane.locator("[data-text]").fill("帮我做一个旅行台")
    pane.locator("[data-submit]").click()

    gate = pane.locator(".bg-gate")
    gate.locator("textarea[data-q]").first.wait_for(timeout=15000)
    assert pane.locator(".bg-stages li.now").text_content() == "立项"
    gate.locator("[data-opt]").first.click()  # 点选项填进回答
    assert gate.locator("textarea[data-q]").first.input_value() == "自己"
    gate.locator("[data-answer]").click()

    gate.locator("[data-sign]").wait_for(timeout=15000)
    assert gate.locator("li[data-req]").count() == 2
    gate.locator("li[data-req]").nth(1).locator("[data-delreq]").click()
    gate.locator("[data-addreq]").click()
    gate.locator("li[data-req]").nth(1).locator("input").fill("生日有惊喜")
    gate.locator("[data-note]").fill("给爸妈用")
    gate.locator("[data-sign]").click()

    gate.locator("[data-pass]").wait_for(timeout=15000)
    assert pane.locator(".bg-stages li.now").text_content() == "执行"
    assert gate.locator(".bg-demo img").count() >= 1
    gate.locator("[data-comments]").fill("地图再大一点")
    gate.locator("[data-cwin]").select_option("map-view")
    gate.locator("[data-change]").click()
    pane.locator(".bg-changes li", has_text="改好了").wait_for(timeout=15000)
    gate.locator("[data-pass]").wait_for(timeout=15000)
    assert gate.locator(".bg-demo figcaption", has_text="改之后").count() == 1
    gate.locator("[data-pass]").click()

    pane.locator(".bg-gate [data-pass]", has_text="验收通过").wait_for(timeout=15000)
    assert pane.locator(".bg-stages li.now").text_content() == "交付"
    pane.locator(".bg-gate [data-pass]").click()
    pane.locator("[data-evolve]").click()
    pane.locator(".bg-evolve li", has_text="加离线地图").wait_for(timeout=15000)
    pane.locator("[data-nextver]").click()
    assert "加离线地图" in pane.locator("[data-text]").input_value()
    pane.locator(".bg-timeline").evaluate("el => el.closest('details').open = true")
    assert "你签字开工" in pane.locator(".bg-timeline").text_content()
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):
        page.set_viewport_size({"width": 1280, "height": 2200})
        page.screenshot(path=str(Path(shots) / "benchgen_project.png"))


def test_benchgen_team_meeting_board_attack_and_plugin_request(serve, page, monkeypatch, tmp_path):
    """三位助手协作：方案会（主案）、协作看板、对抗手的严重问题挡住第 1 轮、申请给 dsh 装插件。"""
    from ..core.benchkit.test_builder import LearnScout
    from ..core.benchkit.test_team import PluginResearcher, TeamSC

    class TeamResearchSC(TeamSC, ResearchSC):
        pass

    bugs = {1: [{"window": "plan", "severity": "high", "problem": "空行程点保存会卡死"}]}

    def factory(rules, *, root, **kw):
        return BenchBuilder(
            rules.model_copy(
                update={
                    "baseline": [],
                    "project": BenchProject(enabled=False),
                    "team": BenchTeam(enabled=True),
                }
            ),
            root=tmp_path / "bench",
            supercomputer=TeamResearchSC(
                votes={"a": "c", "b": "c", "c": "a"}, bugs=lambda n: bugs.get(n, [])
            ),
            render=fake_render,
            check=check,
            scout=LearnScout(),
            researcher=PluginResearcher(),
            seed=3,
        )

    monkeypatch.setattr(service_mod, "BenchBuilder", factory)
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    pane = page.locator('#bench [data-pane="gen"]')
    pane.locator("[data-text]").fill("做一个修图台")
    pane.locator("[data-submit]").click()
    pane.locator(".bg-status.bg-done").wait_for(timeout=15000)

    team = pane.locator(".bg-team")
    assert "3 位助手协作" in team.locator("summary").text_content()
    assert team.locator(".bg-pitch").count() == 3
    lead = team.locator(".bg-pitch.bg-lead")
    assert lead.count() == 1 and "2 票" in lead.text_content() and "c 的思路" in lead.text_content()
    assert team.locator(".bg-votes li").count() == 3
    assert team.locator(".bg-col").count() == 3
    roles = team.locator(".bg-board").text_content()
    assert "主案候选" in roles and "实施" in roles and "对抗" in roles
    first = pane.locator(".bg-round").first
    first.evaluate("el => el.open = true")
    assert "没过线" in first.locator("summary").text_content()
    assert "空行程点保存会卡死" in first.locator(".bg-attack").text_content()
    assert first.locator(".bg-bug-high").count() == 1

    plugins = pane.locator(".bg-plugins")
    plugins.locator("[data-plugin]").first.click()
    plugins.locator("li", has_text="已申请").wait_for(timeout=10000)
    assert "dsh 插件" in pane.locator(".bg-cards").text_content()  # 插件也写了学习卡
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):
        page.set_viewport_size({"width": 1280, "height": 2600})
        team.screenshot(path=str(Path(shots) / "benchgen_team.png"))


def test_benchgen_stuck_lets_you_edit_requirements_or_accept(serve, page, monkeypatch, tmp_path):
    """做满轮数没过线：列出没做到的和建议，改要求再跑一轮，最后选「就这样」。"""
    from ..core.benchkit.test_builder import CheckSC, plan_with_requirements

    sc = CheckSC(unmet={"r1"}, plan=plan_with_requirements())

    def factory(rules, *, root, **kw):
        return BenchBuilder(
            rules.model_copy(
                update={
                    "baseline": [],
                    "max_rounds": 1,
                    "project": BenchProject(enabled=False),
                    "team": BenchTeam(enabled=False),
                }
            ),
            root=tmp_path / "bench",
            supercomputer=sc,
            render=fake_render,
            check=check,
            seed=3,
        )

    monkeypatch.setattr(service_mod, "BenchBuilder", factory)
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    pane = page.locator('#bench [data-pane="gen"]')
    pane.locator("[data-text]").fill("做一个婚礼策划台")
    pane.locator("[data-submit]").click()
    stuck = pane.locator(".bg-stuck")
    stuck.wait_for(timeout=15000)
    assert "你来定" in stuck.text_content() and "能按婚礼定制" in stuck.text_content()
    stuck.locator(".bg-edit summary").click()
    stuck.locator("li[data-req] input").first.fill("选婚礼后标题出现「婚礼」")
    stuck.locator('[data-decide="edit"]').click()
    pane.locator(".bg-round").nth(1).wait_for(timeout=15000)
    pane.locator(".bg-stuck").wait_for(timeout=15000)
    assert "选婚礼后标题出现「婚礼」" in pane.locator(".bg-stuck").text_content()
    pane.locator('[data-decide="accept"]').click()
    pane.locator(".bg-status.bg-done").wait_for(timeout=10000)
    assert pane.locator(".bg-stuck").count() == 0
