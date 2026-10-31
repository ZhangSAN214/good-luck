"""意图理解、分段规划、意图追踪表（2026-10-31 工作台大更新第 2 批）。

方案见 /mnt/project-files/计划/意图理解与分段规划_方案.md。这里只放纯函数（不调助手、不读写文件），
生成器（builder.py）在对应的步骤调用：

- **原话表**（``add_voice``）：你的每句话原样存档——开工那句、访谈回答、签字补充、变更单、风格要求。
  不改写、不覆盖，后面所有判断都能指回某一句。
- **理想画像**（``intent_of`` / ``answer_intent``）：访谈的助手先整理五要素（给谁用、在哪用、要办成
  什么事、用起来什么感觉、不要什么）、隐含期待和假设，每条标把握（确定 / 推断 / 假设），只问
  会改结构的取舍（最多 3 问，每问带推荐项和后果）。你答了的那一项变成「确定」。
- **分段**（``stages_of``）：竖着切，每段都是能走通的版本（地基 → 最小可用 → 主要功能 → 打磨），
  3～5 段；每段一页：目标、你能试什么（3 步以内）、验收（不超过 9 条）、这段不做什么。
  每个窗口、每条验收都有归属；拆需求的助手没给分段时按「主视觉 + 第一条要求先做」兜底。
- **意图追踪表**（``trace``）：每句原话去了哪一段、哪几条验收、现在什么状态。交付前没做到的、
  没对上的都列出来，不让你的话被吞掉。
"""

from __future__ import annotations

import re
from typing import Any

LEVELS = ("确定", "推断", "假设")
FIVE = (
    ("who", "给谁用"),
    ("where", "在哪用"),
    ("task", "要办成什么事"),
    ("feel", "用起来什么感觉"),
    ("avoid", "不要什么"),
)
FIVE_KEYS = tuple(k for k, _ in FIVE)
EXPECT_FROM = ("对标", "使用者", "常识", "素材", "档案")
VOICE_SRC = ("开工", "访谈", "签字", "变更单", "风格", "素材", "档案")
MAX_QUESTIONS = 3
MAX_STAGES = 5
MIN_STAGES = 2
MAX_ACCEPT = 9  # 每段验收最多几条（越做越肥的第一道闸）
STAGE_NAMES = ("地基", "最小可用", "主要功能", "打磨", "革新")


def _text(v: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def _wid(v: Any) -> str:
    """窗口 id 和规格里一样规范化（小写、非字母数字换短横线）。"""
    return re.sub(r"[^a-z0-9-]+", "-", str(v).lower()).strip("-")[:32]


def _level(v: Any, default: str = "推断") -> str:
    v = str(v or "")
    return v if v in LEVELS else default


# --- 原话表 -------------------------------------------------------------------------


def add_voice(b: dict[str, Any], src: str, text: str, at: str = "", ref: str = "") -> dict | None:
    """存一句你说的话（原样，最多 1000 字）；空话不存。ref 指向变更单、访谈题这类原件。"""
    text = str(text or "").strip()[:1000]
    if not text:
        return None
    voices = b.setdefault("voices", [])
    v = {
        "id": f"v{len(voices) + 1}",
        "src": src if src in VOICE_SRC else "开工",
        "text": text,
        "at": at,
        "ref": ref,
    }
    voices.append(v)
    return v


def voices_text(b: dict[str, Any]) -> str:
    """给拆需求的助手看的原话表：编号、来源、原文。"""
    return "\n".join(f"- {v['id']}（{v['src']}）{v['text']}" for v in b.get("voices") or [])


# --- 理想画像 -----------------------------------------------------------------------


def _item(raw: Any, limit: int = 120, default: str = "推断") -> dict[str, str] | None:
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        return None
    text = _text(raw.get("text"), limit)
    if not text:
        return None
    return {"text": text, "level": _level(raw.get("level"), default)}


def intent_of(raw: Any) -> dict[str, Any]:
    """整理访谈助手给的理想画像：五要素、隐含期待（注明从哪来）、假设、一句话理想。看不懂的项丢掉。"""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {"ideal": _text(raw.get("ideal"), 200)}
    for key in FIVE_KEYS:
        out[key] = _item(raw.get(key))
    expect = []
    for x in raw.get("expect") or []:
        it = _item(x, 100)
        if it is None:
            continue
        src = str((x or {}).get("from") or "") if isinstance(x, dict) else ""
        it["from"] = src if src in EXPECT_FROM else "常识"
        expect.append(it)
    out["expect"] = expect[:8]
    out["assume"] = [
        a for a in (_item(x, 100, "假设") for x in raw.get("assume") or []) if a is not None
    ][:6]
    for a in out["assume"]:
        a["level"] = "假设"
    return out


def questions_of(raw: Any, limit: int = MAX_QUESTIONS) -> list[dict[str, Any]]:
    """只问会改结构的取舍：每问 2～4 个选项、推荐哪一个、选了会怎样；about 指向五要素的哪一项。"""
    out: list[dict[str, Any]] = []
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict) or not _text(q.get("q"), 200):
            continue
        opts = [_text(o, 60) for o in (q.get("options") or []) if _text(o, 60)][:4]
        rec = _text(q.get("recommend"), 60)
        about = str(q.get("about") or "")
        out.append(
            {
                "id": f"q{len(out) + 1}",
                "q": _text(q.get("q"), 200),
                "why": _text(q.get("why"), 200),
                "options": opts,
                "recommend": rec if rec in opts else (opts[0] if opts else ""),
                "effect": _text(q.get("effect"), 200),
                "about": about if about in FIVE_KEYS else "",
            }
        )
        if len(out) >= limit:
            break
    return out


