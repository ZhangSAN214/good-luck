"""工作台窗口的自动质检（docs/KIT.md）：生成的页面不达标就打回，不交给用户。

用 Playwright 在三种屏宽 × 浅色 / 深色下打开页面，量出一组硬指标：
报错、横向溢出、出现 undefined / NaN 之类的坏字、没用设计套件、外链脚本、
没有名字的按钮、没写说明的图片、字太小、文字对比度不够、手机上按钮太小、图表空白。
每种组合截一张图，和报告一起放进输出目录。

纯函数（颜色解析、对比度、判定）不依赖浏览器，单独测试；没装 Playwright 时
``run_check`` 抛 ``QualityError``，调用方给出安装提示。不调用模型、不联网
（页面里对外网的请求一律中止）。
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import json
import os
import re
import threading
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

KIT_DIR = Path(__file__).resolve().parents[4] / "web" / "kit"

# 屏宽：电脑、平板竖屏、手机
VIEWPORTS: tuple[tuple[str, int, int], ...] = (
    ("电脑", 1280, 860),
    ("平板", 820, 1100),
    ("手机", 390, 844),
)
MODES = ("light", "dark")

# 允许的外链脚本来源（和项目对外发布页的规则一致）；其余外链脚本算不合格
SCRIPT_HOSTS = (
    "cdnjs.cloudflare.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
)
MIN_FONT_PX = 12.0
MIN_TAP_PX = 32.0
# 生成的工作台：手机上一个窗口卡片最多几屏高（超过建议修 / 必须修）
WINDOW_WARN_SCREENS = 2.0
WINDOW_BLOCK_SCREENS = 4.0
ERROR_WINDOW = re.compile(r"窗口 ([a-z0-9-]+) 出错")
BAD_TEXT = ("undefined", "NaN", "[object Object]", "null null", "Infinity")

# 在页面里跑的测量脚本：只读，返回原始数据，判定在 Python 里做
PROBE_JS = (Path(__file__).with_name("probe.js")).read_text(encoding="utf-8").split("\n", 1)[1]


SCROLL_JS = """async () => {
  for (let y = 0; y < document.documentElement.scrollHeight; y += innerHeight * 0.8) {
    scrollTo(0, y); await new Promise((r) => setTimeout(r, 40));
  }
  scrollTo(0, 0);
}"""


class QualityError(Exception):
    """质检没法进行（没装 Playwright、页面打不开等）。"""


# ---------------------------------------------------------------- 颜色与对比度


@functools.lru_cache(maxsize=4096)
def parse_color(text: str) -> tuple[float, float, float, float] | None:
    """解析浏览器给出的 rgb() / rgba() / color(srgb ...)，返回 0～1 的 (r, g, b, a)。"""
    s = (text or "").strip()
    m = re.fullmatch(r"rgba?\(([^)]*)\)", s)
    if m:
        parts = [p for p in re.split(r"[\s,/]+", m.group(1)) if p]
        if len(parts) < 3:
            return None
        rgb = [float(p) / 255 for p in parts[:3]]
        a = (
            float(parts[3].rstrip("%")) / (100 if parts[3].endswith("%") else 1)
            if len(parts) > 3
            else 1.0
        )
        return (rgb[0], rgb[1], rgb[2], a)
    m = re.fullmatch(r"color\(srgb ([^)]*)\)", s)
    if m:
        parts = [p for p in re.split(r"[\s/]+", m.group(1)) if p]
        if len(parts) < 3:
            return None
        a = float(parts[3]) if len(parts) > 3 else 1.0
        return (float(parts[0]), float(parts[1]), float(parts[2]), a)
    if s.startswith("#") and len(s) in (4, 7):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, 1.0)
    return None


def blend(top: tuple[float, ...], bottom: tuple[float, ...]) -> tuple[float, float, float, float]:
    """把半透明的颜色叠到下面一层上。"""
    a = top[3]
    return (
        top[0] * a + bottom[0] * (1 - a),
        top[1] * a + bottom[1] * (1 - a),
        top[2] * a + bottom[2] * (1 - a),
        1.0,
    )


def luminance(c: tuple[float, ...]) -> float:
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast_ratio(fg: tuple[float, ...], bg: tuple[float, ...]) -> float:
    """WCAG 对比度（1～21）。"""
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def effective_background(layers: Iterable[str]) -> tuple[float, float, float, float] | None:
    """从里到外的背景层（最后一层是页面底色）叠出实际底色。"""
    parsed = [c for c in (parse_color(x) for x in layers) if c]
    if not parsed:
        return None
    out = parsed[-1] if parsed[-1][3] >= 1 else blend(parsed[-1], (1.0, 1.0, 1.0, 1.0))
    for c in reversed(parsed[:-1]):
        out = blend(c, out)
    return out


def needed_ratio(size_px: float, weight: int) -> float:
    """大字（≥24px，或 ≥18.66px 且加粗）要求 3:1，其余 4.5:1。"""
    large = size_px >= 24 or (size_px >= 18.66 and weight >= 600)
    return 3.0 if large else 4.5


# ---------------------------------------------------------------- 判定


@dataclass
class Finding:
    check: str  # 检查项的名字（中文）
    severity: str  # block：必须修；warn：建议修
    where: str  # 哪种屏宽 / 明暗
    detail: str
    # 生成的工作台里出在哪几个窗口（按窗口交回作者）
    windows: list[str] = field(default_factory=list)


@dataclass
class Report:
    target: str
    findings: list[Finding] = field(default_factory=list)
    shots: list[str] = field(default_factory=list)
    combos: int = 0

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "block"]

    @property
    def passed(self) -> bool:
        return not self.blocking

    @property
    def score(self) -> int:
        """100 分起扣：必须修的每条 15 分，建议修的每条 3 分，最低 0。"""
        warn = sum(1 for f in self.findings if f.severity == "warn")
        return max(0, 100 - 15 * len(self.blocking) - 3 * warn)

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "passed": self.passed,
            "score": self.score,
            "combos": self.combos,
            "findings": [asdict(f) for f in self.findings],
            "shots": self.shots,
        }

    def summary(self) -> str:
        verdict = "通过" if self.passed else "不通过"
        head = f"{verdict} · {self.score} 分 · 测了 {self.combos} 种屏宽和明暗"
        lines = [head]
        for f in self.findings:
            mark = "✗" if f.severity == "block" else "!"
            lines.append(f"  {mark} [{f.where}] {f.check}：{f.detail}")
        return "\n".join(lines)


def _host_ok(url: str, local_hosts: tuple[str, ...]) -> bool:
    host = urlsplit(url).hostname or ""
    return host in local_hosts or host in SCRIPT_HOSTS or url.startswith(("data:", "blob:"))


def evaluate(
    probe: dict, where: str, *, errors: list[str], phone: bool, local_hosts: tuple[str, ...]
) -> list[Finding]:
    """把一次测量结果变成问题清单。"""
    out: list[Finding] = []

    def add(check: str, severity: str, detail: str, items: Iterable[dict] = ()) -> None:
        wins = sorted({i.get("win") for i in items if i.get("win")})
        out.append(Finding(check, severity, where, detail, wins))

    if errors:
        # 生成的工作台：外壳把窗口的错误记成「窗口 xxx 出错：…」，据此交回那个窗口的作者
        wins = [{"win": m.group(1)} for e in errors for m in [ERROR_WINDOW.search(e)] if m]
        add("页面报错", "block", "；".join(e[:160] for e in errors[:3]), wins)
    if (probe.get("charset") or "").upper() != "UTF-8":
        add(
            "没有声明编码",
            "block",
            f"浏览器按 {probe.get('charset')} 读，中文会乱码；"
            '在 <head> 里加 <meta charset="utf-8">',
        )
    if probe["scrollWidth"] > probe["width"] + 1:
        extra = "、".join(probe["overflow"][:3])
        add(
            "横向溢出",
            "block",
            f"页面宽 {probe['scrollWidth']}px，屏幕 {probe['width']}px"
            + (f"（{extra}）" if extra else ""),
        )
    bad = [
        w
        for w in BAD_TEXT
        if re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", probe["bodyText"])
    ]
    if bad:
        add("坏字", "block", "页面上出现 " + "、".join(bad))
    if not probe["kit"]:
        add("没用设计套件", "block", "要引入 /kit/kit.css，并给 body 加 k-root")
    foreign = [s for s in probe["scripts"] if not _host_ok(s, local_hosts)]
    if foreign:
        add("外链脚本", "block", "、".join(foreign[:3]))

    def examples(items: list[dict]) -> str:
        return "；".join(i["where"] for i in items[:3])

    nameless = [b for b in probe["buttons"] if not b["name"]]
    if nameless:
        add(
            "按钮没有名字",
            "block",
            f"{len(nameless)} 个，如 {examples(nameless)}（读屏软件读不出来；"
            "只有图标的按钮要加 aria-label）",
            nameless,
        )
    no_alt = [i for i in probe["images"] if i["alt"] is None]
    if no_alt:
        add("图片没有说明", "block", f"{len(no_alt)} 张，如 {examples(no_alt)}", no_alt)
    broken = [i for i in probe["images"] if not i["ok"]]
    if broken:
        add("图片打不开", "block", f"{len(broken)} 张，如 {examples(broken)}", broken)
    blank = [c for c in probe["charts"] if not c["drawn"]]
    if blank:
        add(
            "图表空白",
            "block",
            f"{len(blank)} 个，如 {examples(blank)}（没数据也要显示空状态）",
            blank,
        )

    small = [t for t in probe["texts"] if t["size"] < MIN_FONT_PX - 0.01 and not t["disabled"]]
    if small:
        t = small[0]
        add(
            "字太小",
            "warn",
            f"{len(small)} 处小于 {MIN_FONT_PX:g}px，如「{t['text']}」{t['size']:g}px"
            f"（{t['where']}）",
            small,
        )
    if phone and probe.get("height"):
        screens = [(w["h"] / probe["height"], w) for w in probe.get("windows") or []]
        for limit, sev in ((WINDOW_BLOCK_SCREENS, "block"), (WINDOW_WARN_SCREENS, "warn")):
            long = [(n, w) for n, w in screens if n > limit]
            if long:
                add(
                    "窗口太长",
                    sev,
                    "、".join(f"{w['id']} 约 {n:.1f} 屏" for n, w in long[:4])
                    + f"（手机上一个窗口不超过 {WINDOW_WARN_SCREENS:g} 屏：用标签页、分段、"
                    "折叠或「查看全部」收起来）",
                    [{"win": w["id"]} for _, w in long],
                )
                break

    low: list[tuple[float, dict]] = []
    for t in probe["texts"]:
        if t["disabled"] or t["bg"].get("image"):  # 停用的字、压在图片或图形上的字不查
            continue
        fg = parse_color(t["color"])
        bg = effective_background(t["bg"].get("layers", []))
        if not fg or not bg:
            continue
        r = contrast_ratio(blend(fg, bg) if fg[3] < 1 else fg, bg)
        if r < needed_ratio(t["size"], t["weight"]) - 0.05:
            low.append((r, t))
    if low:
        worst = min(low, key=lambda x: x[0])
        sev = "block" if worst[0] < 3.0 else "warn"
        add(
            "对比度不够",
            sev,
            f"{len(low)} 处，最差「{worst[1]['text']}」{worst[0]:.1f}:1（{worst[1]['where']}）",
            [t for _, t in low],
        )
    if phone:
        tiny = [
            b
            for b in probe["buttons"]
            if b["tag"] in ("button", "a")
            and (b["w"] < MIN_TAP_PX - 0.5 or b["h"] < MIN_TAP_PX - 0.5)
        ]
        if tiny:
            b = tiny[0]
            add(
                "手机上按钮太小",
                "warn",
                f"{len(tiny)} 个小于 {MIN_TAP_PX:g}px，如「{b['name']}」{b['w']:.0f}×{b['h']:.0f}"
                f"（{b['where']}）",
                tiny,
            )
    return out


def merge(findings: Iterable[Finding]) -> list[Finding]:
    """同一检查项在几种组合里都出现时合成一条，where 写成「电脑·浅色、手机·深色」。"""
    by: dict[tuple[str, str], Finding] = {}
    for f in findings:
        k = (f.check, f.severity)
        if k in by:
            if f.where not in by[k].where:
                by[k].where += "、" + f.where
            by[k].windows = sorted(set(by[k].windows) | set(f.windows))
        else:
            by[k] = Finding(f.check, f.severity, f.where, f.detail, list(f.windows))
    order = {"block": 0, "warn": 1}
    return sorted(by.values(), key=lambda f: order.get(f.severity, 2))


# ---------------------------------------------------------------- 跑浏览器


class _Handler(http.server.SimpleHTTPRequestHandler):
    """静态文件：/kit/ 指向设计套件，其余指向被测页面所在的文件夹。"""

    root = "."

    def translate_path(self, path: str) -> str:
        p = unquote(urlsplit(path).path)
        if p.startswith("/kit/"):
            base, rest = KIT_DIR, p[len("/kit/") :]
        else:
            base, rest = Path(self.root), p.lstrip("/")
        full = (base / rest).resolve()
        if not str(full).startswith(str(base.resolve())):
            return str(base / "__forbidden__")
        return str(full)

    def log_message(self, *args: object) -> None:  # 安静
        pass


def _gate(local_hosts: tuple[str, ...], allow: tuple[str, ...], route, _request=None) -> None:
    """只放行本机和 allow 里的网址（工作台配置里的公开数据，如地图底图、天气），其余一律中止。"""
    u = route.request.url
    if (urlsplit(u).hostname or "") in local_hosts or any(u.startswith(o + "/") for o in allow):
        route.continue_()
    else:
        route.abort()


@contextlib.contextmanager
def _serve(folder: Path) -> Iterator[str]:
    handler = type("H", (_Handler,), {"root": str(folder)})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def run_check(
    target: str,
    out_dir: str | Path | None = None,
    *,
    viewports: tuple[tuple[str, int, int], ...] = VIEWPORTS,
    modes: tuple[str, ...] = MODES,
    settle_ms: int = 900,
    allow: tuple[str, ...] = (),
) -> Report:
    """质检一个页面：本地 .html 文件（自带一个只在本机的静态服务）或 http(s) 地址。"""
    try:
        from playwright.sync_api import Error as PwError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise QualityError(
            "没有安装 Playwright：pip install playwright，再运行 playwright install chromium"
        ) from None

    out = Path(out_dir) if out_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    report = Report(target=target)
    raw: list[Finding] = []

    path = Path(target)
    local = not re.match(r"https?://", target)
    if local and not path.is_file():
        raise QualityError(f"找不到文件：{target}")

    with contextlib.ExitStack() as stack:
        if local:
            base = stack.enter_context(_serve(path.resolve().parent))
            url = f"{base}/{path.name}"
        else:
            url = target
        local_hosts = ("127.0.0.1", "localhost", urlsplit(url).hostname or "")
        try:
            pw = stack.enter_context(sync_playwright())
            # ROUNDTABLE_CHROMIUM：指定已安装的 Chromium（与 playwright 版本不匹配时用）
            browser = pw.chromium.launch(
                executable_path=os.environ.get("ROUNDTABLE_CHROMIUM") or None
            )
        except PwError as exc:
            raise QualityError(f"浏览器启动失败：{str(exc).splitlines()[0][:160]}") from None
        stack.callback(browser.close)
        for vp_name, w, h in viewports:
            for mode in modes:
                where = f"{vp_name}·{'浅色' if mode == 'light' else '深色'}"
                page = browser.new_page(
                    viewport={"width": w, "height": h}, color_scheme=mode, reduced_motion="reduce"
                )
                errors: list[str] = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.on(
                    "console",
                    lambda m, errors=errors: errors.append(m.text) if m.type == "error" else None,
                )

                page.route("**/*", functools.partial(_gate, local_hosts, tuple(allow)))
                try:
                    page.goto(url, wait_until="load", timeout=20000)
                    page.wait_for_timeout(settle_ms)
                    page.evaluate(SCROLL_JS)  # 滚到底再回来，让懒加载的图片都加载
                    page.wait_for_timeout(300)
                    probe = page.evaluate(PROBE_JS)
                except PwError as exc:
                    raise QualityError(f"页面打不开：{str(exc).splitlines()[0][:160]}") from None
                # 外部字体等被中止的请求会在控制台报「Failed to load resource」，不算页面的错
                errors = [e for e in errors if "Failed to load resource" not in e]
                raw += evaluate(probe, where, errors=errors, phone=w < 600, local_hosts=local_hosts)
                if out:
                    shot = out / f"{vp_name}_{mode}.png"
                    page.screenshot(path=str(shot), full_page=True)
                    report.shots.append(str(shot))
                page.close()
                report.combos += 1

    report.findings = merge(raw)
    if out:
        (out / "report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report
