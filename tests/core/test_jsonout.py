from __future__ import annotations

import pytest

from roundtable.core.jsonout import JSONOutputError, extract_json_object


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        '好的，结果如下：\n{"a": 1}\n以上。',
    ],
)
def test_extracts(text):
    assert extract_json_object(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2]", '{"a": }', "{broken"])
def test_errors(text):
    with pytest.raises(JSONOutputError):
        extract_json_object(text)


def test_invalid_backslash_escapes_and_trailing_commas_are_repaired():
    got = extract_json_object('{"a": "公式 \\(x+1\\) 与 \\sigma", "b": [1, 2,], }')
    assert got["a"] == "公式 \\(x+1\\) 与 \\sigma" and got["b"] == [1, 2]


def test_a_bare_array_is_wrapped():
    got = extract_json_object('结果：[{"target": "x"}, {"target": "y"},]')
    assert got == {"_list": [{"target": "x"}, {"target": "y"}]}


def test_valid_escapes_are_left_alone():
    assert (
        extract_json_object('{"a": "第一行\\n第二行 \\"引号\\" \\\\"}')["a"]
        == '第一行\n第二行 "引号" \\'
    )