def answer_intent(intent: dict[str, Any], questions: list[dict], answers: dict[str, str]) -> None:
    """你答了的题：对应的五要素换成你的回答，标「确定」；没答的按推荐项算，标「假设」。"""
    for q in questions:
        got = str(answers.get(q["id"]) or "").strip()
        key = q.get("about") or ""
        if got and key in FIVE_KEYS:
            intent[key] = {"text": got[:120], "level": "确定"}
        elif not got and q.get("recommend"):
            intent.setdefault("assume", []).append(
                {"text": f"{q['q']} → 按推荐「{q['recommend']}」", "level": "假设"}
            )


def intent_text(intent: dict[str, Any] | None) -> str:
    """理想画像写成给助手看的一段（标把握）。"""
    if not intent:
        return ""
    lines = []
    if intent.get("ideal"):
        lines.append(f"一句话理想：{intent['ideal']}")
    for key, label in FIVE:
        it = intent.get(key)
        if it:
            lines.append(f"{label}：{it['text']}（{it['level']}）")
    for x in intent.get("expect") or []:
        lines.append(f"隐含期待：{x['text']}（{x['level']}，来自{x['from']}）")
    for x in intent.get("assume") or []:
        lines.append(f"假设：{x['text']}（第 1 段演示时请用户确认）")
    return "\n".join(lines)


def intent_counts(intent: dict[str, Any] | None) -> dict[str, int]:
    """理想画像里各把握等级各几条（界面上的小结）。"""
    out = dict.fromkeys(LEVELS, 0)
    if not intent:
        return out
    items = [intent.get(k) for k in FIVE_KEYS] + list(intent.get("expect") or [])
    items += list(intent.get("assume") or [])
    for it in items:
        if it:
            out[it["level"]] += 1
    return out


# --- 分段 ---------------------------------------------------------------------------


