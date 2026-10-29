"""成果 Markdown 的拆块与分节。"""

from __future__ import annotations

from roundtable.core.export.markdown import parse, plain, runs, title_and_sections

SAMPLE = """# 标题

> 题目一行

第一段 **粗体** 和 `代码`，
接着一行。

## 第一节

1. 甲
2. 乙
   - 子项

| 甲 | 乙 |
|---|---|
| 1 | 2 |

```
# 不是标题
```

---
"""


def test_blocks_cover_the_common_markdown():
    kinds = [b.kind for b in parse(SAMPLE)]
    assert kinds == [
        "heading", "quote", "para", "heading", "bullet", "bullet", "bullet", "table", "code", "hr"
    ]  # fmt: skip
    blocks = parse(SAMPLE)
    assert blocks[2].text == "第一段 **粗体** 和 `代码`， 接着一行。"
    assert blocks[4].ordered and blocks[4].number == 1 and blocks[6].level == 1
    assert blocks[7].rows == [["甲", "乙"], ["1", "2"]]
    assert blocks[8].text == "# 不是标题"


def test_inline_runs_and_links():
    rs = runs("前 **粗** *斜* `码` [文档](https://example.com) 后")
    assert [(r.text, r.bold, r.italic, r.code) for r in rs if r.text.strip()] == [
        ("前 ", False, False, False),
        ("粗", True, False, False),
        ("斜", False, True, False),
        ("码", False, False, True),
        ("文档（https://example.com）", False, False, False),
        (" 后", False, False, False),
    ]
    assert plain("a*b*c 2*3*4") == "a*b*c 2*3*4"  # 词中的星号不当斜体


def test_sections_split_on_first_and_second_level_headings():
    title, sections = title_and_sections(parse(SAMPLE))
    assert title == "标题"
    assert [name for name, _ in sections] == ["", "第一节"]
