"""网络梗（梗雷达的「懂梗、玩梗」这一半，docs/CHAT.md「网络梗」）。

- **梗库**（``MemeBook``）：存在 ``app_settings``（``memes.items``，不加迁移）；配置里的经典梗
  合进来一起看。新梗 ``fresh_days`` 天没再上榜就下架（经典梗、你手动标了经典的不下架）；
  你删掉的记进 ``memes.removed``，以后不再收。
- **谁懂哪个梗**：由（成员, 梗）派生的随机数和懂梗概率比——概率只看性格分（``know_traits``），
  新梗刚出来时懂的人少，``learn_days`` 天后到满。8 位同一套规则（中立性规则 1、9）。
- **意图卡里的一句**（``MemeSense.line``）：对方说了梗的上句或梗本身——懂就接住（有固定接法时
  一半时候程序直接接，不调模型），不懂就问一句是啥梗；平时偶尔提一句「最近在玩的梗」，
  用不用她自己看场合；同一个梗她隔 ``reuse_days`` 天才再主动用。
- **老梗烂梗**（``strip_stale``）：说出口前把含老梗的那一小句删掉（整条都是老梗就整条不发）。

全程序、不花钱；只用于生活模式（大脑分区的意图卡和发言收尾），工作模式不挂载。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.storage.repository import iso

ITEMS_KEY = "memes.items"
REMOVED_KEY = "memes.removed"
USED_KEY = "memes.used:"
_NORM = re.compile(r"[\s，,。.！!？?、~～…\"'“”‘’「」]+")
_CLAUSES = re.compile(r"([^，,。！!？?；;～~…\n]*[，,。！!？?；;～~…\n]*)")
FIELDS = ("phrase", "aliases", "meaning", "cue", "reply", "example", "since")


def norm(text: str) -> str:
    return _NORM.sub("", text or "").lower()


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 3.0


def strip_stale(text: str, stale: list[str] | tuple[str, ...]) -> str:
    """把含老梗烂梗的小句删掉（10-31 用户：禁止出现老梗烂梗）；删完什么都不剩时返回空字符串。"""
    words = [w for w in stale if w]
    if not text or not words:
        return text
    low = text.lower()
    if not any(w.lower() in low for w in words):
        return text
    lines = []
    for line in text.split("\n"):
        parts = [p for p in _CLAUSES.findall(line) if p]
        kept = [p for p in parts if not any(w.lower() in p.lower() for w in words)]
        out = "".join(kept).strip()
        out = re.sub(r"^[，,。；;！!？?～~]+", "", out)
        if out:
            lines.append(out)
    return "\n".join(lines)


class MemeBook:
    """梗库：经典梗（配置 + 你标的）+ 梗雷达收来的新梗。"""

    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.memes

    def enabled(self) -> bool:
        return bool(self.rules.enabled)

    # --- 读写 --------------------------------------------------------------------

    def _load(self, key: str, default: Any) -> Any:
        raw = self.rt.repo.setting(key)
        if not raw:
            return default
        try:
            data = json.loads(raw)
        except ValueError:
            return default
        return data if isinstance(data, type(default)) else default

    def stored(self) -> list[dict[str, Any]]:
        return [m for m in self._load(ITEMS_KEY, []) if isinstance(m, dict) and m.get("phrase")]

    def save(self, items: list[dict[str, Any]]) -> None:
        self.rt.repo.set_setting(ITEMS_KEY, json.dumps(items, ensure_ascii=False))

    def removed(self) -> set[str]:
        return {norm(str(p)) for p in self._load(REMOVED_KEY, [])}

    def is_stale(self, phrase: str) -> bool:
        p = norm(phrase)
        return any(norm(w) and (norm(w) in p or p in norm(w)) for w in self.rules.stale)

    def all(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """现在还算数的梗：经典梗在前，新梗按最近上榜排；过了保质期的、你删掉的不算。"""
        removed = self.removed()
        by: dict[str, dict[str, Any]] = {}
        for c in self.rules.classics:
            m = {**c.model_dump(), "classic": True, "source": "classic", "example": "", "since": ""}
            by[norm(c.phrase)] = m
        cutoff = iso(now - timedelta(days=self.rules.fresh_days)) if now is not None else ""
        for m in self.stored():
            key = norm(m["phrase"])
            if key in by:  # 经典梗：补好的意思、你改过的字段盖在配置上
                by[key] = {**by[key], **{k: v for k, v in m.items() if v not in ("", None, [])}}
                by[key]["classic"] = True
                continue
            if not m.get("classic") and cutoff and str(m.get("last_seen") or "") < cutoff:
                continue  # 两周没再上榜：下架
            by[key] = m
        out = [m for k, m in by.items() if k not in removed and not self.is_stale(m["phrase"])]
        new = sorted(
            (m for m in out if not m.get("classic")), key=lambda m: str(m.get("last_seen") or "")
        )
        return [m for m in out if m.get("classic")] + new[::-1]

    def add(self, data: dict[str, Any], now: datetime, *, source: str = "user") -> dict[str, Any]:
        """你手动加一个（或改一个已有的）；classic=True 时永远不下架。"""
        phrase = str(data.get("phrase") or "").strip()[:20]
        if not phrase:
            raise ValueError("梗不能是空的")
        if self.is_stale(phrase) and not data.get("classic"):
            raise ValueError(f"「{phrase}」在老梗名单里")
        items = self.stored()
        key = norm(phrase)
        old = next((m for m in items if norm(m["phrase"]) == key), {})
        meme = {
            **old,
            "phrase": phrase,
            "aliases": [str(a)[:20] for a in (data.get("aliases") or old.get("aliases") or [])][:6],
            "meaning": str(data.get("meaning") or old.get("meaning") or "")[:80],
            "cue": str(data.get("cue") or old.get("cue") or "")[:40],
            "reply": str(data.get("reply") or old.get("reply") or "")[:40],
            "example": str(data.get("example") or old.get("example") or "")[:80],
            "since": str(data.get("since") or old.get("since") or "")[:7],
            "classic": bool(data.get("classic", old.get("classic", False))),
            "source": old.get("source") or source,
            "first_seen": old.get("first_seen") or iso(now),
            "last_seen": iso(now),
        }
        items = [m for m in items if norm(m["phrase"]) != key] + [meme]
        self.save(items)
        removed = [p for p in self._load(REMOVED_KEY, []) if norm(str(p)) != key]
        self.rt.repo.set_setting(REMOVED_KEY, json.dumps(removed, ensure_ascii=False))
        return meme

    def remove(self, phrase: str) -> bool:
        key = norm(phrase)
        items = self.stored()
        kept = [m for m in items if norm(m["phrase"]) != key]
        known = len(kept) != len(items) or any(norm(c.phrase) == key for c in self.rules.classics)
        if not known:
            return False
        self.save(kept)
        removed = self._load(REMOVED_KEY, [])
        if key not in {norm(str(p)) for p in removed}:
            removed.append(phrase)
        self.rt.repo.set_setting(REMOVED_KEY, json.dumps(removed[-300:], ensure_ascii=False))
        return True

    # --- 懂不懂 ------------------------------------------------------------------

    def chance(self, member: str, meme: dict[str, Any], at: datetime) -> float:
        r = self.rules
        if meme.get("classic"):
            ramp = 1.0
        else:
            first = str(meme.get("first_seen") or "")
            try:
                age = (at - datetime.fromisoformat(first)).total_seconds() / 86400
            except ValueError:
                age = r.learn_days
            ramp = 1.0 if r.learn_days <= 0 else min(1.0, 0.5 + 0.5 * max(age, 0) / r.learn_days)
        traits = self._traits(member)
        p = r.know_base + sum(c * (_num(traits.get(t, 3)) - 3) for t, c in r.know_traits.items())
        return max(r.know_min, min(r.know_max, p)) * ramp

    def _traits(self, member: str) -> dict[str, Any]:
        from roundtable.core.member.profile import ProfileStore

        try:
            data = ProfileStore(self.rt).current(member).data
        except Exception:  # noqa: BLE001 — 没有档案：按中间分算
            return {}
        return dict((data.get("personality") or {}).get("五项") or {})

    def knows(self, member: str, meme: dict[str, Any], at: datetime) -> bool:
        digest = hashlib.sha256(f"meme:{member}:{norm(meme['phrase'])}".encode()).digest()
        roll = int.from_bytes(digest[:4], "big") / 2**32
        return roll < self.chance(member, meme, at)

    @staticmethod
    def hit(meme: dict[str, Any], text: str) -> bool:
        """这句话里有没有这个梗（梗本身、别的说法或上句）。"""
        t = norm(text)
        if not t:
            return False
        keys = [meme.get("phrase"), meme.get("cue"), *(meme.get("aliases") or [])]
        return any(len(norm(k)) >= 2 and norm(k) in t for k in keys if k)

    def heard(self, text: str, at: datetime) -> dict[str, Any] | None:
        for meme in self.all(at):
            if self.hit(meme, text):
                return meme
        return None

    # --- 用过 --------------------------------------------------------------------

    def used(self, member: str) -> dict[str, str]:
        return {str(k): str(v) for k, v in self._load(USED_KEY + member, {}).items()}

    def mark_used(self, member: str, phrase: str, at: datetime) -> None:
        used = self.used(member)
        used[norm(phrase)] = iso(at)
        keep = dict(sorted(used.items(), key=lambda kv: kv[1])[-60:])
        self.rt.repo.set_setting(USED_KEY + member, json.dumps(keep, ensure_ascii=False))

    def fresh_for(self, member: str, at: datetime) -> list[dict[str, Any]]:
        """她懂、又有段时间没主动用过的梗（意思还不知道的不算）。"""
        cutoff = iso(at - timedelta(days=self.rules.reuse_days))
        used = self.used(member)
        return [
            m
            for m in self.all(at)
            if m.get("meaning")
            and used.get(norm(m["phrase"]), "") < cutoff
            and self.knows(member, m, at)
        ]


def _gloss(meme: dict[str, Any]) -> str:
    meaning = str(meme.get("meaning") or "")[:36]
    return f"（{meaning}）" if meaning else ""


class MemeSense:
    """意图卡里关于网络梗的那一句（只用于生活模式）。"""

    def __init__(self, rt: Any) -> None:
        self.book = MemeBook(rt)

    def line(
        self,
        member: str,
        *,
        last: str,
        chosen: str,
        hurt: bool,
        at: datetime,
        rng: random.Random,
    ) -> tuple[str, str, str] | None:
        """返回 (标签, 写进意图卡的一句, 程序直接发的接法)；没有就 None。"""
        book = self.book
        if not book.enabled() or hurt:
            return None
        rules = book.rules
        heard = book.heard(last, at) if last else None
        if heard is not None:
            phrase = heard["phrase"]
            if book.knows(member, heard, at):
                reply = str(heard.get("reply") or "")
                quick = reply if reply and rng.random() < rules.quick_chance else ""
                tail = f"，熟的人会接「{reply}」" if reply else ""
                line = f"对方在玩梗「{phrase}」{_gloss(heard)}{tail}；顺着接住，别解释这个梗"
                return f"meme:reply:{phrase}", line, quick
            if rng.random() < rules.ask_chance:
                return "meme:ask", f"对方说的「{phrase}」你没听过是啥梗：可以直接问一句，别装懂", ""
            return None
        if chosen not in rules.use_intents or rng.random() >= rules.use_chance:
            return None
        pool = book.fresh_for(member, at)
        if not pool:
            return None
        meme = rng.choice(pool)
        reply = str(meme.get("reply") or "")
        how = f"，接法是「{reply}」" if reply and meme.get("cue") else ""
        line = (
            f"最近大家在玩梗「{meme['phrase']}」{_gloss(meme)}{how}；"
            "话赶话正好合适才顺手用，不合适就别用，用了也别解释"
        )
        return f"meme:use:{meme['phrase']}", line, ""

    def after(self, member: str, tags: tuple[str, ...], at: datetime) -> None:
        for tag in tags:
            if tag.startswith(("meme:use:", "meme:reply:")):
                self.book.mark_used(member, tag.split(":", 2)[2], at)
