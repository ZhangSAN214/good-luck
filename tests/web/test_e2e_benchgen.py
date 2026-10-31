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
    assert pane.locator(".bg-steps li.past").count() == 7  # 7 步都走完
    assert "第 7/7 步" in pane.locator(".bg-top").text_content()
    assert pane.locator(".sc-subs tr", has_text="写好了").count() == 3
    pane.locator('[data-drawer="research"]').click()  # 资料都在底部抽屉里
    research = pane.locator(".bg-research")
    head = research.locator("summary").text_content()
    assert "GitHub 10 个" in head and "学习卡 3 张" in head and "资讯和视频 1 条" in head
    assert research.locator(".bg-gh tbody tr").count() == 10
    assert "借：时间轴拖拽" in research.locator(".bg-cards").text_content()
    assert "发布会" in research.locator(".bg-news").text_content()
    pane.locator('[data-drawer="sources"]').click()
    src = pane.locator(".bg-sources")
    assert "直接用 1 个，只借思路 1 个" in src.locator("summary").text_content()
    assert src.locator(".bg-v-adopt").text_content() == "直接用"
    assert "验收 2/2" in pane.locator(".bg-plan").text_content()  # 右栏有最近一轮的结果
    pane.locator('[data-drawer="rounds"]').click()
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

    frame = page.frame_locator("iframe.bg-frame")  # 做好了中间直接就是成品
    frame.locator(".answer", has_text="先去浅草寺").wait_for(timeout=10000)
    assert frame.locator("h1").text_content() == "旅行台"
    frame.locator(".gen", has_text="生成了 1 天").wait_for(timeout=10000)
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):  # 版本说明用的截图
        page.set_viewport_size({"width": 1440, "height": 900})
        page.screenshot(path=str(Path(shots) / "benchgen.png"))


