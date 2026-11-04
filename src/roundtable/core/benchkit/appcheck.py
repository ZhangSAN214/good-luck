"""应用型产出的企业级验收（docs/KIT.md「K5」）：在真浏览器里打开应用，跑底座的页内自检
（``/kit/<底座>/selftest.js``）和各功能自带的测试，截图，没过的变成质检问题交回作者。

验收项（门槛来自配置 ``bench.apps``，写进成品的 ``app.json``）：

- 撤销至少 ``undo_min`` 步，撤销、重做后画面都对得上
- 打开 ``big_px`` 像素宽的大图、调一次亮度都在 ``big_max_s`` 秒内，页面不卡死
- 导出 PNG 的尺寸和画布一样
- 打开坏文件时页面上有出错提示（``role="alert"``），不多出图层
- 快捷键（撤销、重做、复制图层）能用
- 功能模块导出的 ``test(t)`` 都通过

本机静态服务和放行规则同 ``quality.run_check``
（只放行本机和 ``bench.net`` 的公开数据网址）。不调模型。
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from roundtable.core.benchkit.quality import Finding, QualityError, _gate, _serve

# 自检项 → 质检问题的名字（技能配置 bench.skills.checks.editor 引用这些名字，测试核对）
CHECK_NAMES = {
    "k5-undo": "撤销步数不够",
    "k5-big": "大图卡死",
    "k5-export": "导出尺寸变了",
    "k5-error": "出错没有提示",
    "k5-keys": "快捷键不灵",
}
FEATURE_FAIL = "应用自带测试没过"
NOT_READY = "底座没起来"
SHOT_IMAGE = (1200, 800)  # 截图前先放进去的测试图
SHOT_WAIT_MS = 15000  # 放测试图最多等这么久
WHERE = "企业级验收"


@dataclass
class AppReport:
    target: str
    results: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    shots: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "block" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "passed": self.passed,
            "total": len(self.results),
            "ok": sum(1 for r in self.results if r.get("ok")),
            "results": self.results,
            "findings": [asdict(f) for f in self.findings],
            "shots": self.shots,
        }

    def summary(self) -> str:
        if not self.results and not self.findings:
            return "企业级验收：没有跑"
        ok = sum(1 for r in self.results if r.get("ok"))
        lines = [f"企业级验收：{ok}/{len(self.results)} 项通过"]
        lines += [f"- [必须修] {f.check}：{f.detail}" for f in self.findings]
        return "\n".join(lines)


def findings_of(
    results: list[dict[str, Any]], features: list[str], errors: list[str] | None = None
) -> list[Finding]:
    """自检结果 → 质检问题。底座那几项没过，多半是某个功能改坏了底座的行为，交给所有功能的作者看；
    功能自带的测试没过只交给那个功能。"""
    out: list[Finding] = []
    for r in results:
        if r.get("ok"):
            continue
        rid = str(r.get("id") or "")
        detail = str(r.get("detail") or "")[:300]
        if rid in CHECK_NAMES:
            out.append(
                Finding(
                    CHECK_NAMES[rid],
                    "block",
                    WHERE,
                    f"{r.get('name')}：{detail}（底座本身测过是好的，多半是某个功能改了底座的行为；"
                    "别改 /kit/ 里的文件，只通过 editor 的接口改画面）",
                    list(features),
                )
            )
        else:
            feature = str(r.get("feature") or "")
            out.append(
                Finding(
                    FEATURE_FAIL,
                    "block",
                    WHERE,
                    f"「{r.get('name')}」：{detail}",
                    [feature] if feature in features else list(features),
                )
            )
    errs = [e for e in errors or [] if "Failed to load resource" not in e]
    if errs:
        wins = sorted({m for e in errs for m in re.findall(r"窗口 ([a-z0-9-]+) 出错", e)})
        out.append(
            Finding(
                "页面报错",
                "block",
                WHERE,
                f"自检时脚本报错 {len(errs)} 次，如：{errs[0][:200]}",
                wins or list(features),
            )
        )
    return out


def run_app_check(
    target: str,
    out_dir: str | Path | None = None,
    *,
    allow: tuple[str, ...] = (),
    checks: dict[str, Any] | None = None,
    ready_s: float = 30,
) -> AppReport:
    """验收一个应用型成品（本地 index.html，旁边要有 app.json）。"""
    try:
        from playwright.sync_api import Error as PwError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise QualityError(
            "没有安装 Playwright：pip install playwright，再运行 playwright install chromium"
        ) from None
    path = Path(target)
    if not path.is_file():
        raise QualityError(f"找不到文件：{target}")
    meta: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        meta = json.loads((path.parent / "app.json").read_text(encoding="utf-8"))
    features = [str(f.get("id")) for f in meta.get("features") or [] if f.get("id")]
    opts = {**(meta.get("checks") or {}), **(checks or {})}
    big_s = float(opts.get("bigMaxMs", 8000)) / 1000
    report = AppReport(target=target)
    out = Path(out_dir) if out_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)

    with contextlib.ExitStack() as stack:
        base = stack.enter_context(_serve(path.resolve().parent))
        url = f"{base}/{path.name}"
        local_hosts = ("127.0.0.1", "localhost", urlsplit(url).hostname or "")
        try:
            pw = stack.enter_context(sync_playwright())
            browser = pw.chromium.launch(
                executable_path=os.environ.get("ROUNDTABLE_CHROMIUM") or None
            )
        except PwError as exc:
            raise QualityError(f"浏览器启动失败：{str(exc).splitlines()[0][:160]}") from None
        stack.callback(browser.close)
        gate = functools.partial(_gate, local_hosts, tuple(allow))

        def page_at(w: int, h: int, errors: list[str]):
            page = browser.new_page(viewport={"width": w, "height": h}, reduced_motion="reduce")
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("dialog", lambda d: d.dismiss())
            page.route("**/*", gate)
            return page

        # 1) 截图：电脑和手机各一张，先放一张测试图进去（不带示例图也看得出界面）
        errors: list[str] = []
        for name, w, h in (("电脑", 1280, 860), ("手机", 390, 844)):
            page = page_at(w, h, errors)
            try:
                page.goto(f"{url}?blank=1", wait_until="load", timeout=20000)
                page.wait_for_function(
                    "document.documentElement.dataset.app === 'ready'", timeout=ready_s * 1000
                )
            except PwError as exc:
                report.findings.append(
                    Finding(
                        NOT_READY,
                        "block",
                        WHERE,
                        f"{ready_s:g} 秒内应用没有准备好（{str(exc).splitlines()[0][:120]}）",
                        list(features),
                    )
                )
                page.close()
                report.findings += findings_of([], features, errors)
                return _save(report, out)
            with contextlib.suppress(PwError):
                page.evaluate(
                    # 页面卡住时 evaluate 自己不会超时：最多等 SHOT_WAIT_MS，截图照截
                    """async ([w, h, ms]) => Promise.race([
                      (async () => {
                        const { sampleBlob } = await import('/kit/editor/selftest.js');
                        const blob = await sampleBlob(w, h);
                        await window.__editor.importFile(blob, { name: '截图用的测试图' });
                      })(),
                      new Promise((r) => setTimeout(r, ms)),
                    ])""",
                    [*SHOT_IMAGE, SHOT_WAIT_MS],
                )
                page.wait_for_timeout(300)
            if out:
                shot = out / f"应用_{name}.png"
                page.screenshot(path=str(shot), full_page=name == "手机")
                report.shots.append(str(shot))
            page.close()

        # 2) 自检：?selftest=1 打开，页面自己跑完把结果放在 window.__selftest
        run_errors: list[str] = []
        page = page_at(1280, 860, run_errors)
        budget = (big_s * 4 + 60) * 1000
        try:
            page.goto(f"{url}?selftest=1", wait_until="load", timeout=20000)
            page.wait_for_function("window.__selftest", timeout=budget)
            got = page.evaluate("window.__selftest")
            report.results = list(got.get("results") or [])
        except PwError as exc:
            report.findings.append(
                Finding(
                    CHECK_NAMES["k5-big"],
                    "block",
                    WHERE,
                    f"自检 {budget / 1000:.0f} 秒没跑完，页面可能卡死了"
                    f"（{str(exc).splitlines()[0][:120]}）",
                    list(features),
                )
            )
        if out:
            with contextlib.suppress(PwError):
                shot = out / "应用_自检.png"
                page.screenshot(path=str(shot))
                report.shots.append(str(shot))
        page.close()
        report.findings += findings_of(report.results, features, errors + run_errors)
    return _save(report, out)


def _save(report: AppReport, out: Path | None) -> AppReport:
    if out:
        (out / "app_report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report
