from __future__ import annotations

from roundtable.core.tools import has_unclosed_call, parse_calls, strip_calls


def test_parse_calls_with_attrs_and_fences():
    text = """先算一下。
<tool_call name="python">
```python
print(2 ** 10)
```
</tool_call>
<tool_call name="write_file" path="out/a.md">
# 标题
内容 {{ x }} 不展开
</tool_call>"""
    calls = parse_calls(text)
    assert [c.name for c in calls] == ["python", "write_file"]
    assert calls[0].body == "print(2 ** 10)"
    assert calls[1].attrs == {"path": "out/a.md"} and "{{ x }}" in calls[1].body
    assert strip_calls(text) == "先算一下。"


def test_unclosed_and_missing_name():
    assert has_unclosed_call('答案<tool_call name="python">\nprint(1')
    assert strip_calls('答案\n<tool_call name="python">\nprint(1') == "答案"
    assert not has_unclosed_call("没有工具调用")
    assert parse_calls("<tool_call>x</tool_call>")[0].name == ""
    assert parse_calls("plain") == []