def test_benchgen_project_room(serve, page, monkeypatch, tmp_path):
    """项目室：理想画像 + 取舍 → 改验收清单签字 → 分段演示提意见 → 通过 → 交付验收 → 下一版提案。"""
    from ..core.benchkit.test_builder import IntentSC

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
            supercomputer=IntentSC(),
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
    assert "问取舍" in pane.locator(".bg-flow li.now").text_content()
    ideal = pane.locator(".bg-stage .bg-idealpage")  # 还没成品时理想画像放大摆在中间
    assert "带爸妈出游也不手忙脚乱" in ideal.locator(".bg-idealbig").text_content()
    assert ideal.locator('.bg-lv[data-lv="确定"]').count() == 1  # 排三天行程（原话）
    assert "推荐" in gate.locator(".chip.rec").first.text_content()
    gate.locator("[data-opt]").first.click()  # 点选项填进回答
    assert gate.locator("textarea[data-q]").first.input_value() == "自己"
    gate.locator("[data-answer]").click()

    gate.locator("[data-sign]").wait_for(timeout=15000)
    intent = pane.locator(".bg-client .bg-intent")  # 签字时中间是选型预览，理想画像回到左栏
    assert intent.locator('.bg-lv[data-lv="确定"]').count() == 2  # 选了「自己」：给谁用变成确定
    assert pane.locator(".bg-stages li").count() == 2  # 方案里的分段
    assert "你能试：换目的地 → 看行程" in pane.locator(".bg-stages li").first.text_content()
    assert pane.locator(".bg-trace li").count() == 2  # 开工那句 + 访谈回答
    assert gate.locator("li[data-req]").count() == 2
    gate.locator("li[data-req]").nth(1).locator("[data-delreq]").click()
    gate.locator("[data-addreq]").click()
    gate.locator("li[data-req]").nth(1).locator("input").fill("生日有惊喜")
    gate.locator("[data-note]").fill("给爸妈用")
    gate.locator("[data-sign]").click()

    gate.locator("[data-pass]").wait_for(timeout=15000)
    assert "自动搭" in pane.locator(".bg-steps li.now").text_content()
    assert pane.locator(".bg-plan .bg-demo img").count() >= 1  # 演示截图在右栏
    page.frame_locator("iframe.bg-frame").locator("body").wait_for()  # 中间是这一段的草稿
    gate.locator("[data-comments]").fill("地图再大一点")
    gate.locator("[data-cwin]").select_option("map-view")
    gate.locator("[data-change]").click()
    pane.locator('.bg-trace li[data-state="改好了"]').wait_for(state="attached", timeout=15000)
    gate.locator("[data-pass]").wait_for(timeout=15000)
    assert pane.locator(".bg-demo figcaption", has_text="改之后").count() == 1
    gate.locator("[data-pass]").click()

    pane.locator(".bg-gate [data-pass]", has_text="验收通过").wait_for(timeout=15000)
    assert pane.locator(".bg-stages li", has_text="第 2 段 · 主要功能").count() == 1
    assert "交付" in pane.locator(".bg-steps li.now").text_content()
    pane.locator(".bg-gate [data-pass]").click()
    pane.locator("[data-evolve]").click()
    pane.locator(".bg-evolve li", has_text="加离线地图").wait_for(timeout=15000)
    pane.locator('[data-drawer="log"]').click()
    assert "你签字开工" in pane.locator(".bg-timeline").text_content()
    page.keyboard.press("Escape")  # 收起抽屉
    pane.locator("[data-nextver]").click()
    assert "加离线地图" in pane.locator("[data-text]").input_value()
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):
        page.set_viewport_size({"width": 1440, "height": 900})
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

    pane.locator('[data-drawer="team"]').click()
    team = pane.locator(".bg-team")
    assert "3 位助手协作" in team.locator("summary").text_content()
    assert team.locator(".bg-pitch").count() == 3
    lead = team.locator(".bg-pitch.bg-lead")
    assert lead.count() == 1 and "2 票" in lead.text_content() and "c 的思路" in lead.text_content()
    assert team.locator(".bg-votes li").count() == 3
    assert team.locator(".bg-col").count() == 3
    roles = team.locator(".bg-board").text_content()
    assert "主案候选" in roles and "实施" in roles and "对抗" in roles
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):
        page.set_viewport_size({"width": 1440, "height": 900})
        page.screenshot(path=str(Path(shots) / "benchgen_team.png"))
    pane.locator('[data-drawer="rounds"]').click()
    first = pane.locator(".bg-round").first
    first.evaluate("el => el.open = true")
    assert "没过线" in first.locator("summary").text_content()
    assert "空行程点保存会卡死" in first.locator(".bg-attack").text_content()
    assert first.locator(".bg-bug-high").count() == 1

    pane.locator('[data-drawer="research"]').click()
    plugins = pane.locator(".bg-plugins")
    plugins.locator("[data-plugin]").first.click()
    plugins.locator("li", has_text="已申请").wait_for(timeout=10000)
    assert "dsh 插件" in pane.locator(".bg-cards").text_content()  # 插件也写了学习卡


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
    stuck = pane.locator(".bg-stuck-form")  # 左栏：你来定
    stuck.wait_for(timeout=15000)
    assert "你来定" in stuck.text_content()
    assert "能按婚礼定制" in pane.locator(".bg-plan .bg-stuck").text_content()  # 右栏：卡在哪
    stuck.locator(".bg-edit summary").click()
    stuck.locator("li[data-req] input").first.fill("选婚礼后标题出现「婚礼」")
    stuck.locator('[data-decide="edit"]').click()
    pane.locator(".bg-top", has_text="第 2 轮").wait_for(timeout=15000)
    pane.locator(".bg-stuck-form").wait_for(timeout=15000)
    assert "选婚礼后标题出现「婚礼」" in pane.locator(".bg-plan .bg-stuck").text_content()
    pane.locator('[data-decide="accept"]').click()
    pane.locator(".bg-status.bg-done").wait_for(timeout=10000)
    assert pane.locator(".bg-stuck-form").count() == 0


