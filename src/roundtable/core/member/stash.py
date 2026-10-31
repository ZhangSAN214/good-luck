"""表情包收藏与跟队形（2026-10-31 拟人插件调研第 5 项；规则在 ``chat.engine.stash``）。

真人聊天里表情包是会「传染」的：朋友发了个好笑的图，你顺手存下来，过几天自己也用上了，
用腻了就不用了；群里两个人先后发了同一句「6」，剩下的人常常也跟一个，然后就停。
思路借 AstrBot 的偷表情包插件和麦麦的「偷表情包」，Python 自己写，全程序、不调模型。

- **收藏**：你在生活群 / 私聊发的图片里像表情包的（动图、宽高都不超过 ``small_px`` 的小图，
  或本来就是表情包的），回你这条的成员看过之后按性格（幽默、好奇）和在不在乎你有一定概率存下来，
  每条消息只掷一次（随机数由消息和成员派生，可复现）。说明用感官已经整理好的感知卡（没看成的不收），
  不另外调模型。每位最多 ``max_keep`` 张，满了丢最旧的。
- **自己用**：收藏的放进她能发的表情包里（记号 ``[表情:收藏·编号]``，提示词里带一句说明），
  刚收的 ``fresh_days`` 天内标「正想用」、还没用过的标「偷来的」——只给事实，怎么说由她自己定
  （不写「偷你的图」这类固定句子）。发出时换成那张图（存成和别的表情包一样的图片消息）；
  用了 ``tired_uses`` 次就腻了、``forget_days`` 天没用就忘了，都会丢掉。
- **跟队形**：群里 ``follow.minutes`` 分钟内两个不同的人（含你）先后发了同一句短话或同一个表情包，
  这一轮该回话的成员按外向、幽默分有概率跟着发一模一样的，最多 ``follow.max_followers`` 位，
  这一串里已经有这么多人跟过就不再跟；问句、关心时段不跟。

只用于生活模式，8 位同一套规则，差别只来自性格分和她在不在乎你。收藏只存本机（app_settings），
在「成员设定」里能看、能删、能一键清空。
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.chat_actions import STICKER_RE
from roundtable.core.storage.repository import iso

USER = "user"
STASH_KEY = "sticker_stash"
PACK = "收藏"
LIFE_KINDS = frozenset({"group", "dm"})
_TOKEN = re.compile(r"\[表情[:：]" + PACK + r"·(\d{1,3})\]")
_DROP = re.compile(r"[\[\]【】<>\n\r\t「」\"']")


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 3.0


def sticker_like(item: dict[str, Any], small_px: int) -> bool:
    """这张图像不像表情包：本来就是表情包、动图，或宽高都不超过 small_px 的小图。"""
    if item.get("kind") != "image":
        return False
    if item.get("sticker") or str(item.get("id", "")).endswith(".gif"):
        return True
    w, h = _num(item.get("w") or 0), _num(item.get("h") or 0)
    return 0 < w <= small_px and 0 < h <= small_px


def chance(rules: Any, traits: dict[str, Any], care: float) -> float:
    raw = rules.base + sum(c * (_num(traits.get(t, 3)) - 3) for t, c in rules.traits.items())
    raw += getattr(rules, "care", 0.0) * care
    return max(0.0, min(rules.max_chance, raw))


class Stash:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.stash

    def enabled(self) -> bool:
        cfg = self.rt.config.roundtable.chat
        return bool(cfg.engine.enabled and self.rules.enabled and cfg.actions.enabled)

    # --- 存取 ------------------------------------------------------------------------

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        raw = self.rt.repo.setting(STASH_KEY)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, list[dict[str, Any]]]) -> None:
        data = {k: v for k, v in data.items() if v}
        self.rt.repo.set_setting(STASH_KEY, json.dumps(data, ensure_ascii=False))

    def _alive(self, row: dict[str, Any], at: datetime) -> bool:
        r = self.rules
        if int(row.get("uses", 0)) >= r.tired_uses:
            return False  # 用腻了
        since = row.get("last") or row.get("at") or ""
        try:
            last = datetime.fromisoformat(since)
        except ValueError:
            return False
        return at - last <= timedelta(days=r.forget_days)

    def items(self, member: str, at: datetime | None = None) -> list[dict[str, Any]]:
        """她现在收着的（腻了、忘了的不算），最近收的在前。"""
        at = at or self.rt.budget.now()
        rows = [r for r in self._load().get(member, []) if self._alive(r, at)]
        return sorted(rows, key=lambda r: r.get("at", ""), reverse=True)

    def listing(self) -> dict[str, list[dict[str, Any]]]:
        at = self.rt.budget.now()
        keep = ("n", "id", "label", "at", "uses")
        return {m: [{k: r.get(k) for k in keep} for r in self.items(m, at)] for m in self._load()}

    def delete(self, member: str, n: int) -> bool:
        data = self._load()
        rows = data.get(member, [])
        left = [r for r in rows if int(r.get("n", 0)) != n]
        if len(left) == len(rows):
            return False
        data[member] = left
        self._save(data)
        return True

    def clear(self, member: str | None = None) -> int:
        data = self._load()
        if member is None:
            gone = sum(len(v) for v in data.values())
            self.rt.repo.set_setting(STASH_KEY, "{}")
            return gone
        gone = len(data.pop(member, []))
        self._save(data)
        return gone

    # --- 收藏 ------------------------------------------------------------------------

    def _traits(self, member: str) -> dict[str, Any]:
        from roundtable.core.member.profile import ProfileStore  # 档案依赖较多，用到再导入

        data = ProfileStore(self.rt).current(member).data
        return dict((data.get("personality") or {}).get("五项") or {})

    def _care(self, member: str, at: datetime) -> float:
        from roundtable.core.member.love import Heart

        try:
            return float(Heart(self.rt).care(member, USER, at))
        except Exception:  # 关系数据坏了也不影响收藏
            return 0.0

    def _label(self, text: str) -> str:
        first = re.split(r"[。！？!?；;\n]", _DROP.sub(" ", text).strip(), maxsplit=1)[0]
        return " ".join(first.split())[: self.rules.label_chars]

    def consider(
        self,
        member: str,
        row: dict[str, Any] | None,
        *,
        at: datetime | None = None,
        traits: dict[str, Any] | None = None,
        care: float | None = None,
    ) -> list[int]:
        """她看到你发的这条：像表情包的图按概率存下来。返回新收的编号。"""
        if not self.enabled() or not row or row.get("author") != USER:
            return []
        meta = row.get("meta") or {}
        cards = {c.get("id"): c for c in meta.get("perception") or [] if isinstance(c, dict)}
        found = [
            (it, cards[it["id"]])
            for it in meta.get("media") or []
            if sticker_like(it, self.rules.small_px) and it.get("id") in cards
        ]
        if not found:
            return []
        at = at or self.rt.budget.now()
        traits = self._traits(member) if traits is None else traits
        care = self._care(member, at) if care is None else care
        p = chance(self.rules, traits, care)
        rng = random.Random(f"stash:{member}:{row.get('id')}")
        data = self._load()
        rows = [r for r in data.get(member, []) if self._alive(r, at)]
        got: list[int] = []
        for it, card in found:
            if rng.random() >= p or any(r.get("id") == it["id"] for r in rows):
                continue
            label = self._label(str(card.get("text", "")))
            if not label:
                continue
            if len(rows) >= self.rules.max_keep:
                rows.sort(key=lambda r: r.get("at", ""))
                rows = rows[1:]  # 满了：丢最旧的
            taken = {int(r.get("n", 0)) for r in rows}
            n = next(i for i in range(1, 1000) if i not in taken)
            rows.append({"n": n, "id": it["id"], "label": label, "at": iso(at), "uses": 0})
            got.append(n)
        if got:
            data[member] = rows
            self._save(data)
        return got

    # --- 自己用 ----------------------------------------------------------------------

    def pack(self, member: str) -> dict[str, list[str]]:
        """放进她能发的表情包里：{收藏: [编号]}。"""
        if not self.enabled():
            return {}
        names = [str(r["n"]) for r in self.items(member)]
        return {PACK: names} if names else {}

    def menu(self, member: str) -> str:
        """提示词里的一句：她收着哪几张、各是什么（刚收的、还没用过的标一下）。"""
        if not self.enabled():
            return ""
        at = self.rt.budget.now()
        r = self.rules
        out = []
        for row in self.items(member, at)[: r.menu_max]:
            notes = [row["label"]]
            try:
                fresh = at - datetime.fromisoformat(row["at"]) <= timedelta(days=r.fresh_days)
            except ValueError:
                fresh = False
            if not row.get("uses"):
                notes.append(r.unused)
            elif fresh:
                notes.append(r.fresh)
            out.append(f"[表情:{PACK}·{row['n']}]（{'，'.join(notes)}）")
        return r.menu.format(items="、".join(out)) if out else ""

    def extract(self, member: str, text: str) -> tuple[str, list[dict[str, Any]]]:
        """把话里收藏的表情包记号拿出来：返回（剩下的话，要发的图片清单）。用了就记一次。"""
        if not self.enabled() or not _TOKEN.search(text):
            return text, []
        at = self.rt.budget.now()
        data = self._load()
        rows = {int(r.get("n", 0)): r for r in data.get(member, []) if self._alive(r, at)}
        items: list[dict[str, Any]] = []

        def take(m: re.Match[str]) -> str:
            row = rows.get(int(m.group(1)))
            if row is not None and all(i["id"] != row["id"] for i in items):
                items.append({"id": row["id"], "kind": "image", "sticker": True})
                row["uses"] = int(row.get("uses", 0)) + 1
                row["last"] = iso(at)
            return ""

        rest = re.sub(r"[ \t]+$", "", _TOKEN.sub(take, text), flags=re.M).strip()
        if items:
            self._save(data)
        return rest, items

    def label_of(self, member: str, media_id: str) -> str:
        for r in self._load().get(member, []):
            if r.get("id") == media_id:
                return str(r.get("label", ""))
        return ""


# --- 跟队形 ----------------------------------------------------------------------------


def chain_key(row: dict[str, Any], max_chars: int, small_px: int) -> tuple[str, str] | None:
    """这条能不能「跟」：一句很短的话（不是问句）或只发了一个表情包。"""
    if (row.get("meta") or {}).get("recalled"):
        return None
    media = (row.get("meta") or {}).get("media") or []
    text = " ".join(str(row.get("text") or "").split())
    if len(media) == 1 and sticker_like(media[0], small_px):
        return ("media", str(media[0]["id"]))
    if media or not text:
        return None
    if len(STICKER_RE.sub("表", text)) > max_chars or re.search(r"[?？]", text):  # 表情包算一个字
        return None
    return ("text", text)


class Formation:
    """群里跟队形：只看聊天记录和性格分，程序直接发。"""

    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.stash.follow

    def followers(
        self,
        room: str,
        msg: dict[str, Any],
        candidates: list[str],
        *,
        stickers: dict[str, dict[str, list[str]]] | None = None,
        traits: dict[str, dict[str, Any]] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """返回 [(成员 id, {"text": …, "media": …})]：这一轮跟着发同一个的人和要发的东西。"""
        stash = Stash(self.rt)
        r = self.rules
        if not (stash.enabled() and r.enabled and r.max_followers):
            return []
        small = stash.rules.small_px
        key = chain_key(msg, r.max_chars, small)
        if key is None:
            return []
        try:
            at = datetime.fromisoformat(msg["created_at"])
        except (KeyError, TypeError, ValueError):
            at = self.rt.budget.now()
        since = at - timedelta(minutes=r.minutes)
        rows = self.rt.repo.chat_messages(room, limit=40)
        authors: list[str] = []
        for row in rows:
            if row["id"] > msg["id"] or row["author"] == "system":
                continue
            try:
                when = datetime.fromisoformat(row["created_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if when < since:
                continue
            if chain_key(row, r.max_chars, small) == key:
                authors.append(row["author"])
        if len(set(authors)) < 2:
            return []  # 只有一个人发过：还不成队形
        room_left = r.max_followers - (len(set(authors)) - 2)  # 前两位起头，之后的都算跟的
        if room_left <= 0:
            return []  # 这一串已经有人跟够了
        out: list[tuple[str, dict[str, Any]]] = []
        for member in candidates:
            if len(out) >= room_left:
                break
            if member in authors:
                continue
            what: dict[str, Any]
            if key[0] == "media":
                what = {
                    "text": "[表情]",
                    "media": [{"id": key[1], "kind": "image", "sticker": True}],
                }
            else:
                text = key[1]
                for m in STICKER_RE.finditer(text):  # 别人的专属表情她发不了
                    if m.group(2) not in (stickers or {}).get(member, {}).get(m.group(1), []):
                        what = {}
                        break
                else:
                    what = {"text": text}
                if not what:
                    continue
            mine = (traits or {}).get(member, {})
            rng = random.Random(f"follow:{room}:{msg['id']}:{member}")
            if rng.random() < chance(r, mine, 0.0):
                out.append((member, what))
        return out
