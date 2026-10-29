"""工作台生成器自己长出来的东西：新零件和配方（docs/KIT.md「自行拓展」）。

- 新零件：写窗口的助手觉得零件库不够用时，在工作台的 ``parts/`` 里做通用零件（文件开头
  ``// part: 名字 — 用途``）。这个工作台**过了质检和评审**，零件才收进本机零件库
  ``data/bench/_library/parts/``；以后生成工作台时先拷进工作文件夹，拆需求的助手也能看到。
- 配方：过线的工作台把原话、规格、用到的插件和分数存成配方（``_library/recipes/``），
  以后有人要类似的台，拆需求的助手可以参考。
只在本机，不上传；仓库里的 ``web/kit/`` 不会被自动改动（要并进去，走正常的 PR）。
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HEADER = re.compile(r"^//\s*part:\s*([a-z0-9][a-z0-9-]{1,40})\s*[—–-]+\s*(.{2,120})$")
LIBRARY = "_library"


class Library:
    def __init__(self, root: Path) -> None:
        self.root = root / LIBRARY
        self.parts_dir = self.root / "parts"
        self.recipes_dir = self.root / "recipes"

    def parts(self) -> list[dict[str, str]]:
        out = []
        if self.parts_dir.is_dir():
            for p in sorted(self.parts_dir.glob("*.js")):
                head = read_header(p)
                if head:
                    out.append({"name": head[0], "use": head[1], "file": p.name})
        return out

    def brief(self) -> str:
        """给拆需求的助手看的一段：本机零件库里额外的零件。"""
        items = self.parts()
        if not items:
            return ""
        return "以前的工作台做出来的零件（在 parts/ 里）：\n" + "\n".join(
            f"- {x['name']}：{x['use']}" for x in items
        )

    def seed(self, folder: Path) -> int:
        """把本机零件库拷进这个工作台的 parts/（写窗口的助手可以直接 import）。"""
        dest = folder / "parts"
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        if self.parts_dir.is_dir():
            for p in self.parts_dir.glob("*.js"):
                shutil.copyfile(p, dest / p.name)
                n += 1
        return n

    def promote(self, folder: Path, bench_id: str) -> list[str]:
        """过线以后：parts/ 里新做的、开头写清楚了的零件收进本机零件库。返回收进来的名字。"""
        src = folder / "parts"
        if not src.is_dir():
            return []
        known = {x["name"] for x in self.parts()}
        got = []
        for p in sorted(src.glob("*.js")):
            head = read_header(p)
            if not head or head[0] in known or p.stem != head[0]:
                continue
            self.parts_dir.mkdir(parents=True, exist_ok=True)
            text = p.read_text(encoding="utf-8")
            day = f"{datetime.now(UTC):%Y-%m-%d}"
            note = f"// 来自工作台 {bench_id}，{day} 过了质检和评审后收进零件库\n"
            lines = text.split("\n", 1)
            (self.parts_dir / p.name).write_text(
                lines[0] + "\n" + note + (lines[1] if len(lines) > 1 else ""), encoding="utf-8"
            )
            got.append(head[0])
        return got

    def save_recipe(self, build: dict[str, Any], spec: dict[str, Any]) -> Path:
        self.recipes_dir.mkdir(parents=True, exist_ok=True)
        recipe = {
            "request": build["request"],
            "title": spec.get("title"),
            "spec": spec,
            "sources": [
                {k: s.get(k) for k in ("repo", "url", "license", "verdict", "why", "need")}
                for s in build.get("sources") or []
            ],
            "score": build.get("score"),
            "bench": build["id"],
            "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        path = self.recipes_dir / f"{build['id']}.json"
        path.write_text(json.dumps(recipe, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def recipes(self) -> list[dict[str, Any]]:
        out = []
        if self.recipes_dir.is_dir():
            for p in sorted(self.recipes_dir.glob("*.json"), reverse=True):
                try:
                    r = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                out.append(
                    {k: r.get(k) for k in ("bench", "title", "request", "score", "saved_at")}
                )
        return out


def read_header(path: Path) -> tuple[str, str] | None:
    try:
        with path.open(encoding="utf-8") as f:
            first = f.readline().strip()
    except (OSError, UnicodeDecodeError):
        return None
    m = HEADER.match(first)
    return (m.group(1), m.group(2).strip()) if m else None
