"""工具调用的文字协议：成员在输出中写

    <tool_call name="python">
    print(1 + 1)
    </tool_call>

    <tool_call name="write_file" path="report.md">
    文件内容
    </tool_call>

代码解析并执行，把结果追加到同一对话后再调用一次；不再申请工具的那次输出就是最终结果。
不用各家原生的 function calling：格式各不相同，部分模型不支持，难以保证所有成员同样对待。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CALL = re.compile(r"<tool_call\b([^>]*)>(.*?)</tool_call\s*>", re.DOTALL)
_OPEN = re.compile(r"<tool_call\b", re.IGNORECASE)
_ATTR = re.compile(r'([A-Za-z_][\w-]*)\s*=\s*"([^"]*)"')
_FENCE = re.compile(r"^\s*```[\w+-]*\s*\n(.*?)\n\s*```\s*$", re.DOTALL)


@dataclass(frozen=True)
class ToolRequest:
    name: str
    attrs: dict[str, str] = field(default_factory=dict)
    body: str = ""


def _body(raw: str) -> str:
    text = raw.strip("\n")
    fenced = _FENCE.match(text)
    return fenced.group(1) if fenced else text


def parse_calls(text: str) -> list[ToolRequest]:
    """按出现顺序解析全部工具调用。没有 name 的调用也返回（name 为空，执行时被拒绝）。"""
    out = []
    for m in _CALL.finditer(text):
        attrs = dict(_ATTR.findall(m.group(1)))
        out.append(ToolRequest(attrs.pop("name", "").strip(), attrs, _body(m.group(2))))
    return out


def has_unclosed_call(text: str) -> bool:
    """有 <tool_call 开头但没有闭合（多半是输出被截断）。"""
    return len(_OPEN.findall(text)) > len(_CALL.findall(text))


def strip_calls(text: str) -> str:
    """去掉输出中的工具调用（最终结果里不应出现）。"""
    text = _CALL.sub("", text)
    cut = _OPEN.search(text)
    if cut:  # 未闭合的调用：从这里截掉
        text = text[: cut.start()]
    return re.sub(r"\n{3,}", "\n\n", text).strip()
