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
