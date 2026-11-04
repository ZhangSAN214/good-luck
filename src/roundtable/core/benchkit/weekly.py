"""资料台周报（2026-11-02 用户要）：每周一份「这周有什么值得看的」，不限插件。

内容全部取自资料台（``desk.py``）已经抓到的：GitHub 榜单与新星、论文、资讯和发布、建议卡。
每条写一句**为什么值得看**，仓库再带一行中文「大概做什么」；全由程序按规则写，不调模型、不花钱。

- **什么时候出**：每周 ``weekly_weekday``（默认周一）``weekly_hour`` 点（主时区）之后
  第一次看表时出；电脑那时关着，就在当周第一次开着时补上（一周只出一份）。
  资料超过 ``refresh_hours`` 没刷新的先刷新一次。
- **和上周比**：「新进榜」是这周总榜里上周周报还没有的；上周周报已经写过的条目这周不再重复。
  第一期没得比，就列各类榜首。
- **存在哪**：``data/bench/_desk/weekly/<年>-W<周>.json`` 和同名 ``.md``（可以直接打开看）。

外部标题和简介只当资料：页面上转义显示，Markdown 里去掉方括号和换行。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from roundtable.core.benchkit.desk import DESK_DIR, Desk
from roundtable.core.textutil import iso_week as week_of

WEEKLY_DIR = "weekly"
WEEK_ID = re.compile(r"^\d{4}-W\d{2}$")
DAYS = "一二三四五六日"


def _days_since(day: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(day[:10])).days
    except ValueError:
        return None


def _stars(n: int) -> str:
    if n >= 10000:
        return f"{n / 10000:.1f}".rstrip("0").rstrip(".") + " 万星"
    return f"{n} 星"


def _md(text: Any) -> str:
    return re.sub(r"[\[\]\r\n]+", " ", str(text or "")).strip()


class Weekly:
    """周报：到点生成（先保证资料是新的）、读历史、手动现在生成。"""

    def __init__(self, desk: Desk, *, now: Callable[[], datetime]) -> None:
        self.desk = desk
        self.rules = desk.rules
        self.now = now  # 带主时区的当前时间

    # --- 存取 ---------------------------------------------------------------------

    @property
    def dir(self) -> Path | None:
        return None if self.desk.root is None else self.desk.root / DESK_DIR / WEEKLY_DIR

    def weeks(self) -> list[str]:
        d = self.dir
        if d is None or not d.is_dir():
            return []
        return sorted((p.stem for p in d.glob("*.json") if WEEK_ID.match(p.stem)), reverse=True)

    def get(self, week: str) -> dict[str, Any] | None:
        d = self.dir
        if d is None or not WEEK_ID.match(week):
            return None
        p = d / f"{week}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
        except (OSError, ValueError):
            return None

    def _save(self, rep: dict[str, Any]) -> None:
        d = self.dir
        if d is None:
            return
        d.mkdir(parents=True, exist_ok=True)
        for suffix, text in (
            (".json", json.dumps(rep, ensure_ascii=False, indent=1)),
            (".md", markdown(rep)),
        ):
            p = d / f"{rep['week']}{suffix}"
            tmp = p.with_suffix(suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8-sig" if suffix == ".md" else "utf-8")
            os.replace(tmp, p)

    # --- 什么时候出 -----------------------------------------------------------------

    def trigger_at(self, t: datetime | None = None) -> datetime:
        """这一周该出周报的时刻（周一 = 0）。"""
        t = t or self.now()
        monday = (t - timedelta(days=t.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return monday + timedelta(days=self.rules.weekly_weekday, hours=self.rules.weekly_hour)

    def due(self) -> bool:
        if not (self.rules.enabled and self.rules.weekly) or self.dir is None:
            return False
        now = self.now()
        return now >= self.trigger_at(now) and self.get(week_of(now)) is None

    def view(self) -> dict[str, Any]:
        weeks = self.weeks()
        rules = self.rules
        return {
            "enabled": bool(rules.enabled and rules.weekly),
            "when": f"每周{DAYS[rules.weekly_weekday]} {rules.weekly_hour:02d}:00",
            "weeks": weeks,
            "latest": self.get(weeks[0]) if weeks else None,
            "due": self.due(),
            "busy": self.desk.busy,
        }

    async def tick(self, *, force: bool = False) -> dict[str, Any] | None:
        """到点了（或 force）就出本周的周报。资料过期先刷新（联网、免费，约 3～5 分钟）。"""
        if not (force or self.due()):
            return None
        if self.dir is None or not self.rules.enabled:
            return None
        if self.desk.busy:
            await self.desk.wait()
        elif self.desk.due():
            await self.desk.refresh()
        return self.build()

    # --- 生成 ---------------------------------------------------------------------

    def build(self, week: str | None = None) -> dict[str, Any]:
        now = self.now()
        week = week or week_of(now)
        today = now.date()
        v = self.desk.view()
        older = [w for w in self.weeks() if w < week]
        prev = self.get(older[0]) if older else None
        shown_before = set((prev or {}).get("shown") or [])
        seen_before = (prev or {}).get("rank_seen") or {}
        taken: set[str] = set()
        cap = self.rules.weekly_max

        def take(items: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
            out = []
            for x in items:
                if len(out) >= n:
                    break
                if x["url"] in taken or x["url"] in shown_before:
                    continue
                taken.add(x["url"])
                out.append(x)
            return out

        def repo_item(r: dict[str, Any], why: str) -> dict[str, Any]:
            return {
                "kind": "repo",
                "title": r["repo"],
                "url": r["url"],
                "why": why,
                "zh": r.get("zh") or "",
                "desc": r.get("desc") or "",
                "source": "GitHub",
            }

        sections: list[dict[str, Any]] = []
        rank = v.get("rank") or {}
        cats = v.get("categories") or []
        # 1. 新进榜（第一期没得比：各类榜首）
        fresh: list[dict[str, Any]] = []
        for c in cats:
            block = rank.get(c["id"]) or {}
            before = set(seen_before.get(c["id"]) or [])
            for i, r in enumerate(block.get("total") or []):
                if prev is None:
                    if i == 0:
                        fresh.append(
                            repo_item(r, f"「{c['label']}」总榜第 1，{_stars(r['stars'])}")
                        )
                elif before and r["repo"] not in before:
                    fresh.append(
                        repo_item(
                            r,
                            f"本周新进「{c['label']}」总榜第 {i + 1} 名，{_stars(r['stars'])}",
                        )
                    )
        sections.append(
            {
                "id": "rank",
                "title": "本周新进榜" if prev else "各类榜首（第一期，下周起能比出谁是新进榜的）",
                "items": take(fresh, 8),
            }
        )
        # 2. 新冒头的项目：最近新建、星数涨得快
        rising: list[dict[str, Any]] = []
        for c in cats:
            for r in sorted(
                (rank.get(c["id"]) or {}).get("rising") or [], key=lambda r: -r["stars"]
            )[:2]:
                days = _days_since(r.get("created") or "", today)
                age = f"{days} 天前才建" if days is not None and days >= 0 else "最近才建"
                rising.append(
                    repo_item(r, f"{age}，已经 {_stars(r['stars'])}，「{c['label']}」里涨得最快")
                )
        sections.append({"id": "rising", "title": "新冒头的项目", "items": take(rising, 6)})
        # 3. 论文
        feed = v.get("feed") or []
        recent = [
            x
            for x in feed
            if (d := _days_since(x.get("date") or "", today)) is not None and 0 <= d <= 7
        ]
        papers: list[dict[str, Any]] = []
        hf = sorted(
            (x for x in feed if x["kind"] == "paper" and x.get("score")),
            key=lambda x: -x["score"],
        )
        for i, x in enumerate(hf[:3]):
            why = f"Hugging Face 每日论文点赞 {x['score']}" + ("，这期最高" if i == 0 else "")
            papers.append(self._feed_item(x, why))
        per_topic: dict[str, int] = {}
        for x in recent:
            if x["kind"] != "paper" or not x["source"].startswith("arXiv"):
                continue
            topic = x["source"].split("·", 1)[-1].strip()
            if per_topic.get(topic, 0) >= 1:
                continue
            per_topic[topic] = 1
            papers.append(self._feed_item(x, f"和你关注的「{topic}」有关的新论文"))
        sections.append({"id": "papers", "title": "论文", "items": take(papers, 6)})
        # 4. 资讯与发布
        news = [
            self._feed_item(x, f"{x['source']} 本周的新消息")
            for x in recent
            if x["kind"] != "paper"
        ]
        sections.append({"id": "news", "title": "资讯与发布", "items": take(news, 6)})
        # 5. 建议加进能力地图的工具
        sugg = [
            repo_item(
                s,
                f"按关注主题「{s['direction']}」找到，{_stars(s['stars'])}、{s['license']} 许可，"
                "能直接用；在「资料 → 建议」里点「加进地图」",
            )
            for s in v.get("suggest") or []
        ]
        sections.append(
            {"id": "suggest", "title": "可以加进能力地图的工具", "items": take(sugg, 5)}
        )

        # 总数不超过 weekly_max：从后面的分类往前砍
        total = sum(len(s["items"]) for s in sections)
        for s in reversed(sections):
            while total > cap and s["items"]:
                s["items"].pop()
                total -= 1
        sections = [s for s in sections if s["items"]]
        counts = "、".join(f"{s['title'].split('（')[0]} {len(s['items'])}" for s in sections)
        rep = {
            "week": week,
            "generated_at": now.isoformat(timespec="seconds"),
            "desk_updated_at": v.get("updated_at"),
            "intro": (
                f"这周有 {total} 条值得看：{counts}。" if total else "这周资料台没抓到新东西。"
            ),
            "sections": sections,
            "notes": list(v.get("notes") or [])[:10],
            "shown": sorted(taken | shown_before)[-400:],
            "rank_seen": {
                c["id"]: [r["repo"] for r in (rank.get(c["id"]) or {}).get("total") or []]
                for c in cats
            },
        }
        self._save(rep)
        return rep

    @staticmethod
    def _feed_item(x: dict[str, Any], why: str) -> dict[str, Any]:
        return {
            "kind": x["kind"],
            "title": x["title"],
            "url": x["url"],
            "why": why,
            "zh": "",
            "desc": x.get("summary") or "",
            "source": x.get("source") or "",
            "date": x.get("date") or "",
        }


def markdown(rep: dict[str, Any]) -> str:
    """周报的 Markdown 版（存成文件，双击就能看）。"""
    lines = [f"# 工作台资料周报 {rep['week']}", "", rep["intro"], ""]
    for s in rep["sections"]:
        lines += [f"## {s['title']}", ""]
        for x in s["items"]:
            lines.append(f"- [{_md(x['title'])}]({x['url']})：{_md(x['why'])}")
            if x.get("zh"):
                lines.append(f"  - {_md(x['zh'])}")
        lines.append("")
    if rep.get("notes"):
        lines += ["## 这次没取到的", ""] + [f"- {_md(n)}" for n in rep["notes"]] + [""]
    at = rep["generated_at"][:16].replace("T", " ")
    lines.append(f"生成于 {at}，资料全部来自工作台资料台，不调模型。")
    return "\n".join(lines) + "\n"
