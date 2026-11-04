"""星图（docs/STARMAP.md）：把圆桌和每位成员画成一张可以转动的立体星图。

- **圆桌星图**：中心是圆桌（当前版本号）；周围是五个部门（终端 / 办公室 / 公寓楼 / 群聊 /
  工作台）、成员、体检。每次更新是一颗星，挂在它属于的部门下（按标题里的关键词分，纯函数），
  越新越亮；体检里没过的项、档案不全的成员是「缺口」（空心星）。成员之间按亲近度连线。
- **人生星图**：中心是成员本人；周围十个星域——身份、性格、三观、喜好、能力、记忆、关系、
  情绪、表达、成长——覆盖一个类人智慧生命的全部要素。每个星域应有的东西没有时补一颗缺口星，
  写明怎么补。星与星之间除了从属线，还有「相关线」：记忆里提到的喜好 / 看重的事、和记忆
  同领域的能力、成长改动指向它改的那一项设定。
- 只读、不调模型、不写库；输入是服务层已有接口的返回（普通 dict），输出也是普通 dict，
  前端只负责摆位置和画。同样的输入总是得到同样的输出（可复现、可测试）。
- 星图只在本机网页里看（成员的记忆和档案只存本地，见项目规则 §5.1），不进任何提示词。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from roundtable.core.textutil import ellipsize

# 每颗星的类型：core 中心 / domain 星域 / star 普通星 / gap 缺口 / member 成员（可进入人生星图）
CORE, DOMAIN, STAR, GAP, MEMBER = "core", "domain", "star", "gap", "member"
TREE, RELATE, PEER = "tree", "relate", "peer"

FRESH_DAYS = 7  # 这么多天内的变化算「新」，前端让它闪
MAX_RELEASES = 60  # 圆桌星图最多画多少次更新
MAX_MEMORIES = 14  # 人生星图最多画多少件记忆（按重要度、次数、新旧挑）
MAX_GROWTH = 10  # 人生星图最多画多少条成长
LABEL_CHARS = 14  # 星的名字最多几个字（全文放在 detail 里）
DETAIL_CHARS = 400

# ---------------------------------------------------------------- 圆桌星图

DEPARTMENTS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "hub",
        "终端",
        (
            "终端",
            "首页",
            "立绘",
            "公告",
            "音乐",
            "封面",
            "片头",
            "开机动画",
            "邮件",
            "日历",
            "余额",
            "预算",
            "天气",
        ),
    ),
    (
        "work",
        "办公室",
        (
            "办公室",
            "讨论",
            "题目",
            "会议",
            "例会",
            "揭晓",
            "附件",
            "导出",
            "自检",
            "出问题",
            "平板",
            "备份",
            "游戏",
            "显卡",
            "资料浏览器",
            "渠道",
            "模型",
            "一起看",
            "上网",
        ),
    ),
    (
        "home",
        "公寓楼",
        ("公寓", "宿舍", "房间", "生活", "旅行", "朋友圈", "配图", "窗外", "房东", "出门"),
    ),
    (
        "chat",
        "群聊",
        (
            "群聊",
            "私聊",
            "聊天",
            "表情",
            "记忆",
            "反思",
            "成长",
            "关系",
            "人设",
            "性格",
            "声音",
            "语音",
            "口癖",
            "八位",
        ),
    ),
    (
        "bench",
        "工作台",
        ("工作台", "生成器", "dsh", "超级电脑", "资料", "周报", "管家", "插件", "零件"),
    ),
]
DEPARTMENT_HINT = {
    "hub": "首页、公告、音乐、立绘、邮件、日历",
    "work": "讨论会议、附件、导出、自检、备份、游戏模式",
    "home": "公寓、生活、旅行、朋友圈",
    "chat": "群聊私聊、记忆、成长、关系、表情和声音",
    "bench": "工作台、生成器、超级电脑、资料台",
}
LEVEL_WEIGHT = {"major": 1.0, "minor": 0.8, "patch": 0.5}
CHECK_LEVEL = {"ok": 0.9, "info": 0.6, "warn": 0.3, "bad": 0.1}
BAD_LEVELS = ("warn", "bad")


def department_of(text: str) -> str:
    """按标题和正文里的关键词分到部门：命中最多的那个（平手取靠前的），都没中归办公室。"""
    low = text.lower()
    best, hits = "work", 0
    for key, _, words in DEPARTMENTS:
        n = sum(low.count(w.lower()) for w in words)
        if n > hits:
            best, hits = key, n
    return best


def _clip(s: Any, n: int) -> str:
    return ellipsize(s, n)


def _plain(s: Any) -> str:
    """去掉 Markdown 记号（# ** | 表格线），方便当详情看。"""
    s = str(s or "")
    s = re.sub(r"^#+\s*", "", s, flags=re.M)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"^\|?[-:| ]+\|?$", "", s, flags=re.M)
    return _clip(s, DETAIL_CHARS)


