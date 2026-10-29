"""工作台规格：拆需求的助手交回来的 JSON → 校验、修正成生成器能放心用的样子；再按规格画出外壳页面。

外壳（顶栏 + 网格 + 每个窗口的卡片）由程序按规格生成，品质稳定；助手只写每个窗口里面的内容
（``windows/<id>.js``，默认导出 ``mount(el, ctx)``）和示例数据（``data/<id>.json``）。
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from roundtable.core.benchkit.quality import KIT_DIR

_ID = re.compile(r"[^a-z0-9-]+")


def manifest() -> dict[str, Any]:
    return json.loads((KIT_DIR / "manifest.json").read_text(encoding="utf-8"))


def icon_names() -> set[str]:
    text = (KIT_DIR / "icons.js").read_text(encoding="utf-8")
    return set(re.findall(r'^"([\w-]+)":', text, re.M))


def parts_brief(m: dict[str, Any] | None = None) -> str:
    """零件清单的简写（给拆需求的助手看）：名字、用途；最后附图标名。"""
    m = m or manifest()
    lines = [f"- {p['name']}：{p['use']}" for p in m["parts"]]
    lines.append("图标：" + "、".join(sorted(icon_names())))
    return "\n".join(lines)


@dataclass
class Window:
    id: str
    title: str
    sub: str = ""
    icon: str = "layout-grid"
    span: int = 6
    purpose: str = ""
    parts: list[str] = field(default_factory=list)
    data: str = ""
    actions: list[str] = field(default_factory=list)

    @property
    def file(self) -> str:
        return f"windows/{self.id}.js"

    @property
    def data_file(self) -> str:
        return f"data/{self.id}.json"


@dataclass
class Spec:
    title: str
    subtitle: str = ""
    icon: str = "layers"
    theme: str = "ink"
    windows: list[Window] = field(default_factory=list)
    needs: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def window(self, wid: str) -> Window | None:
        return next((w for w in self.windows if w.id == wid), None)


class SpecError(ValueError):
    pass


def _text(v: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def normalize(raw: dict[str, Any], *, max_windows: int) -> Spec:
    """校验并修正：id 规范且不重复、零件和图标只留认得的、span 限在 3～12、窗口数不超上限。"""
    m = manifest()
    part_names = {p["name"] for p in m["parts"]}
    icons = icon_names()
    themes = m["themes"]
    title = _text(raw.get("title"), 24)
    if not title:
        raise SpecError("规格里没有标题")
    items = raw.get("windows") or []
    if not isinstance(items, list) or not items:
        raise SpecError("规格里没有窗口")
    windows: list[Window] = []
    seen: set[str] = set()
    for n, w in enumerate(items[:max_windows], 1):
        if not isinstance(w, dict):
            continue
        wid = _ID.sub("-", str(w.get("id") or "").lower()).strip("-")[:32] or f"w{n}"
        while wid in seen:
            wid = f"{wid}-{n}"
        seen.add(wid)
        try:
            span = int(w.get("span") or 6)
        except (TypeError, ValueError):
            span = 6
        icon = str(w.get("icon") or "")
        windows.append(
            Window(
                id=wid,
                title=_text(w.get("title"), 20) or f"窗口 {n}",
                sub=_text(w.get("sub"), 40),
                icon=icon if icon in icons else "layout-grid",
                span=max(3, min(12, span)),
                purpose=_text(w.get("purpose"), 400),
                parts=[p for p in (w.get("parts") or []) if p in part_names][:8],
                data=_text(w.get("data"), 600),
                actions=[_text(a, 40) for a in (w.get("actions") or []) if _text(a, 40)][:10],
            )
        )
    if not windows:
        raise SpecError("规格里的窗口都看不懂")
    icon = str(raw.get("icon") or "")
    theme = str(raw.get("theme") or "")
    needs = [
        {"need": _text(x.get("need"), 80), "keywords": _text(x.get("keywords"), 80)}
        for x in (raw.get("needs") or [])
        if isinstance(x, dict) and x.get("need")
    ][:6]
    return Spec(
        title=title,
        subtitle=_text(raw.get("subtitle"), 60),
        icon=icon if icon in icons else "layers",
        theme=theme if theme in themes else "ink",
        windows=windows,
        needs=needs,
    )


# 外壳和卡片的模板（shell.html、card.html）：只引零件库，按规格排卡片；每个窗口的模块单独加载，
# 坏了只影响自己那一格（并在控制台报错，质检会抓到）。占位写成 %%名字%%。
_HERE = Path(__file__).with_name


def _fill(template: str, **values: object) -> str:
    return re.sub(r"%%(\w+)%%", lambda m: str(values[m.group(1)]), template)


def shell_html(spec: Spec, bench_id: str) -> str:
    e = html.escape
    card = _HERE("card.html").read_text(encoding="utf-8").rstrip("\n")
    cards = "\n".join(
        _fill(
            card,
            span=w.span,
            id=w.id,
            icon=e(w.icon),
            title=e(w.title),
            sub=f'<div class="k-sub">{e(w.sub)}</div>' if w.sub else "",
        )
        for w in spec.windows
    )
    return _fill(
        _HERE("shell.html").read_text(encoding="utf-8"),
        theme=e(spec.theme),
        title=e(spec.title),
        subtitle=e(spec.subtitle),
        icon=e(spec.icon),
        cards=cards,
        bench_id=json.dumps(bench_id),
    )


def write_shell(folder: Path, spec: Spec, bench_id: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "windows").mkdir(exist_ok=True)
    (folder / "data").mkdir(exist_ok=True)
    (folder / "spec.json").write_text(
        json.dumps(spec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    path = folder / "index.html"
    path.write_text(shell_html(spec, bench_id), encoding="utf-8")
    return path