def test_benchgen_design_board_live_preview(serve, page, monkeypatch, tmp_path):
    """签字前的选型台：点版型、配色、材质，右边预览马上变；去掉窗口、调宽窄；签字后成品照着排。"""
    from ..core.benchkit.test_builder import PROJECT_PLAN, ProjectSC

    sc = ProjectSC()
    sc.plan = PROJECT_PLAN | {
        "design": {
            "picks": {"layout": "hero-side", "material": "paper"},
            "why": {"layout": "边看地图边改行程"},
            "refs": [{"name": "Google 旅行", "borrow": "地图和行程并排"}],
            "tagline": "说走就走",
        }
    }

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
            supercomputer=sc,
            render=fake_render,
            check=check,
            seed=3,
        )

    monkeypatch.setattr(service_mod, "BenchBuilder", factory)
    srv = serve(confirm_threshold_usd=100.0)
    page.set_viewport_size({"width": 1400, "height": 1000})
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    pane = page.locator('#bench [data-pane="gen"]')
    pane.locator("[data-text]").fill("帮我做一个旅行台")
    pane.locator("[data-submit]").click()
    pane.locator(".bg-gate [data-skip]").click(timeout=15000)

    gate = pane.locator(".bg-gate")
    board = gate.locator(".bg-design")
    board.wait_for(timeout=15000)
    rec = board.locator('[data-dz="layout"][aria-pressed="true"]')
    assert "主视觉 + 侧栏" in rec.text_content() and "推荐" in rec.text_content()
    assert "边看地图边改行程" in board.text_content()
    view = page.frame_locator("[data-dpreview]")
    cards = view.locator("[data-pw]")
    cards.first.wait_for(timeout=10000)
    assert cards.count() == 3
    assert cards.first.get_attribute("data-span") == "8"
    assert "说走就走" in view.locator(".k-display").text_content()
    root = view.locator("html")
    assert root.get_attribute("data-k-material") == "paper"

    gate.locator("li[data-req] input").first.fill("能换目的地和日期")  # 先改要求，再点选型也不丢
    board.locator('[data-dz="layout"]', has_text="主视觉通栏").click()
    assert cards.first.get_attribute("data-span") == "12"
    board.locator(".bg-dgroup summary", has_text="质感").click()  # 前两组默认展开，其余点开
    board.locator('[data-dz="material"]', has_text="液态玻璃").click()
    board.locator('[data-dz="theme"]', has_text="森绿").click()
    pane.locator('[data-dz="mode"][data-v="dark"]').click()  # 浅色 / 深色在中间预览的上方
    assert root.get_attribute("data-k-material") == "glass"
    assert root.get_attribute("data-k-theme") == "forest"
    assert root.get_attribute("data-k-mode") == "dark"
    board.locator('[data-ddrop="plan"]').uncheck()
    assert cards.count() == 2
    board.locator('[data-dspan="budget"]').select_option("6")
    assert view.locator('[data-pw="budget"]').get_attribute("data-span") == "6"
    pane.locator('[data-dz="device"][data-v="phone"]').click()
    assert pane.locator("[data-dframe]").get_attribute("data-device") == "phone"
    assert gate.locator("li[data-req] input").first.input_value() == "能换目的地和日期"
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):
        pane.locator('[data-dz="device"][data-v="desk"]').click()
        page.set_viewport_size({"width": 1440, "height": 900})
        page.screenshot(path=str(Path(shots) / "benchgen_design.png"))

    gate.locator("[data-sign]").click()
    gate.locator("[data-pass]").wait_for(timeout=15000)
    folder = next((tmp_path / "bench").glob("*/spec.json")).parent
    spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
    assert spec["theme"] == "forest" and spec["design"]["picks"]["material"] == "glass"
    assert [(w["id"], w["span"]) for w in spec["windows"]] == [("map-view", 12), ("budget", 6)]
    assert spec["requirements"][0]["text"] == "能换目的地和日期"
    assert 'data-k-material="glass"' in (folder / "index.html").read_text(encoding="utf-8")


def png(w=48, h=32, rgb=(255, 122, 0)):
    """一张纯色 PNG（不靠图片库）。"""
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    head = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", head)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# 在页面里用画布录一段 1.5 秒的 webm（不带视频文件进仓库）
RECORD_WEBM = """async () => {
  const c = document.createElement('canvas'); c.width = 160; c.height = 120;
  const g = c.getContext('2d'); let n = 0;
  const paint = () => { g.fillStyle = n++ % 2 ? '#f70' : '#111'; g.fillRect(0, 0, 160, 120) };
  const tick = setInterval(paint, 50);
  const rec = new MediaRecorder(c.captureStream(20), { mimeType: 'video/webm' });
  const parts = []; rec.ondataavailable = (e) => parts.push(e.data);
  const done = new Promise((ok) => (rec.onstop = ok));
  rec.start(); await new Promise((ok) => setTimeout(ok, 1500));
  rec.stop(); await done; clearInterval(tick);
  const buf = new Uint8Array(await new Blob(parts, { type: 'video/webm' }).arrayBuffer());
  let s = ''; for (const b of buf) s += String.fromCharCode(b); return btoa(s);
}"""