def _fresh(day: str | None, today: date) -> bool:
    if not day:
        return False
    try:
        return date.fromisoformat(str(day)[:10]) >= today - timedelta(days=FRESH_DAYS)
    except ValueError:
        return False


class _Graph:
    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self._ids: set[str] = set()
        self._pairs: set[tuple[str, str]] = set()

    def node(self, nid: str, label: str, kind: str, group: str, **kw: Any) -> str:
        if nid in self._ids:
            return nid
        self._ids.add(nid)
        node = {
            "id": nid,
            "label": _clip(label, LABEL_CHARS),
            "kind": kind,
            "group": group,
            "value": round(float(kw.pop("value", 0.6)), 3),
            "detail": _plain(kw.pop("detail", "")),
            "fresh": bool(kw.pop("fresh", False)),
            "t": kw.pop("t", None),
        }
        node.update({k: v for k, v in kw.items() if v is not None})
        self.nodes.append(node)
        return nid

    def link(self, a: str, b: str, kind: str = TREE, weight: float = 1.0) -> None:
        if a == b or a not in self._ids or b not in self._ids:
            return
        pair = (a, b) if a < b else (b, a)
        if pair in self._pairs:
            return
        self._pairs.add(pair)
        self.links.append({"a": a, "b": b, "kind": kind, "w": round(weight, 3)})


