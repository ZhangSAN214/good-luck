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
SHARED_MAX_CHARS = 6000
STATE_MAX = 10


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
    # 各窗口共用的基本信息（日期、人数、币种、场景……）：只放一份，窗口都从 ctx.shared 读
    shared: dict[str, Any] = field(default_factory=dict)
    # 窗口之间的消息约定：名字、谁发、谁收、detail 里有什么（ctx.bus 上照这个发和收）
    events: list[dict[str, Any]] = field(default_factory=list)
    # 几个窗口一起读写的用户数据（行程里加了什么、记了哪些账……）：键 → 结构和谁来改。
    # 都放在 ctx.state 里，谁改了大家都看得到，不各存一份（「已加入」两边对不上就是各存一份）
    state: dict[str, str] = field(default_factory=dict)
    # 验收清单：用户这句话拆出来的要求（r1、r2……）和底线标准（b1、b2……），每条写怎么检查、
    # 归哪几个窗口负责。评审逐条判做到没有，有一条没做到就不过线
    requirements: list[dict[str, Any]] = field(default_factory=list)
    # 主视觉窗口（地图、3D、图纸、照片这类真素材）：排在最前面、占大半屏
    hero: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def window(self, wid: str) -> Window | None:
        return next((w for w in self.windows if w.id == wid), None)


class SpecError(ValueError):
    pass


def _text(v: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


PROBE_DO = ("fill", "select", "click")


def probe_of(raw: Any) -> dict[str, Any] | None:
    """验收要求的自动检查：最多 4 步（填写 / 选择 / 点击），做完看哪些字出现、页面变没变。
    看不懂或没有能看的结果就当没写。"""
    if not isinstance(raw, dict):
        return None
    steps = []
    for x in raw.get("steps") or []:
        if not isinstance(x, dict) or x.get("do") not in PROBE_DO:
            continue
        target = _text(x.get("target"), 40)
        if not target:
            continue
        step = {"do": x["do"], "target": target}
        if x["do"] != "click":
            value = _text(x.get("value"), 80)
            if not value:
                continue
            step["value"] = value
        steps.append(step)
    steps = steps[:4]
    expect = [t for t in (_text(e, 40) for e in raw.get("expect") or []) if t][:4]
    changes = raw.get("changes") is True
    if not steps or not (expect or changes):
        return None
    return {"steps": steps, "expect": expect, "changes": changes}


def _requirements(
    raw: Any, seen: set[str], baseline: list[str], max_requirements: int
) -> list[dict[str, Any]]:
    """用户的要求编号 r1…，底线编号 b1…；底线以配置为准（拆需求的助手只能补「怎么检查、归谁」）。"""
    items = [x for x in (raw if isinstance(raw, list) else []) if isinstance(x, dict)]
    own = [x for x in items if not str(x.get("id") or "").startswith("b")]
    out: list[dict[str, Any]] = []

    def wins(x: dict[str, Any]) -> list[str]:
        got = [_ID.sub("-", str(w).lower()).strip("-") for w in (x.get("windows") or [])]
        return [w for w in got if w in seen][:8]

    for x in own:
        text = _text(x.get("text"), 160)
        if text and len(out) < max_requirements:
            out.append(
                {
                    "id": f"r{len(out) + 1}",
                    "source": "你的要求",
                    "text": text,
                    "check": _text(x.get("check"), 200),
                    "windows": wins(x),
                    "probe": probe_of(x.get("probe")),
                }
            )
    by_id = {str(x.get("id")): x for x in items}
    for n, text in enumerate(baseline, 1):
        x = by_id.get(f"b{n}", {})
        out.append(
            {
                "id": f"b{n}",
                "source": "底线",
                "text": text,
                "check": _text(x.get("check"), 200),
                "windows": wins(x),
                "probe": probe_of(x.get("probe")),
            }
        )
    return out


def normalize(
    raw: dict[str, Any],
    *,
    max_windows: int,
    baseline: list[str] | None = None,
    max_requirements: int = 12,
) -> Spec:
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
    shared = raw.get("shared") if isinstance(raw.get("shared"), dict) else {}
    if len(json.dumps(shared, ensure_ascii=False)) > SHARED_MAX_CHARS:
        raise SpecError(f"共用信息太长（超过 {SHARED_MAX_CHARS} 字）")
    events = []
    for e in raw.get("events") or []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        name = re.sub(r"[^a-z0-9:-]+", "-", str(e["name"]).lower()).strip("-")[:40]
        if not name:
            continue
        sender = _ID.sub("-", str(e.get("from") or "").lower()).strip("-")
        receivers = [_ID.sub("-", str(x).lower()).strip("-") for x in (e.get("to") or [])]
        detail = e.get("detail") if isinstance(e.get("detail"), dict) else {}
        events.append(
            {
                "name": name,
                "from": sender if sender in seen else "",
                "to": [x for x in receivers if x in seen][:8],
                "detail": {str(k)[:30]: _text(v, 80) for k, v in list(detail.items())[:12]},
                "when": _text(e.get("when"), 80),
            }
        )
    state = {}
    for k, v in (raw.get("state") if isinstance(raw.get("state"), dict) else {}).items():
        key = re.sub(r"[^a-zA-Z0-9_-]+", "", str(k))[:30]
        if key and len(state) < STATE_MAX:
            state[key] = _text(v, 200)
    hero = _ID.sub("-", str(raw.get("hero") or "").lower()).strip("-")
    if hero in seen:
        # 主视觉排最前、占大半屏（参考资料里地图、3D、图纸都是画面的主体）
        windows.sort(key=lambda w: w.id != hero)
        windows[0].span = max(windows[0].span, 8)
    else:
        hero = ""
    requirements = _requirements(
        raw.get("requirements"), seen, list(baseline or []), max_requirements
    )
    return Spec(
        title=title,
        subtitle=_text(raw.get("subtitle"), 60),
        icon=icon if icon in icons else "layers",
        theme=theme if theme in themes else "ink",
        windows=windows,
        needs=needs,
        shared=shared,
        events=events[:12],
        state=state,
        requirements=requirements,
        hero=hero,
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
            hero=" k-hero" if w.id == spec.hero else "",
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


def write_shell(
    folder: Path, spec: Spec, bench_id: str, net: list[dict[str, Any]] | None = None
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "windows").mkdir(exist_ok=True)
    (folder / "data").mkdir(exist_ok=True)
    (folder / "spec.json").write_text(
        json.dumps(spec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "data" / "_shared.json").write_text(
        json.dumps(spec.shared, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 能联网取的公开数据（名字、用途、网址模板、来源）：窗口经 ctx.net 取，地图零件自动用底图
    (folder / "data" / "_net.json").write_text(
        json.dumps(net or [], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    path = folder / "index.html"
    path.write_text(shell_html(spec, bench_id), encoding="utf-8")
    return path


def load_spec(folder: Path) -> Spec:
    """读回拆需求时存下的规格（项目室在关口之间停下、之后接着做时用）。"""
    d = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
    d["windows"] = [Window(**w) for w in d.get("windows") or []]
    return Spec(**d)
