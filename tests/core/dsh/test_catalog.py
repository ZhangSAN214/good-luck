from __future__ import annotations

from pathlib import Path

import pytest

from roundtable.core.dsh.catalog import CatalogError, load_catalog

REPO = Path(__file__).resolve().parents[3] / "config" / "dsh_plugins.yaml"

GOOD = """
runtime: {package: "@x/dsh", version: 1.2.3}
plugins:
  - id: demo
    name: demo-plugin
    kind: panel
    url: https://example.com/demo.tgz
    sha256: %s
    version: 0.1.0
    dsh: [1.2.3]
    license: MIT
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "dsh_plugins.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_repo_catalog_loads_and_pins_versions():
    cat = load_catalog(REPO)
    assert cat.runtime.version.count(".") >= 2  # 确切版本号，不是范围
    ids = [p.id for p in cat.enabled]
    assert ids == ["worktable"]
    worktable = cat.enabled[0]
    assert worktable.url.startswith("https://") and len(worktable.sha256) == 64
    assert cat.runtime.version in worktable.dsh


def test_repo_catalog_has_no_persona_plugins():
    """dsh 不带人格（硬性原则 4）：白名单里没有人设类插件。"""
    cat = load_catalog(REPO)
    for p in cat.plugins:
        assert "maid" not in p.name.lower() and "persona" not in p.name.lower()


def test_good_catalog(tmp_path):
    cat = load_catalog(write(tmp_path, GOOD % ("a" * 64)))
    assert cat.summary()[0]["id"] == "demo"


@pytest.mark.parametrize(
    "bad, where",
    [
        ((GOOD % ("a" * 64)).replace("https://", "http://"), "url"),
        (GOOD % ("A" * 64), "sha256"),
        ((GOOD % ("a" * 64)).replace("version: 1.2.3}", "version: '>=1.2'}"), "runtime.version"),
        ((GOOD % ("a" * 64)).replace("dsh: [1.2.3]", "dsh: [9.9.9]"), "9.9.9"),
        ((GOOD % ("a" * 64)) + "extra: 1\n", "extra"),
    ],
)
def test_bad_catalog_names_file_and_field(tmp_path, bad, where):
    with pytest.raises(CatalogError) as exc:
        load_catalog(write(tmp_path, bad))
    assert "dsh_plugins.yaml" in str(exc.value) and where in str(exc.value)