def roundtable_map(
    *,
    changelog: dict[str, Any],
    members: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    peers: dict[str, list[dict[str, Any]]] | None = None,
    today: date,
) -> dict[str, Any]:
    """圆桌星图。

    ``members``：每位成员 ``{"id", "name", "score", "missing"}``（人生星图完整度 0～1、
    缺的星域名）。
    ``findings``：体检结果 ``{"id", "level", "title", "detail", "fix"}``。
    ``peers``：成员 id → 和同事的亲近度列表（``member`` / ``closeness``）。
    """
    g = _Graph()
    latest = changelog.get("latest") or ""
    core = g.node(
        "core",
        f"圆桌 {latest}".strip(),
        CORE,
        "core",
        value=1.0,
        detail=f"现在的版本 {latest}。转动看看各部门最近的变化，空心的星是还缺的东西。",
    )
    groups: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    releases = list(changelog.get("releases") or [])[:MAX_RELEASES]
    by_dept: dict[str, list[dict[str, Any]]] = {k: [] for k, _, _ in DEPARTMENTS}
    for r in releases:
        text = " ".join([r.get("title") or "", *(r.get("added") or []), *(r.get("fixed") or [])])
        by_dept[department_of(text)].append(r)

    for key, label, _ in DEPARTMENTS:
        items = by_dept[key]
        fresh = sum(_fresh(r.get("date"), today) for r in items)
        did = g.node(
            f"dept:{key}",
            label,
            DOMAIN,
            key,
            value=min(1.0, 0.45 + 0.05 * len(items)),
            detail=f"{DEPARTMENT_HINT[key]}。最近 {len(items)} 次更新，其中 {fresh} 次在一周内。",
            fresh=fresh > 0,
        )
        g.link(core, did)
        for r in items:
            body = "；".join([*(r.get("added") or []), *(r.get("fixed") or [])])
            how = f"\n怎么用：{r['howto']}" if r.get("howto") else ""
            rid = g.node(
                f"rel:{r.get('id') or r.get('version')}",
                r.get("title") or r.get("version") or "",
                STAR,
                key,
                value=LEVEL_WEIGHT.get(r.get("level") or "patch", 0.5),
                detail=f"{r.get('version', '')} · {r.get('date', '')}\n"
                f"{r.get('title', '')}\n{body}{how}",
                fresh=_fresh(r.get("date"), today),
                t=str(r.get("date") or "")[:10] or None,
                tag=r.get("version"),
            )
            g.link(did, rid)
            changes.append(
                {"t": str(r.get("date") or "")[:10], "text": r.get("title") or "", "node": rid}
            )
        if not items:
            gid = g.node(
                f"gap:dept:{key}",
                "最近没有更新",
                GAP,
                key,
                value=0.3,
                detail=f"{label}最近 {MAX_RELEASES} 次更新里一次都没轮到。",
            )
            g.link(did, gid)
            gaps.append({"text": f"{label}最近没有更新", "node": gid})
        groups.append(
            {"key": key, "label": label, "count": len(items), "filled": len(items), "total": None}
        )

    # 成员：每位一颗可以进入的星
    mid = g.node(
        "dept:members", "成员", DOMAIN, "members", value=0.8, detail="点一位成员进入她的人生星图。"
    )
    g.link(core, mid)
    full = 0
    for m in members:
        score = float(m.get("score") or 0)
        missing = list(m.get("missing") or [])
        full += not missing
        detail = f"人生星图完整度 {round(score * 100)}%。"
        if missing:
            detail += f"\n还缺：{'、'.join(missing)}。"
        nid = g.node(
            f"member:{m['id']}",
            m.get("name") or m["id"],
            MEMBER,
            "members",
            value=0.35 + 0.65 * score,
            detail=detail,
            member=m["id"],
            missing=bool(missing),
        )
        g.link(mid, nid)
        if missing:
            what = "整张人生星图几乎是空的" if len(missing) >= 7 else f"缺{'、'.join(missing)}"
            gaps.append({"text": f"{m.get('name') or m['id']}：{what}", "node": nid})
    for a, lst in (peers or {}).items():
        for p in lst:
            c = float(p.get("closeness") or 0)
            if c > 0:
                g.link(f"member:{a}", f"member:{p['member']}", PEER, c)
    groups.append(
        {
            "key": "members",
            "label": "成员",
            "count": len(members),
            "filled": full,
            "total": len(members),
        }
    )

    # 体检
    hid = g.node(
        "dept:health", "体检", DOMAIN, "health", value=0.7, detail="「出问题点这里」的各项。"
    )
    g.link(core, hid)
    ok = 0
    for f in findings:
        level = f.get("level") or "info"
        bad = level in BAD_LEVELS
        ok += not bad
        text = "\n".join(x for x in (f.get("title"), f.get("detail"), f.get("fix")) if x)
        fid = g.node(
            f"check:{f.get('id')}",
            f.get("title") or f.get("id") or "",
            GAP if bad else STAR,
            "health",
            value=CHECK_LEVEL.get(level, 0.5),
            detail=text,
            level=level,
        )
        g.link(hid, fid)
        if bad:
            gaps.append({"text": f.get("title") or "", "node": fid, "fix": f.get("fix") or ""})
    groups.append(
        {
            "key": "health",
            "label": "体检",
            "count": len(findings),
            "filled": ok,
            "total": len(findings),
        }
    )

    changes.sort(key=lambda c: c["t"], reverse=True)
    return {
        "kind": "roundtable",
        "title": "圆桌星图",
        "subtitle": f"版本 {latest} · {len(releases)} 次更新 · {len(gaps)} 处缺口",
        "center": core,
        "nodes": g.nodes,
        "links": g.links,
        "groups": groups,
        "changes": changes[:30],
        "gaps": gaps,
        "score": None,
    }


