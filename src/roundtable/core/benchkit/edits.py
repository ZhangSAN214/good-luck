"""生成器第二版 · 第 2 块：处处可改，改了只重做受影响的部分（docs/KIT.md）。

能改的东西（键）：

- ``answers``：访谈回答 {题号: 回答}。
- ``research``：调研的取舍 {removed: [编号], pinned: [编号], added: [{url, note}]}。
- ``materials``：留下哪些素材（素材 id 列表）。
- ``stages``：分段计划（方案书里的 milestones，可调顺序、拆段、合段）。
- ``window:<id>``：窗口说明（用途、数据、能做的事）。
- ``code:<id>`` / ``data:<id>``：窗口代码和数据文件。

每改一次存一个版本（值放在 ``history/`` 下的文件里，``build.json`` 只记谁改的、为什么、什么时候），
能看前后对比、能退回。改了上游，受影响的窗口记进 ``stale``（过期），等你点「只重做受影响的」。
这里只做纯数据和文件操作，不调模型。
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

VERSIONS_KEPT = 30  # 每样东西最多留多少个版本
MAX_TEXT = 400_000  # 代码 / 数据一次最多多少字
KEY_RE = re.compile(
    r"^(answers|research|materials|stages|(window|code|data):[a-z0-9][a-z0-9-]{0,40})$"
)
LABELS = {
    "answers": "访谈回答",
    "research": "调研",
    "materials": "素材",
    "stages": "分段计划",
    "window": "窗口说明",
    "code": "窗口代码",
    "data": "窗口数据",
}
WINDOW_FIELDS = ("title", "purpose", "data", "actions")
FIELD_LABELS = {"title": "标题", "purpose": "用途", "data": "数据从哪来", "actions": "能做的事"}


class EditError(ValueError):
    pass


def check_key(key: str) -> tuple[str, str]:
    """键 → (种类, 窗口 id)。"""
    if not KEY_RE.match(key or ""):
        raise EditError(f"不认得要改的东西：{key}")
    kind, _, wid = key.partition(":")
    return kind, wid


def label_of(key: str, b: dict[str, Any] | None = None) -> str:
    """给人看的名字；带上项目时窗口用它的标题（「预算」），不用编号。"""
    kind, wid = check_key(key)
    if wid and b is not None:
        wid = next((w.get("title") or wid for w in b.get("windows") or [] if w["id"] == wid), wid)
    return LABELS[kind] + (f"「{wid}」" if wid else "")


def as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)


def diff_text(old: Any, new: Any, a: str = "改之前", b: str = "改之后") -> str:
    lines = difflib.unified_diff(
        as_text(old).splitlines(), as_text(new).splitlines(), a, b, lineterm="", n=2
    )
    return "\n".join(lines)[:20000]


# --- 版本 --------------------------------------------------------------------------


def _safe(key: str) -> str:
    return key.replace(":", "__")


def version_path(folder: Path, key: str, n: int) -> Path:
    return folder / "history" / _safe(key) / f"v{n}.json"


def versions_of(b: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return list((b.get("versions") or {}).get(key) or [])


def read_version(folder: Path, key: str, n: int) -> Any:
    p = version_path(folder, key, n)
    if not p.is_file():
        raise EditError(f"第 {n} 版已经不在了")
    return json.loads(p.read_text(encoding="utf-8"))["value"]


def add_version(
    b: dict[str, Any], folder: Path, key: str, value: Any, by: str, why: str, at: str
) -> dict[str, Any]:
    """存一个版本；和上一版一模一样就不存。"""
    vs = b.setdefault("versions", {}).setdefault(key, [])
    if vs and _same(folder, key, vs[-1]["n"], value):
        return vs[-1]
    n = (vs[-1]["n"] + 1) if vs else 1
    p = version_path(folder, key, n)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"value": value}, ensure_ascii=False), encoding="utf-8")
    meta = {"n": n, "at": at, "by": by, "why": why[:200]}
    vs.append(meta)
    for old in vs[:-VERSIONS_KEPT]:
        version_path(folder, key, old["n"]).unlink(missing_ok=True)
    del vs[:-VERSIONS_KEPT]
    return meta


def _same(folder: Path, key: str, n: int, value: Any) -> bool:
    try:
        return read_version(folder, key, n) == value
    except (EditError, ValueError, OSError):
        return False


# --- 读和写各样东西 -------------------------------------------------------------------


def _spec_path(folder: Path) -> Path:
    return folder / "spec.json"


def _spec_window(folder: Path, wid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = json.loads(_spec_path(folder).read_text(encoding="utf-8"))
    w = next((x for x in spec.get("windows") or [] if x.get("id") == wid), None)
    if w is None:
        raise EditError(f"没有这个窗口：{wid}")
    return spec, w


def current(b: dict[str, Any], folder: Path, key: str) -> Any:
    kind, wid = check_key(key)
    if kind == "answers":
        return dict(((b.get("interview") or {}).get("answers")) or {})
    if kind == "research":
        notes = b.get("research_notes") or {}
        return {
            "removed": list(notes.get("removed") or []),
            "pinned": list(notes.get("pinned") or []),
            "added": list(notes.get("added") or []),
        }
    if kind == "materials":
        return [m["id"] for m in b.get("materials") or []]
    if kind == "stages":
        return json.loads(json.dumps(((b.get("proposal") or {}).get("milestones")) or []))
    if kind == "window":
        _, w = _spec_window(folder, wid)
        return {k: w.get(k) for k in WINDOW_FIELDS}
    path = folder / ("windows" if kind == "code" else "data") / f"{wid}.{_ext(kind)}"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _ext(kind: str) -> str:
    return "js" if kind == "code" else "json"


def clean_value(b: dict[str, Any], folder: Path, key: str, value: Any) -> Any:
    """校验你交来的新值，整理成存得下的样子。"""
    kind, wid = check_key(key)
    if kind == "answers":
        qs = {q["id"] for q in ((b.get("interview") or {}).get("questions")) or []}
        if not isinstance(value, dict) or not qs:
            raise EditError("这个项目没有访谈，或者回答的格式不对")
        return {q: str(value.get(q) or "").strip()[:1000] for q in sorted(qs)}
    if kind == "research":
        if not isinstance(value, dict):
            raise EditError("调研的取舍格式不对")
        ids = {x for x in _ref_ids(b)}
        removed = [x for x in value.get("removed") or [] if x in ids]
        pinned = [x for x in value.get("pinned") or [] if x in ids and x not in removed]
        added = []
        for item in (value.get("added") or [])[:20]:
            url = str((item or {}).get("url") or "").strip()
            if not re.match(r"^https?://\S+$", url):
                raise EditError(f"链接不对：{url or '（空）'}")
            added.append({"url": url[:500], "note": str(item.get("note") or "").strip()[:200]})
        return {"removed": removed, "pinned": pinned, "added": added}
    if kind == "materials":
        have = {m["id"] for m in b.get("materials") or []}
        if not isinstance(value, list):
            raise EditError("素材的格式不对")
        return [x for x in dict.fromkeys(str(v) for v in value) if x in have]
    if kind == "stages":
        return _clean_stages(b, folder, value)
    if kind == "window":
        if not isinstance(value, dict):
            raise EditError("窗口说明的格式不对")
        _, w = _spec_window(folder, wid)
        out = {k: w.get(k) for k in WINDOW_FIELDS}
        for k in ("title", "purpose", "data"):
            if k in value:
                out[k] = str(value[k] or "").strip()[: 60 if k == "title" else 600]
        if "actions" in value:
            acts = value["actions"]
            if isinstance(acts, str):
                acts = [a for a in re.split(r"[\n、，,]", acts)]
            out["actions"] = [str(a).strip()[:80] for a in acts if str(a).strip()][:12]
        if not out.get("title"):
            raise EditError("窗口标题不能空")
        return out
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise EditError("内容太长或格式不对")
    if kind == "data":
        try:
            json.loads(value or "{}")
        except ValueError as exc:
            raise EditError(f"数据不是合法的 JSON：{exc}") from None
    return value


def _ref_ids(b: dict[str, Any]) -> list[str]:
    from roundtable.core.benchkit.research import research_refs

    return [x["id"] for x in research_refs(b.get("research") or {})]


def _clean_stages(b: dict[str, Any], folder: Path, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise EditError("分段至少要有一段")
    try:
        spec = json.loads(_spec_path(folder).read_text(encoding="utf-8"))
        wids = {w["id"] for w in spec.get("windows") or []}
    except (OSError, ValueError):
        wids = {w["id"] for w in b.get("windows") or []}
    old = {str(m.get("title")): m for m in ((b.get("proposal") or {}).get("milestones")) or []}
    out = []
    for i, m in enumerate(value[:8], 1):
        if not isinstance(m, dict) or not str(m.get("title") or "").strip():
            raise EditError(f"第 {i} 段没有名字")
        title = str(m["title"]).strip()[:40]
        base = old.get(title, {})
        windows = [w for w in dict.fromkeys(m.get("windows") or []) if w in wids]
        out.append(
            base
            | {
                "n": i,
                "title": title,
                "goal": str(m.get("goal") or base.get("goal") or title).strip()[:200],
                "windows": windows,
            }
        )
    return out


def apply_value(b: dict[str, Any], folder: Path, key: str, value: Any) -> None:
    kind, wid = check_key(key)
    if kind == "answers":
        b.setdefault("interview", {})["answers"] = value
    elif kind == "research":
        b["research_notes"] = value
    elif kind == "materials":
        keep = set(value)
        b["materials"] = [m for m in b.get("materials") or [] if m["id"] in keep]
    elif kind == "stages":
        b.setdefault("proposal", {})["milestones"] = value
    elif kind == "window":
        spec, w = _spec_window(folder, wid)
        w.update(value)
        _spec_path(folder).write_text(
            json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        for rec in b.get("windows") or []:
            if rec["id"] == wid:
                rec["title"] = value["title"]
    else:
        path = folder / ("windows" if kind == "code" else "data") / f"{wid}.{_ext(kind)}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")


# --- 改了上游，哪些窗口受影响 -----------------------------------------------------------


def affected(
    b: dict[str, Any], folder: Path, key: str, old: Any, new: Any
) -> tuple[list[str], str]:
    """改了这一样，要重做哪些窗口、为什么。代码 / 数据你亲手改的不用重做（只是评审过期）。"""
    kind, wid = check_key(key)
    all_ids = [w["id"] for w in b.get("windows") or []]
    if kind in ("code", "data"):
        return [], ""
    if kind == "window":
        return [wid], "窗口说明改了：" + _short_diff(old, new)
    if kind == "answers":
        changed = [q for q in new if (old or {}).get(q) != new[q]]
        qs = {q["id"]: q["q"] for q in ((b.get("interview") or {}).get("questions")) or []}
        lines = [f"{qs.get(q, q)} —— {new[q] or '（改成不答，按推荐）'}" for q in changed]
        return (all_ids if changed else []), "访谈回答改了：" + "；".join(lines)
    if kind == "research":
        bits = []
        if set(new["removed"]) - set((old or {}).get("removed") or []):
            bits.append("删掉了调研条目 " + "、".join(new["removed"]))
        if set(new["pinned"]) - set((old or {}).get("pinned") or []):
            bits.append("钉住必须借鉴 " + "、".join(new["pinned"]))
        if new["added"] != ((old or {}).get("added") or []):
            bits.append("你加了参考链接 " + "、".join(x["url"] for x in new["added"]))
        return (all_ids if bits else []), "调研改了：" + "；".join(bits)
    if kind == "materials":
        gone = [m for m in b.get("materials") or [] if m["id"] in set(old) - set(new)]
        names = [m.get("file") or m["id"] for m in gone]
        hit = [w for w in all_ids if _uses(folder, w, names)]
        hero = ((b.get("proposal") or {}).get("hero")) or None
        if not hit and names and hero in all_ids:
            hit = [hero]
        return hit, ("删掉了素材：" + "、".join(names)) if names else ""
    if kind == "stages":
        before = {m["title"]: (i, tuple(m.get("windows") or [])) for i, m in enumerate(old or [])}
        hit: list[str] = []
        for i, m in enumerate(new):
            if before.get(m["title"]) != (i, tuple(m.get("windows") or [])):
                hit += [w for w in m.get("windows") or [] if w not in hit]
        gone = set(before) - {m["title"] for m in new}
        for t in gone:
            hit += [w for w in before[t][1] if w not in hit]
        return hit, "分段计划改了：" + " → ".join(m["title"] for m in new)
    return [], ""


def _uses(folder: Path, wid: str, names: list[str]) -> bool:
    if not names:
        return False
    text = ""
    for p in (folder / "windows" / f"{wid}.js", folder / "data" / f"{wid}.json"):
        if p.is_file():
            text += p.read_text(encoding="utf-8", errors="replace")
    return any(n and n in text for n in names)


def _short_diff(old: Any, new: Any) -> str:
    if isinstance(old, dict) and isinstance(new, dict):

        def show(v: Any) -> str:
            return "、".join(map(str, v)) if isinstance(v, list) else str(v)

        parts = [
            f"{FIELD_LABELS.get(k, k)}：{show(new.get(k))}" for k in new if old.get(k) != new.get(k)
        ]
        return "；".join(str(p) for p in parts)[:400]
    return "内容有改动"


def mark_stale(b: dict[str, Any], wids: list[str], reason: str, at: str) -> None:
    stale = b.setdefault("stale", {})
    for w in wids:
        reasons = stale.setdefault(w, [])
        if reason and reason not in [r["why"] for r in reasons]:
            reasons.append({"why": reason[:600], "at": at})
        del reasons[:-5]


def research_text(b: dict[str, Any]) -> str:
    """你对调研的取舍，写给写窗口的助手看。"""
    notes = b.get("research_notes") or {}
    lines = []
    if notes.get("pinned"):
        lines.append("- 你钉住了必须借鉴的调研条目：" + "、".join(notes["pinned"]))
    if notes.get("removed"):
        lines.append("- 你删掉的调研条目（不要再借鉴）：" + "、".join(notes["removed"]))
    for x in notes.get("added") or []:
        lines.append(f"- 你自己找的参考：{x['url']}" + (f"（{x['note']}）" if x["note"] else ""))
    return "\n".join(lines)
