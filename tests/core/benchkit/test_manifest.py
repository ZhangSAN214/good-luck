"""设计套件的零件清单要和代码对得上：生成器按清单挑零件，清单写错就拼不出来。"""

from __future__ import annotations

import json
import re

from roundtable.core.benchkit.quality import KIT_DIR

MANIFEST = json.loads((KIT_DIR / "manifest.json").read_text(encoding="utf-8"))
JS = (KIT_DIR / "kit.js").read_text(encoding="utf-8")
CSS = (KIT_DIR / "kit.css").read_text(encoding="utf-8")
ICONS = (KIT_DIR / "icons.js").read_text(encoding="utf-8")


def test_js_parts_are_exported():
    exported = set(re.findall(r"export (?:function|const) (\w+)", JS)) | {"icon"}
    for part in MANIFEST["parts"]:
        if part["kind"] == "js" and part["name"] != "format":
            assert part["name"] in exported, part["name"]
            assert part["api"].startswith(part["name"]), part["name"]


def test_css_classes_exist():
    for part in MANIFEST["parts"]:
        for cls in part.get("classes", []):
            if "…" in cls:
                continue
            assert re.search(rf"\.{re.escape(cls)}(?![\w-])", CSS), cls


def test_themes_are_defined_for_light_and_dark():
    for theme in MANIFEST["themes"]:
        if theme == "ink":  # 默认
            continue
        assert f'[data-k-theme="{theme}"]' in CSS
        assert f':root[data-k-mode="dark"][data-k-theme="{theme}"]' in CSS


def test_icons_used_by_kit_exist():
    names = set(re.findall(r'^"([\w-]+)":', ICONS, re.M))
    used = set(re.findall(r"icon\('([\w-]+)'", JS))
    assert used <= names, used - names


def test_kit_has_no_external_requests():
    for text in (JS, CSS):
        assert not re.search(r"https?://", re.sub(r"//.*|/\*.*?\*/", "", text, flags=re.S)), (
            "套件不能引外部资源"
        )