# ---------------------------------------------------------------- 人生星图

# 十个星域：key、名字、一句话、缺了怎么补
LIFE_DOMAINS: list[tuple[str, str, str, str]] = [
    ("identity", "身份", "名字、年纪、住处、身世", "在「成员设定」→ 自我档案里导入人物卡或手写"),
    ("personality", "性格", "五项性格、小毛病", "导入人物卡；成长反思也会慢慢改"),
    ("values", "三观", "最看重的事、底线", "导入人物卡；成长反思也会慢慢改"),
    ("likes", "喜好", "最爱、讨厌、擅长", "导入人物卡；生活里也会长出新的喜好"),
    ("ability", "能力", "长处、各领域熟练度", "多聊、多过日子，各领域的经历攒够了就升级"),
    ("memory", "记忆", "记住的事、挂着的事", "多聊几天，夜里整理时会记下来"),
    ("relation", "关系", "和你、和同事", "多聊几天，关系会慢慢近起来"),
    ("emotion", "情绪", "心情、精力、作息", "成员引擎的状态打开后自动有"),
    ("style", "表达", "口头禅、自称、说话方式", "导入人物卡；口癖会在聊天里慢慢变"),
    ("growth", "成长", "每周反思改了什么、习惯", "每周一反思一次；去「成员设定」→ 成长看"),
]

# 档案里的短项 → 星域（长段原文只放进星域的详情，不单独成星）
IDENTITY_KEYS = ("名字", "小名", "年龄感", "住处", "房号", "身世", "自我介绍")
SCORE_LABELS = ("外向", "好奇", "幽默", "温和", "较真")
_WORD = re.compile(r"[一-鿿A-Za-z0-9]{2,}")


