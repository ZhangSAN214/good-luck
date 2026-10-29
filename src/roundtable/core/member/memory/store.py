"""记忆件的写入与整理（P2 是这些表唯一的写入方，docs/MEMBER_ENGINE.md「P2 记忆与联想」）。

- **三路分流**（思路借自 mnemon，自己重写）：每件新事先在全文索引里找像的旧件——
  很像（``merge_similarity``）→ **合并**：旧件命中次数 +1、更新最近一次的时间，原文不改；
  是事实且有点像（``update_similarity``）→ **更新**：写成新件，旧件标「被更新」但保留；
  都不像 → **新增**。只看全文索引的几十件候选，记忆再多也不变慢。
- **原文不改**：程序和心情只改重要度（1～3）和回忆语气（``tone``），每次改动都留在
  ``memory_revisions``（旧值 → 新值、原因）。只有用户能改原文（也留旧版）。
- **脑子里挂着的事**：最多 ``hanging_max`` 件开着；满了再挂新的，最早的那件「放下」（保留记录）。
- **习惯摘要**：每晚用全部经历（不只最近）按领域和合并次数算一段「常做的事」，随身卡用。
- **训练数据**：召回、联想、夜间整理的输入输出记进 ``memory_train``，只存本机、不过期。
- 所有成员用同一套规则（中立）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from roundtable.core.member.memory.domains import WORK, tag
from roundtable.core.recall import grams
from roundtable.core.routing.decide import WORKFLOWS

FACT = "fact"
EPISODE = "episode"
MERGE, UPDATE, NEW = "merge", "update", "new"
SIMILAR_CANDIDATES = 40
# 生活读工作记录时看的工作模式（邮件、革新例会等后台任务不算）
WORK_WORKFLOWS = WORKFLOWS
WORKFLOW_NAMES = {
    "discussion": "讨论",
    "collab": "协同",
    "solo": "个人作答",
    "brainstorm": "头脑风暴",
    "office": "办公室",
}


@dataclass(frozen=True)
class Written:
    route: str  # merge / update / new / skip
    item: dict[str, Any] | None


def similarity(a: str, b: str) -> float:
    """两段话的二字片段重合度（Jaccard，0～1）。"""
    x, y = grams(a, (2,)), grams(b, (2,))
    if not x or not y:
        return 1.0 if a.strip() == b.strip() else 0.0
    return len(x & y) / len(x | y)


def query_terms(text: str, limit: int = 24, size: int = 3) -> list[str]:
    """全文索引的查询片段：size 字片段，越靠后（越新的话）越先，去重，最多 limit 个。"""
    seen: list[str] = []
    for g in sorted(grams(text, (size,)), key=lambda g: -text.rfind(g)):
        if g not in seen:
            seen.append(g)
    return seen[:limit]


class MemoryStore:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.memory

    @property
    def repo(self) -> Any:
        return self.rt.repo

    # --- 写入：三路分流 -----------------------------------------------------------

    def similar(self, member: str, text: str) -> list[tuple[float, dict[str, Any]]]:
        ids = self.repo.search_memory_items(member, query_terms(text), SIMILAR_CANDIDATES)
        rows = [r for r in self.repo.memory_items_by_ids(ids) if r["superseded_by"] is None]
        scored = [(similarity(text, r["text"]), r) for r in rows]
        return sorted(scored, key=lambda x: (-x[0], -x[1]["id"]))

    def write(
        self,
        member: str,
        text: str,
        *,
        day: date | str,
        fact: bool = False,
        domain: str | None = None,
        importance: int = 1,
        source: str = "night",
        source_key: str | None = None,
        at: str | None = None,
    ) -> Written:
        text = " ".join((text or "").split())
        if not text:
            return Written("skip", None)
        day = day if isinstance(day, str) else day.isoformat()
        importance = min(max(int(importance), 1), 3)
        kind = FACT if fact else EPISODE
        domain = tag(text, domain, self.rules.domains)
        found = self.similar(member, text)
        if found and found[0][0] >= self.rules.merge_similarity:
            old = found[0][1]
            self.repo.bump_memory_item(old["id"], at or self.repo.clock())
            if importance > old["importance"]:
                self.repo.revise_memory_item(old["id"], "importance", importance, "合并时更重要")
            return Written(MERGE, self.repo.memory_item(old["id"]))
        new = self.repo.add_memory_item(
            member,
            text,
            day=day,
            kind=kind,
            domain=domain,
            importance=importance,
            source=source,
            source_key=source_key,
            at=at,
        )
        if new is None:  # source_key 重复：同一件事已经记过
            return Written("skip", None)
        if fact:
            for score, old in found:
                if score < self.rules.update_similarity:
                    break
                if old["kind"] == FACT and old["domain"] == domain:
                    self.repo.supersede_memory_item(old["id"], new["id"])
                    return Written(UPDATE, new)
        return Written(NEW, new)

    # --- 心情只改语气和重要度 -------------------------------------------------------

    def retell(self, item_id: int, *, tone: str | None, importance: int | None, why: str) -> int:
        """改一件记忆的回忆语气 / 重要度（原文不改），旧值留版。返回改了几项。"""
        n = 0
        if tone is not None:
            n += self.repo.revise_memory_item(item_id, "tone", " ".join(tone.split())[:80], why)
        if importance is not None:
            n += self.repo.revise_memory_item(
                item_id, "importance", min(max(int(importance), 1), 3), why
            )
        return n

    def user_edit(self, item_id: int, text: str) -> dict[str, Any] | None:
        """用户改原文：留旧版，改过的算事实（不变淡）。"""
        self.repo.revise_memory_item(item_id, "text", text, "用户修改")
        self.repo.revise_memory_item(item_id, "kind", FACT, "用户修改")
        return self.repo.memory_item(item_id)

    # --- 脑子里挂着的事 -------------------------------------------------------------

    def hang(self, member: str, text: str, since: str) -> int | None:
        text = " ".join((text or "").split())[:60]
        if not text or self.rules.hanging_max == 0:
            return None
        open_ = self.repo.memory_hanging(member)
        if any(similarity(text, h["text"]) >= self.rules.merge_similarity for h in open_):
            return None
        for h in open_[: max(len(open_) - self.rules.hanging_max + 1, 0)]:
            self.repo.close_memory_hanging(h["id"], "dropped")
        return self.repo.add_memory_hanging(
            member, text, tag(text, None, self.rules.domains), since
        )

    def settle(self, member: str, hanging_id: int) -> bool:
        if not any(h["id"] == hanging_id for h in self.repo.memory_hanging(member)):
            return False
        return self.repo.close_memory_hanging(hanging_id, "done")

    # --- 生活只读自己的工作记录 -----------------------------------------------------

    def work_text(self, row: dict[str, Any]) -> str:
        mode = WORKFLOW_NAMES.get(row.get("workflow") or "office", "工作")
        title = " ".join((row.get("question") or "").split())[:40]
        return f"在{mode}参加了「{title}」"

    def add_work(self, member: str, row: dict[str, Any], day: str) -> Written:
        """一场工作成为一件经历（同一场只记一次）。匿名开启且未揭晓的由调用方跳过。"""
        return self.write(
            member,
            self.work_text(row),
            day=day,
            domain=WORK,
            source="work",
            source_key=f"work:{row['session_id']}",
        )

    # --- 全量经历的习惯摘要 ---------------------------------------------------------

    def digest(self, member: str) -> str:
        """用全部经历算「常做的事」和经历分布（一两条 SQL，不读全部行）并存下来。"""
        limit = self.rules.digest_chars
        stats = self.repo.memory_item_stats(member)
        top = self.repo.top_memory_items(member, limit=5)
        parts = []
        if top:
            habits = "、".join(f"{r['tone'] or r['text']}（{r['hits']} 次）" for r in top)
            parts.append(f"常做的事：{habits}")
        total = sum(s["hits"] or 0 for s in stats)
        if total:
            share = [f"{s['domain']}{round(100 * (s['hits'] or 0) / total)}%" for s in stats[:4]]
            parts.append("经历多在：" + "、".join(share))
        text = "；".join(parts)
        if len(text) > limit:
            text = text[: max(limit - 1, 0)].rstrip("、；") + "…" if limit else ""
        self.repo.set_memory_digest(
            member, text, {"domains": stats, "top": [r["id"] for r in top], "total": total}
        )
        return text

    def train(self, member: str, kind: str, data: dict[str, Any]) -> None:
        self.repo.add_memory_train(member, kind, data)
