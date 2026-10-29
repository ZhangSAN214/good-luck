"""前端：「工作台」（dsh 边车）。用替身代替 dsh 进程，不真装 dsh。

分段栏「工作台」→ dsh 标签 → 启动 → 标签绿点、显示花费和插件、嵌入网页（刷新状态不重新加载）→ 停止。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import roundtable.core.service as service_mod


class FakeDsh:
    url = ""

    def __init__(self) -> None:
        self.state = "stopped"

    def status(self) -> dict[str, Any]:
        on = self.state == "running"
        return {
            "enabled": True,
            "installed": True,
            "unavailable": None,
            "runtime": "0.2.1-rc.3",
            "state": self.state,
            "url": self.url if on else None,
            "last_exit": None if on else "你停止了 dsh",
            "stopped_for_budget": None,
            "log": [],
            "key_env": "DSH_KEY",
            "key_present": True,
            "permission_mode": "read-only",
            "plugins": [
                {
                    "id": "worktable",
                    "name": "工作台面板",
                    "version": "0.5.0",
                    "kind": "panel",
                    "license": "MIT",
                    "enabled": True,
                    "note": "终端窗格不受只读模式约束",
                }
            ],
            "budget": {
                "spent_usd": 0.42,
                "limit_usd": 3.0,
                "balance": 51.3,
                "currency": "CNY",
                "tracking": True,
                "error": None,
            },
        }

    async def start(self) -> dict[str, Any]:
        self.state = "running"
        return self.status()

    async def stop(self, why: str = "") -> dict[str, Any]:
        self.state = "stopped"
        return self.status()

    async def check_now(self) -> dict[str, Any]:
        return self.status()

    async def aclose(self) -> None:
        self.state = "stopped"

    # 超级电脑的启动器要用（这里不派活给它）
    def job_blocked(self) -> str | None:
        return "测试里不派活"

    def job_launch(self):
        raise AssertionError("不该派活")

    async def job_started(self) -> None: ...

    async def job_finished(self) -> None: ...


def test_workbench_start_and_stop(serve, page, monkeypatch):
    fake = FakeDsh()
    monkeypatch.setattr(service_mod.RoundtableService, "_build_dsh", staticmethod(lambda *a: fake))
    srv = serve(confirm_threshold_usd=100.0)
    fake.url = f"{srv.url}/login.html?token=abc"  # 随便一个本机页面代替 dsh 网页
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    btn = page.locator('#bench [data-tab="dsh"]')
    btn.click()
    dlg = page.locator('#bench [data-pane="dsh"]')
    dlg.get_by_text("本月约 $0.42 / 上限 $3.00").wait_for()
    assert "51.30 CNY" in dlg.text_content()
    dlg.locator("[data-start]").click()
    page.wait_for_function(
        "document.querySelector('#bench [data-tab=\"dsh\"]').classList.contains('wb-on')"
    )
    frame = dlg.locator("iframe.wb-frame")
    frame.wait_for()
    assert frame.get_attribute("src") == fake.url
    page.wait_for_function(
        "document.querySelector('#bench iframe.wb-frame').contentDocument?.readyState === 'complete'"
    )
    # 标在网页里面：iframe 被拿出来再放回去时网页会重新加载，标记就没了
    frame.evaluate("f => (f.contentWindow.__mark = 'same')")
    dlg.locator("summary", has_text="白名单插件（1）").click()
    dlg.get_by_text("终端窗格不受只读模式约束").wait_for()
    page.wait_for_timeout(4500)  # 打开时每 4 秒刷新一次状态
    assert dlg.locator("iframe.wb-frame").evaluate("f => f.contentWindow.__mark") == "same"
    assert dlg.get_by_text("终端窗格不受只读模式约束").is_visible()  # 展开的插件列表没被收起
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):  # 版本说明用的截图
        page.screenshot(path=str(Path(shots) / "workbench.png"))
    dlg.locator("[data-stop]").click()
    dlg.locator(".wb-state", has_text="已停止").wait_for()
    assert dlg.locator("iframe").count() == 0
    assert not btn.evaluate("b => b.classList.contains('wb-on')")


def test_workbench_hidden_without_dsh(serve, page, monkeypatch):
    monkeypatch.setattr(service_mod.RoundtableService, "_build_dsh", staticmethod(lambda *a: None))
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_function("fetch('/api/dsh').then(r => r.json()).then(j => j.enabled === false)")
    page.wait_for_timeout(300)
    assert page.locator("#workbenchbtn").is_hidden()