def _items(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _keywords(text: str) -> list[str]:
    """用来找「相关」的词：去掉标点后的二字以上片段，再切成二到四字的小段。"""
    out: list[str] = []
    for w in _WORD.findall(text):
        if len(w) <= 4:
            out.append(w)
        else:
            out += [w[i : i + 3] for i in range(0, len(w) - 2)]
    return out


def _mentions(text: str, target: str) -> bool:
    if not text or not target:
        return False
    if target in text:
        return True
    return any(len(k) >= 3 and k in text for k in _keywords(target))


def life_map(
    *,
    member: dict[str, Any],
    profile: dict[str, Any],
    growth: dict[str, Any],
    relation: dict[str, Any],
    memory: dict[str, Any],
    state: dict[str, Any] | None,
    today: date,
) -> dict[str, Any]:
    """一位成员的人生星图。参数都是服务层对应接口的返回（档案 / 成长 / 关系 / 记忆件 / 状态）。"""
    g = _Graph()
    data = profile.get("data") or {}
    name = member.get("name") or member["id"]
    core = g.node(
        "core",
        name,
        CORE,
        "core",
        value=1.0,
        detail=f"{name}的人生星图。每个星域是构成一个人的一部分，空心的星是还缺的。",
        member=member["id"],
    )
    groups: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    stars: dict[str, list[str]] = {}  # 星域 → 星 id（缺口不算）
    text_of: dict[str, str] = {}  # 星 id → 用来找相关的文字

    def domain(key: str, label: str, hint: str, long: str = "") -> str:
        did = g.node(f"d:{key}", label, DOMAIN, key, value=0.8, detail=f"{hint}\n{long}".strip())
        g.link(core, did)
        stars[key] = []
        return did

    def star(key: str, sid: str, label: str, **kw: Any) -> str:
        nid = g.node(f"{key}:{sid}", label, kw.pop("kind", STAR), key, **kw)
        g.link(f"d:{key}", nid)
        stars[key].append(nid)
        text_of[nid] = f"{label} {kw.get('detail', '')}"
        return nid

    # 身份
    ident = data.get("identity") or {}
    domain("identity", "身份", LIFE_DOMAINS[0][2], _plain(ident.get("基本信息")))
    for k in IDENTITY_KEYS:
        for v in _items(ident.get(k))[:1]:
            star("identity", k, f"{k}·{v}" if k != "名字" else v, detail=f"{k}：{v}", value=0.7)

    # 性格
    pers = data.get("personality") or {}
    domain("personality", "性格", LIFE_DOMAINS[1][2], _plain(pers.get("性格")))
    scores = pers.get("五项") if isinstance(pers.get("五项"), dict) else {}
    for k, v in scores.items():
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        star(
            "personality",
            f"五项.{k}",
            f"{k} {int(n) if n == int(n) else n}/5",
            detail=f"{k}：{n:g} / 5",
            value=max(0.15, min(1.0, n / 5)),
            path=f"personality.五项.{k}",
        )
    for v in _items(pers.get("小毛病")):
        star(
            "personality",
            f"小毛病.{v}",
            v,
            detail=f"小毛病：{v}",
            value=0.45,
            path="personality.小毛病",
        )

    # 三观
    vals = data.get("values") or {}
    domain("values", "三观", LIFE_DOMAINS[2][2], _plain(vals.get("三观")))
    for i, v in enumerate(_items(vals.get("最看重"))):
        star(
            "values",
            f"最看重.{v}",
            v,
            detail=f"最看重的第 {i + 1} 件事：{v}",
            value=1.0 - 0.15 * i,
            path="values.最看重",
        )
    for v in _items(vals.get("底线要点")):
        star("values", f"底线.{v}", v, detail=f"底线：{v}", value=0.75, path="values.底线要点")

    # 喜好
    likes = data.get("likes") or {}
    domain("likes", "喜好", LIFE_DOMAINS[3][2], _plain(likes.get("喜好与讨厌")))
    for v in _items(likes.get("最爱")):
        star("likes", f"最爱.{v}", v, detail=f"最爱：{v}", value=0.85, path="likes.最爱")
    for v in _items(likes.get("讨厌")):
        star("likes", f"讨厌.{v}", v, detail=f"讨厌：{v}", value=0.4, path="likes.讨厌", tone="neg")
    for v in _items(likes.get("擅长")):
        star("likes", f"擅长.{v}", v, detail=f"擅长：{v}", value=0.8, path="likes.擅长")

    # 能力
    domain("ability", "能力", LIFE_DOMAINS[4][2])
    for v in _items(pers.get("长处")):
        star("ability", f"长处.{v}", v, detail=f"长处：{v}", value=0.8, path="personality.长处")
    top = max([int(s.get("next") or 1) for s in growth.get("skills") or []] + [1])
    for s in growth.get("skills") or []:
        lv = int(s.get("level") or 0)
        if lv <= 0:
            continue
        star(
            "ability",
            f"技能.{s['domain']}",
            f"{s['domain']} Lv{lv}",
            detail=f"{s['domain']}：{s.get('name', '')}（经验 {s.get('xp', 0)}）",
            value=min(1.0, 0.35 + 0.65 * lv / max(top, lv, 1)),
            area=s["domain"],
        )

    # 记忆
    domain("memory", "记忆", LIFE_DOMAINS[5][2], f"一共 {memory.get('count', 0)} 件。")
    items = [m for m in memory.get("items") or [] if not m.get("superseded_by")]
    items.sort(
        key=lambda m: (
            -int(m.get("importance") or 1),
            -int(m.get("hits") or 1),
            str(m.get("last_at") or ""),
        )
    )
    items = sorted(items[:MAX_MEMORIES], key=lambda m: str(m.get("day") or ""))
    for m in items:
        imp = int(m.get("importance") or 1)
        star(
            "memory",
            str(m["id"]),
            m.get("text") or "",
            detail=f"{m.get('day', '')} · {m.get('domain', '')}\n{m.get('text', '')}",
            value=min(1.0, 0.35 + 0.2 * imp),
            fresh=_fresh(m.get("day"), today),
            t=str(m.get("day") or "")[:10] or None,
            area=m.get("domain"),
        )
        if m.get("day"):
            changes.append(
                {
                    "t": str(m["day"])[:10],
                    "text": f"记住：{_clip(m.get('text'), 24)}",
                    "node": f"memory:{m['id']}",
                }
            )
    for i, h in enumerate((memory.get("hanging") or [])[:4]):
        text = str(h.get("text") or "")
        star("memory", f"挂着.{i}", f"挂着：{text}", detail=f"还挂在心上的事：{text}", value=0.5)

    # 关系
    domain("relation", "关系", LIFE_DOMAINS[6][2])
    user = relation.get("user") or {}
    if user.get("n") or user.get("closeness"):
        star(
            "relation",
            "你",
            f"你 · {user.get('stage', '')}",
            detail=f"和你：{user.get('stage', '')}，亲近度 {user.get('closeness', 0)}，"
            f"聊过 {user.get('n', 0)} 次、{user.get('days', 0)} 天。{user.get('line') or ''}",
            value=0.4 + 0.6 * float(user.get("closeness") or 0),
        )
    for p in relation.get("peers") or []:
        c = float(p.get("closeness") or 0)
        star(
            "relation",
            str(p["member"]),
            f"{p.get('name', p['member'])} · {p.get('label', '')}",
            detail=f"和{p.get('name', '')}：{p.get('label', '')}（亲近度 {c}）。"
            f"{p.get('view') or ''}",
            value=0.3 + 0.7 * c,
            peer=p["member"],
        )

    # 情绪
    domain("emotion", "情绪", LIFE_DOMAINS[7][2], _plain((data.get("state") or {}).get("日常")))
    if state:
        mood, energy = float(state.get("mood") or 0), float(state.get("energy") or 0)
        b = state.get("baseline") or {}
        star(
            "emotion",
            "心情",
            f"心情 {mood:+.2f}",
            detail=state.get("line") or "",
            value=(mood + 1) / 2,
        )
        star("emotion", "精力", f"精力 {round(energy * 100)}%", detail="0～100%", value=energy)
        if b:
            star(
                "emotion",
                "作息",
                f"作息 {b.get('wake', '')}→{b.get('sleep', '')}",
                detail=f"平时 {b.get('wake')} 点起、{b.get('sleep')} 点睡"
                + ("，夜猫子" if b.get("night_owl") else "")
                + f"；敏感度 {b.get('sensitivity')}",
                value=0.6,
            )
        for i, r in enumerate(list(reversed(state.get("recent") or []))[:3]):
            note = str(r.get("note") or r.get("kind") or "")
            if note:
                star("emotion", f"最近.{i}", note, detail=f"最近的反应：{note}", value=0.5)

    # 表达
    style = data.get("style") or {}
    domain("style", "表达", LIFE_DOMAINS[8][2], _plain(style.get("说话风格")))
    for v in _items(style.get("口头禅")):
        star("style", f"口头禅.{v}", v, detail=f"口头禅：{v}", value=0.7, path="style.口头禅")
    for v in _items(style.get("自称"))[:1]:
        star("style", "自称", f"自称·{v}", detail=f"自称：{v}", value=0.6, path="style.自称")

    # 成长：每条改动连到它改的那一项设定
    domain("growth", "成长", LIFE_DOMAINS[9][2], growth.get("plastic") or "")
    path_nodes: dict[str, list[str]] = {}
    for n in g.nodes:
        if n.get("path"):
            path_nodes.setdefault(n["path"], []).append(n["id"])
    timeline = [e for e in growth.get("timeline") or [] if e.get("kind") in ("change", "revert")]
    for e in timeline[:MAX_GROWTH]:
        day = str(e.get("at") or e.get("week") or "")
        what = e.get("path", "")
        old, new = e.get("old"), e.get("new")
        label = f"{what.rsplit('.', 1)[-1]}：{_clip(old, 6)}→{_clip(new, 6)}"
        nid = star(
            "growth",
            str(e.get("id") or len(stars["growth"])),
            label,
            detail=f"{e.get('week', '')} · {what}\n{old} → {new}\n为什么：{e.get('why') or ''}"
            + ("\n（你撤回了）" if e.get("reverted") else ""),
            value=0.4 if e.get("reverted") else 0.85,
            fresh=_fresh(day, today),
            t=day[:10] if day[:4].isdigit() else None,
        )
        changes.append({"t": day[:10], "text": label, "node": nid})
        for target in path_nodes.get(what, []):
            g.link(nid, target, RELATE, 0.9)
        prefix = what.rsplit(".", 1)[0]
        for p, ids in path_nodes.items():
            if p != what and (p.startswith(prefix + ".") or p == prefix):
                for target in ids:
                    g.link(nid, target, RELATE, 0.5)
    for h in (growth.get("habits") or [])[:4]:
        text = str(h.get("label") or h.get("key") or "")
        star(
            "growth",
            f"习惯.{h.get('key') or text}",
            f"习惯·{text}",
            detail=f"习惯：{text}（{h.get('n', 0)} 次、{h.get('weeks', 0)} 周）",
            value=0.6,
        )
    for b in (growth.get("book") or [])[:6]:
        if b.get("source") == "profile":
            continue  # 已经在时间线里
        changes.append(
            {"t": str(b.get("at") or "")[:10], "text": b.get("text") or "", "node": "d:growth"}
        )

    # 相关线：记忆提到喜好 / 看重的事 / 讨厌；记忆和同领域的能力；关系星连到记忆里提到的同事
    mems = stars["memory"]
    for key in ("likes", "values", "identity", "style"):
        for sid in stars[key]:
            target = next(n for n in g.nodes if n["id"] == sid)
            word = target["detail"].split("：", 1)[-1]
            for mid in mems:
                if _mentions(text_of[mid], word):
                    g.link(sid, mid, RELATE, 0.7)
    by_id = {n["id"]: n for n in g.nodes}
    for sid in stars["ability"]:
        area = by_id[sid].get("area")
        if area:
            for mid in mems:
                if by_id[mid].get("area") == area:
                    g.link(sid, mid, RELATE, 0.4)
    for sid in stars["relation"]:
        who = by_id[sid]["label"].split(" · ", 1)[0]
        for mid in mems:
            if who and _mentions(text_of[mid], who):
                g.link(sid, mid, RELATE, 0.6)
    for sid in stars["ability"]:
        for lid in stars["likes"]:
            if by_id[lid].get("path") == "likes.擅长" and _mentions(
                text_of[sid], by_id[lid]["label"]
            ):
                g.link(sid, lid, RELATE, 0.6)

    # 缺口与完整度
    filled = 0
    missing: list[str] = []
    for key, label, hint, fix in LIFE_DOMAINS:
        n = len(stars[key])
        if n:
            filled += 1
        else:
            missing.append(label)
            gid = g.node(
                f"gap:{key}",
                f"缺{label}",
                GAP,
                key,
                value=0.3,
                detail=f"还没有{hint}。\n怎么补：{fix}",
                fix=fix,
            )
            g.link(f"d:{key}", gid)
            gaps.append({"text": f"还没有{label}", "node": gid, "fix": fix})
        groups.append({"key": key, "label": label, "count": n, "filled": n, "total": None})
    score = filled / len(LIFE_DOMAINS)
    changes.sort(key=lambda c: c["t"], reverse=True)
    return {
        "kind": "life",
        "member": member["id"],
        "title": f"{name}的人生星图",
        "subtitle": f"完整度 {round(score * 100)}% · {len(g.nodes)} 颗星 · {len(gaps)} 处缺口",
        "center": core,
        "nodes": g.nodes,
        "links": g.links,
        "groups": groups,
        "changes": changes[:30],
        "gaps": gaps,
        "score": round(score, 3),
        "missing": missing,
    }