def _fallback(hero: str, wids: list[str], reqs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """拆需求的助手没给分段：主视觉 + 第一条要求的窗口先做（最小可用），其余是主要功能。"""
    first = set([hero] if hero else [])
    own = [r for r in reqs if r["id"].startswith("r")]
    if own:
        first |= set(own[0]["windows"])
    first_ids = [w for w in wids if w in first] or wids[:1]
    rest = [w for w in wids if w not in first_ids]
    out = [
        {
            "title": "最小可用",
            "goal": "最核心的一件事先走通",
            "windows": first_ids,
            "accept": [own[0]["id"]] if own else [],
        }
    ]
    if rest:
        out.append({"title": "主要功能", "goal": "补齐其余窗口和功能", "windows": rest})
    return out


def stages_of(
    raw: Any,
    wids: list[str],
    reqs: list[dict[str, Any]],
    *,
    hero: str = "",
    max_stages: int = MAX_STAGES,
) -> list[dict[str, Any]]:
    """校正分段：段数 2～max_stages（不足 2 段且窗口够时拆开，超了合进最后一段）；窗口和验收 id
    只留认得的；每个窗口至少在一段里出现（漏了的放最后一段）；每条你的要求（r…）都归某一段，
    每段验收不超过 9 条（多出来的挪到后面的段）；底线（b…）放最后一段一起验。"""
    items = [x for x in (raw if isinstance(raw, list) else []) if isinstance(x, dict)]
    known_w = set(wids)
    known_r = {r["id"] for r in reqs}
    stages: list[dict[str, Any]] = []
    for x in items:
        ws = [_wid(w) for w in (x.get("windows") or []) if _wid(w) in known_w]
        acc = [str(a) for a in (x.get("accept") or []) if str(a) in known_r]
        if not ws and not acc:
            continue
        stages.append(
            {
                "title": _text(x.get("title"), 20),
                "goal": _text(x.get("goal"), 120),
                "try": [_text(t, 60) for t in (x.get("try") or []) if _text(t, 60)][:3],
                "windows": list(dict.fromkeys(ws)),
                "accept": list(dict.fromkeys(a for a in acc if a.startswith("r"))),
                "skip": _text(x.get("skip"), 160),
                "risk": _text(x.get("risk"), 120),
            }
        )
    if not stages:
        stages = [
            {"try": [], "skip": "", "risk": "", **s, "accept": s.get("accept") or []}
            for s in _fallback(hero, wids, reqs)
        ]
    if len(stages) > max_stages:
        tail = stages[max_stages - 1 :]
        last = stages[max_stages - 1]
        for s in tail[1:]:
            last["windows"] = list(dict.fromkeys(last["windows"] + s["windows"]))
            last["accept"] = list(dict.fromkeys(last["accept"] + s["accept"]))
        stages = stages[:max_stages]
    # 每个窗口都要有人管：漏了的放最后一段
    covered = {w for s in stages for w in s["windows"]}
    missed = [w for w in wids if w not in covered]
    if missed:
        stages[-1]["windows"] += missed
    # 只有一段且窗口够分：主视觉（或第一个窗口）先做，其余放第 2 段
    if len(stages) < MIN_STAGES and len(stages[0]["windows"]) > 1:
        s = stages[0]
        first = [hero] if hero in s["windows"] else s["windows"][:1]
        stages.append(
            {
                "title": "主要功能",
                "goal": "补齐其余窗口和功能",
                "try": [],
                "windows": [w for w in s["windows"] if w not in first],
                "accept": [],
                "skip": "",
                "risk": "",
            }
        )
        s["windows"] = first
    # 你的要求都要有归属；一个要求只算在第一次出现的那段
    seen: set[str] = set()
    for s in stages:
        s["accept"] = [a for a in s["accept"] if not (a in seen or seen.add(a))]
    for r in reqs:
        if r["id"].startswith("r") and r["id"] not in seen:
            stages[-1]["accept"].append(r["id"])
    # 每段最多 9 条：多的往后挪，最后一段放不下的也留着（宁可多验，不丢）
    for i, s in enumerate(stages[:-1]):
        if len(s["accept"]) > MAX_ACCEPT:
            stages[i + 1]["accept"] = s["accept"][MAX_ACCEPT:] + stages[i + 1]["accept"]
            s["accept"] = s["accept"][:MAX_ACCEPT]
    stages[-1]["accept"] += [r["id"] for r in reqs if r["id"].startswith("b")]
    for n, s in enumerate(stages, 1):
        s["n"] = n
        s["title"] = s["title"] or STAGE_NAMES[min(n, len(STAGE_NAMES)) - 1]
        s["goal"] = s["goal"] or s["title"]
    return stages


def stage_task(stage: dict[str, Any], reqs: list[dict[str, Any]], total: int) -> str:
    """一段的任务说明（给写窗口、返工的助手）：目标、要做到的验收、这段不做什么。"""
    by_id = {r["id"]: r for r in reqs}
    lines = [f"现在做第 {stage['n']}/{total} 段「{stage['title']}」：{stage['goal']}"]
    acc = [by_id[a] for a in stage["accept"] if a in by_id and a.startswith("r")]
    if acc:
        lines.append("这一段要做到：")
        lines += [f"- {r['id']} {r['text']}" for r in acc]
    if stage.get("try"):
        lines.append("用户会这样试：" + " → ".join(stage["try"]))
    if stage.get("skip"):
        lines.append(f"这段不做：{stage['skip']}（后面的段再做，别提前塞进来）")
    if stage["n"] < total:
        lines.append("这一段做完要能从头到尾走通，按钮不能是摆设；没轮到的功能不要放假按钮。")
    return "\n".join(lines)


# --- 意图追踪表 ---------------------------------------------------------------------

STATES = ("待做", "做到", "没做到", "改好了", "没改成", "已参考", "没对上")


def _stage_of(rid: str, stages: list[dict[str, Any]]) -> int | None:
    return next((s["n"] for s in stages if rid in s["accept"]), None)


def trace(b: dict[str, Any]) -> list[dict[str, Any]]:
    """每句原话的去向：对上了哪几条验收、在哪一段、现在什么状态。

    - 变更单看处理结果（改好了 / 没改成 / 待做）。
    - 开工、签字、风格这几类看对上的验收：都做到 = 做到；有一条最近一轮判没做到 = 没做到；
      还没评过 = 待做；一条都没对上 = 没对上（交付前要你看一眼）。
    - 访谈回答、签字补充、风格要求是写进需求的补充资料（已参考），对上了验收的照验收算。
    """
    reqs = (b.get("proposal") or {}).get("requirements") or []
    stages = (b.get("proposal") or {}).get("milestones") or []
    last = next((r for r in reversed(b.get("rounds") or []) if r.get("requirements")), None)
    met = {r["id"]: r["met"] for r in (last or {}).get("requirements") or []}
    changes = {c["id"]: c for c in b.get("changes") or []}
    rows = []
    for v in b.get("voices") or []:
        linked = [r["id"] for r in reqs if v["id"] in (r.get("voices") or [])]
        stage = next((n for n in (_stage_of(r, stages) for r in linked) if n), None)
        if v["src"] == "变更单":
            c = changes.get(v.get("ref") or "")
            state = {"done": "改好了", "failed": "没改成"}.get((c or {}).get("status"), "待做")
        elif linked:
            got = [met.get(r) for r in linked]
            if any(x is False for x in got):
                state = "没做到"
            elif got and all(x is True for x in got):
                state = "做到"
            else:
                state = "待做"
        elif v["src"] in ("访谈", "素材", "签字", "风格", "档案"):
            state = "已参考"
        elif not reqs:
            state = "待做"  # 还没拆出验收清单
        else:
            state = "没对上"
        rows.append(
            {
                "id": v["id"],
                "src": v["src"],
                "text": v["text"],
                "reqs": linked,
                "stage": stage,
                "state": state,
            }
        )
    return rows


def open_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """交付前要你看一眼的：没做到、没改成、没对上的原话。"""
    return [r for r in rows if r["state"] in ("没做到", "没改成", "没对上")]


def link_voices(reqs: list[dict[str, Any]], raw_reqs: Any, voices: list[dict[str, Any]]) -> None:
    """把拆需求的助手给每条要求标的原话编号（voices: ["v1"]）写进验收清单（只留认得的）。
    没标的：开工那句话（v1）兜底——你的要求都是从它拆出来的。"""
    known = {v["id"] for v in voices}
    own_raw = [
        x
        for x in (raw_reqs if isinstance(raw_reqs, list) else [])
        if isinstance(x, dict)
        and not str(x.get("id") or "").startswith("b")
        and _text(x.get("text"), 160)
    ]
    own = [r for r in reqs if r["id"].startswith("r")]
    for r, x in zip(own, own_raw, strict=False):
        got = [str(v) for v in (x.get("voices") or []) if str(v) in known]
        r["voices"] = list(dict.fromkeys(got))[:6]
    for r in own:
        if not r.get("voices") and "v1" in known:
            r["voices"] = ["v1"]