def test_benchgen_materials_drop_look_and_use_in_product(serve, page, monkeypatch, tmp_path):
    """拖进参考图和视频（浏览器截帧）→ 能看图的助手看一遍 → 选型台显示结论和色块，
    标成 Logo、点色块当主色、改按钮形状、写风格要求 → 预览马上用上 → 签字后成品也用上。"""
    from ..core.benchkit.test_materials import LookSC

    sc = LookSC()

    def factory(rules, *, root, **kw):
        return BenchBuilder(
            rules.model_copy(
                update={
                    "baseline": [],
                    "project": BenchProject(enabled=True, interview=False),
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
    page.set_viewport_size({"width": 1400, "height": 1000})
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    pane = page.locator('#bench [data-pane="gen"]')
    pane.locator("[data-text]").fill("帮我做一个修图台")
    files = page.locator("input[data-benchfile]")  # 在面板外面，刷新不会换掉
    files.set_input_files([{"name": "参考.png", "mimeType": "image/png", "buffer": png()}])
    pane.locator(".bg-drop .bg-mat").first.wait_for(timeout=10000)
    webm = base64.b64decode(page.evaluate(RECORD_WEBM))
    files.set_input_files([{"name": "演示.webm", "mimeType": "video/webm", "buffer": webm}])
    video = pane.locator(".bg-drop .bg-mat", has_text="演示.webm")
    video.wait_for(timeout=20000)
    assert "截了" in video.text_content() and "截了 0 帧" not in video.text_content()
    pane.locator("[data-submit]").click()

    board = pane.locator(".bg-design")
    board.wait_for(timeout=15000)
    assert "暖橙、编辑器式三栏" in board.text_content()
    rows = board.locator(".bg-dmats .bg-mat")
    assert rows.count() == 2  # 截帧不单列
    assert "看过" in rows.first.text_content()
    look = next(json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_look")
    assert look["files"].count("截帧") >= 1

    view = page.frame_locator("[data-dpreview]")
    root = view.locator("html")
    view.locator("[data-pw]").first.wait_for(timeout=10000)
    board.locator('[data-swatch="#ff7a00"]').click()
    assert "--k-accent: #ff7a00" in (root.get_attribute("style") or "")
    mid = rows.first.locator("select").get_attribute("data-drole")
    rows.first.locator("select").select_option("logo")
    assert view.locator(".k-logo-img img").count() == 1
    board.locator(".bg-dgroup summary", has_text="控件").click()
    board.locator('[data-dz="button"]', has_text="胶囊").click()
    assert root.get_attribute("data-k-button") == "pill"
    board.locator('[data-dz="cardhead"]', has_text="彩色顶条").click()  # 点完分组不收起
    assert root.get_attribute("data-k-cardhead") == "bar"
    board.locator("[data-dnote]").fill("按钮要大一点")
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):
        page.set_viewport_size({"width": 1400, "height": 2600})
        board.screenshot(path=str(Path(shots) / "benchgen_materials.png"))

    pane.locator(".bg-gate [data-sign]").click()
    pane.locator(".bg-gate [data-pass]").wait_for(timeout=15000)
    folder = next((tmp_path / "bench").glob("*/spec.json")).parent
    spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
    d = spec["design"]
    assert d["picks"]["accent"] == "#ff7a00" and d["picks"]["button"] == "pill"
    assert d["note"] == "按钮要大一点"
    assert [(a["id"], a["role"]) for a in d["assets"]] == [(mid, "logo")]
    assert (folder / d["assets"][0]["file"]).is_file()
    assert d["assets"][0]["file"] in (folder / "index.html").read_text(encoding="utf-8")


def test_benchgen_desk_drawer_rank_feed_caps_and_suggestions(serve, page, monkeypatch, tmp_path):
    """资料台（第 5 批）：打开「资料」→ 过期就后台刷新 → 榜单分类 / 新星、动态、能力地图按类型筛、
    建议卡点「加进地图」。GitHub、论文和订阅都是假的。"""
    from roundtable.core.benchkit.desk import Desk

    from ..core.benchkit.test_desk import Net, gh

    net = Net(
        rank={
            "topic%3A3d": [gh("mrdoob/three.js", 105000, desc="JavaScript 3D library")],
            "topic%3Agame-engine": [gh("godotengine/godot", 99000, desc="Godot Engine")],
            "topic%3Alive2d": [gh("y/live2d-kit", 800, "Apache-2.0", desc="Live2D kit")],
        }
    )

    def desk_factory(rules, root, **kw):
        return Desk(
            rules, tmp_path / "desk", fetch_json=net.json, fetch_text=net.text, sleep=net.sleep
        )

    monkeypatch.setattr(service_mod, "Desk", desk_factory)
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    pane = page.locator('#bench [data-pane="gen"]')
    pane.locator("[data-text]").wait_for()
    pane.locator('[data-drawer="desk"]').click()
    rank = pane.locator(".bg-rank li")
    rank.first.wait_for(timeout=15000)
    assert "three.js" in rank.first.inner_text() and "105,000" in rank.first.inner_text()
    pane.locator('[data-desk-cat="game"]').click()
    assert "godot" in pane.locator(".bg-rank li").first.inner_text()
    pane.locator('[data-desk-mode="rising"]').click()
    assert pane.locator('[data-desk-mode="rising"]').get_attribute("aria-pressed") == "true"

    pane.locator('[data-desk-tab="feed"]').click()
    feed = pane.locator(".bg-feed li")
    assert feed.count() >= 2 and pane.locator('.bg-feed .bg-tag[data-kind="paper"]').count()

    pane.locator('[data-desk-tab="caps"]').click()
    total = pane.locator(".bg-caps tbody tr").count()
    pane.locator('[data-desk-kind="game"]').click()
    games = pane.locator(".bg-caps tbody tr")
    assert 0 < games.count() < total and "Phaser" in pane.locator(".bg-caps").inner_text()

    pane.locator('[data-desk-tab="suggest"]').click()
    card = pane.locator(".bg-sugg li", has_text="y/live2d-kit")
    card.locator('[data-accept="1"]').click()
    card.wait_for(state="detached")
    pane.locator('[data-desk-tab="caps"]').click()
    pane.locator('[data-desk-kind=""]').click()
    assert "你加的" in pane.locator(".bg-caps").inner_text()


def test_benchgen_profile_and_from_product(serve, page, monkeypatch, tmp_path):
    """第 6 批：「你的偏好」写一条、删一条；拖进成品后勾「这是成品」，开工时带上反推。"""
    from ..core.benchkit.test_builder import BlindSC

    built = {}

    def factory(rules, *, root, **kw):
        b = BenchBuilder(
            rules.model_copy(
                update={"project": BenchProject(enabled=False), "team": BenchTeam(enabled=False)}
            ),
            root=tmp_path / "bench",
            supercomputer=BlindSC(),
            render=fake_render,
            check=check,
            seed=3,
        )
        built["b"] = b
        return b

    monkeypatch.setattr(service_mod, "BenchBuilder", factory)
    srv = serve(confirm_threshold_usd=100.0)
    page.set_viewport_size({"width": 1400, "height": 1000})
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    pane = page.locator('#bench [data-pane="gen"]')
    prof = pane.locator(".bg-profile")
    pane.locator("[data-text]").wait_for()
    prof.wait_for()
    assert "0 条" in prof.locator("summary").inner_text()
    prof.locator("[data-profile-text]").fill("给小白用，别堆专业参数")
    prof.locator("[data-profile-add]").click()
    pane.locator(".bg-profile summary", has_text="1 条").wait_for()
    pane.locator(".bg-profile summary").click()  # 有了以后默认收起
    pane.locator(".bg-prof li", has_text="给小白用").wait_for()

    pane.locator("[data-text]").fill("照着这个动图做一个能换角色和动作的台")
    assert pane.locator("[data-fromprod]").count() == 0  # 没有素材时不出现
    files = page.locator("input[data-benchfile]")
    files.set_input_files([{"name": "鲸鱼女孩.png", "mimeType": "image/png", "buffer": PNG}])
    pane.locator(".bg-drop .bg-mat").first.wait_for(timeout=10000)
    pane.locator(".bg-fromprod").click()
    assert pane.locator("[data-fromprod]").is_checked()
    pane.locator("[data-submit]").click()
    page.wait_for_function("() => true")
    end = __import__("time").monotonic() + 10
    while not built["b"].build and __import__("time").monotonic() < end:
        page.wait_for_timeout(100)
    b = built["b"].build
    assert b["from_product"] is True
    assert any(v["src"] == "档案" and "小白" in v["text"] for v in b["voices"])
