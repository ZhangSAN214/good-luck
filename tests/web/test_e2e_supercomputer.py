"""前端：「超级电脑」。用假的命令行助手，不真跑任何程序。

顶栏「超级电脑」→ 看到三位助手和今天的次数 → 交活 → 顶栏绿点、表格里每件小活分给了谁、为什么 →
干完显示结果 → 改每天次数。
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
    btn = page.locator("#superbtn")
    btn.wait_for()
    btn.click()
    dlg = page.locator("#superdlg")
    dlg.locator(".sc-worker", has_text="K").wait_for()
    assert dlg.locator(".sc-worker").count() == 3
    dlg.locator("[data-text]").fill("给项目加一个接口，附文档和测试")
    dlg.locator("[data-submit]").click()
    page.wait_for_function("document.querySelector('#superbtn').classList.contains('sc-on')")
    dlg.get_by_role("cell", name="2. 写文档").wait_for(timeout=10000)
    dlg.locator(".sc-result", has_text="合并好了").wait_for(timeout=15000)
    row = dlg.locator(".sc-subs tr", has_text="2. 写文档").first
    assert "D" in row.text_content() and "分：擅长" in row.text_content()
    assert dlg.locator(".sc-st-done").count() == 3
    page.wait_for_function("!document.querySelector('#superbtn').classList.contains('sc-on')")
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):  # 版本说明用的截图
        page.screenshot(path=str(Path(shots) / "supercomputer.png"))
    limit = dlg.locator("[data-limit='k']")
    limit.fill("5")
    limit.dispatch_event("change")
    page.wait_for_function(
        "fetch('/api/super').then(r => r.json())"
        ".then(j => j.workers.find(w => w.id === 'k').limit === 5)"
    )
