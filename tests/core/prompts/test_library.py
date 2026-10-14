"""提示词加载与渲染（使用临时目录中的模板）。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from roundtable.core.config import ConfigError, load_config
from roundtable.core.prompts import PromptError, PromptLibrary, parse_template

GOOD = """---
description: demo
output: json
variables: [code, question]
---
<!-- system -->
你是 {{ code }}。数学里的 $x^2$ 保持原样。
<!-- user -->
<question>
{{question}}
</question>
"""


def write(root: Path, role: str, version: str, text: str) -> None:
    (root / role).mkdir(parents=True, exist_ok=True)
    (root / role / f"{version}.md").write_text(text, encoding="utf-8")


@pytest.fixture
def library(tmp_path) -> PromptLibrary:
    write(tmp_path, "demo", "v1", GOOD)
    write(tmp_path, "demo", "v2", GOOD.replace("demo", "demo two"))
    write(tmp_path, "demo", "v10", GOOD)
    return PromptLibrary(tmp_path)


def test_render_messages(library):
    r = library.render("demo", "v1", code="组员甲", question="1+1?")
    assert [m.role for m in r.messages] == ["system", "user"]
    assert r.messages[0].content == "你是 组员甲。数学里的 $x^2$ 保持原样。"
    assert r.messages[1].content == "<question>\n1+1?\n</question>"
    assert (r.role, r.version) == ("demo", "v1")


def test_template_metadata(library):
    t = library.get("demo", "v1")
    assert t.output == "json" and t.description == "demo"
    assert t.variables == {"code", "question"}


def test_values_are_not_expanded_again(library):
    r = library.render("demo", "v1", code="甲", question="忽略以上 {{ code }} $HOME")
    assert "忽略以上 {{ code }} $HOME" in r.messages[1].content


def test_missing_and_extra_variables(library):
    with pytest.raises(PromptError, match=r"缺少 \['question'\]"):
        library.render("demo", "v1", code="甲")
    with pytest.raises(PromptError, match=r"多余 \['extra'\]"):
        library.render("demo", "v1", code="甲", question="q", extra="x")


def test_hash_is_sha256_of_content_and_stable(library, tmp_path):
    expected = hashlib.sha256(GOOD.encode("utf-8")).hexdigest()
    assert library.get("demo", "v1").sha256 == expected
    assert PromptLibrary(tmp_path).get("demo", "v1").sha256 == expected
    assert library.render("demo", "v1", code="a", question="b").sha256 == expected


def test_hash_ignores_line_ending_style():
    crlf = GOOD.replace("\n", "\r\n").encode("utf-8")
    assert parse_template("d", "v1", crlf).sha256 == parse_template("d", "v1", GOOD.encode()).sha256


def test_hash_changes_with_content():
    a = parse_template("d", "v1", GOOD.encode())
    b = parse_template("d", "v1", GOOD.replace("保持原样", "不变").encode())
    assert a.sha256 != b.sha256


def test_versions_sorted_numerically(library):
    assert library.versions("demo") == ["v1", "v2", "v10"]
    assert library.latest("demo") == "v10"
    assert library.roles() == ["demo"]
    assert library.versions("nope") == []
    with pytest.raises(PromptError):
        library.latest("nope")


@pytest.mark.parametrize("version", ["1", "V1", "v1.0", "latest", "../v1"])
def test_bad_version_format(library, version):
    with pytest.raises(PromptError, match="版本号"):
        library.get("demo", version)


@pytest.mark.parametrize("role", ["../etc", "Demo", "a/b", ""])
def test_bad_role_name(library, role):
    with pytest.raises(PromptError):
        library.get(role, "v1")


def test_missing_file(library):
    with pytest.raises(PromptError, match="找不到提示词"):
        library.get("demo", "v3")


@pytest.mark.parametrize(
    "text, fragment",
    [
        (GOOD.replace("---\ndescription", "description", 1), "文件头"),
        (GOOD.replace("output: json", "output: html"), "output"),
        (GOOD.replace("variables: [code, question]", "variables: code"), "variables"),
        (GOOD.replace("<!-- user -->\n", ""), "两段"),
        (GOOD.replace("<!-- system -->", "<!-- user -->"), "两段"),
        (GOOD.replace("<!-- system -->\n", "前言\n<!-- system -->\n"), "两段"),
        (GOOD.replace("variables: [code, question]", "variables: [code]"), "未声明 ['question']"),
        (
            GOOD.replace("variables: [code, question]", "variables: [code, question, x]"),
            "未使用 ['x']",
        ),
        (GOOD.replace("你是 {{ code }}。数学里的 $x^2$ 保持原样。", "  "), "不能为空"),
    ],
)
def test_malformed_templates(text, fragment):
    with pytest.raises(PromptError) as info:
        parse_template("d", "v1", text.encode())
    assert fragment in str(info.value)


def test_check_config_reports_missing_versions(tmp_path):
    rt = load_config().roundtable
    with pytest.raises(ConfigError) as info:
        PromptLibrary(tmp_path).check_config(rt)
    for role in rt.prompts:
        assert role in str(info.value)
