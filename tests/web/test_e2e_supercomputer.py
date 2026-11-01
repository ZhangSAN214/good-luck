"""前端：「超级电脑」。用假的命令行助手，不真跑任何程序。

分段栏「工作台」→ 超级电脑 → 看到三位助手和今天的次数 → 交活 → 「工作台」绿点、
表格里每件小活分给了谁、为什么 → 干完显示结果 → 改每天次数。
"""

from __future__ import annotations

import os
from pathlib import Path

import roundtable.core.service as service_mod
from roundtable.core.supercomputer import Supercomputer

from ..core.supercomputer.test_engine import Agents, Launcher, render, rules


def test_supercomputer_flow(serve, page, monkeypatch, tmp_path):
    lau = Launcher()

    def build(self, runtime, base):
        return Supercomputer(
            rules(),
            root=tmp_path / "super",
            render=render,
            launchers={"command": lau, "dsh": lau},
            runner=Agents(delay=0.6),
            seed=7,
        )

    monkeypatch.setattr(service_mod.RoundtableService, "_build_super", build)
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    page.locator('#bench [data-tab="super"]').click()  # 默认标签是生成器
    dlg = page.locator('#bench [data-pane="super"]')
    dlg.locator(".sc-worker", has_text="K").wait_for()
    assert dlg.locator(".sc-worker").count() == 3
    dlg.locator("[data-text]").fill("给项目加一个接口，附文档和测试")
    dlg.locator("[data-submit]").click()
    on = "document.querySelector('#entries [data-m=\"bench\"]').classList.contains('sc-on')"
    page.wait_for_function(on)
    dlg.get_by_role("cell", name="2. 写文档").wait_for(timeout=10000)
    dlg.locator(".sc-result", has_text="合并好了").wait_for(timeout=15000)
    row = dlg.locator(".sc-subs tr", has_text="2. 写文档").first
    assert "D" in row.text_content() and "分：擅长" in row.text_content()
    assert dlg.locator(".sc-st-done").count() == 3
    page.wait_for_function(f"!{on}")
    assert page.evaluate("location.hash") == "#m=bench"
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):  # 版本说明用的截图
        page.screenshot(path=str(Path(shots) / "supercomputer.png"))
    limit = dlg.locator("[data-limit='k']")
    limit.fill("5")
    limit.dispatch_event("change")
    page.wait_for_function(
        "fetch('/api/super').then(r => r.json())"
        ".then(j => j.workers.find(w => w.id === 'k').limit === 5)"
    )


def test_butler_panel(serve, page, monkeypatch, tmp_path):
    """管家：加定时活 → 列表里看到「每天 09:00」；忙时再交一件 → 排队；日报和审计链能展开。"""
    lau = Launcher()

    def build(self, runtime, base):
        return Supercomputer(
            rules(),
            root=tmp_path / "super",
            render=render,
            launchers={"command": lau, "dsh": lau},
            runner=Agents(delay=1.5),
            seed=7,
        )

    monkeypatch.setattr(service_mod.RoundtableService, "_build_super", build)
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    page.locator('#bench [data-tab="super"]').click()
    dlg = page.locator('#bench [data-pane="super"]')
    dlg.locator(".sc-worker", has_text="K").wait_for()
    dlg.locator("details[data-k='sched'] > summary").click()
    dlg.locator("[data-form='text']").fill("调研一下依赖有没有安全更新")
    dlg.locator("[data-add-sched]").click()
    sched = dlg.locator(".sc-sched", has_text="调研一下依赖")
    sched.wait_for()
    assert "每天 09:00" in sched.text_content() and "风险低" in sched.text_content()
    dlg.locator("[data-text]").fill("给项目加一个接口")
    dlg.locator("[data-submit]").click()
    dlg.locator("[data-submit]", has_text="排进队").wait_for()
    dlg.locator("[data-text]").fill("再补一份文档")
    dlg.locator("[data-submit]").click()
    dlg.locator(".sc-queue li", has_text="再补一份文档").wait_for()
    dlg.locator("details[data-k='report'] > summary").click()
    dlg.locator(".sc-report-sum", has_text="交了").wait_for()
    dlg.locator("details[data-k='audit'] > summary").click()
    dlg.locator("details[data-k='audit'] .sc-ok", has_text="完好").wait_for()
    assert dlg.locator(".sc-audit li", has_text="建定时活").count() == 1
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):
        page.screenshot(path=str(Path(shots) / "supercomputer_butler.png"), full_page=True)
    # 前一件干完，队里的马上接着干
    page.wait_for_function(
        "fetch('/api/super').then(r => r.json())"
        ".then(j => j.task && j.task.text === '再补一份文档')",
        timeout=30000,
    )
    dlg.locator(".sc-queue").wait_for(state="detached", timeout=10000)
