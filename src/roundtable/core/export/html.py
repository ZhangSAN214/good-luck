"""Markdown 块 → 独立的网页（.html）：样式内联、不带脚本，可以在浏览器里「打印 → 另存为 PDF」。"""

from __future__ import annotations

from html import escape

from roundtable.core.export.markdown import Block, parse, runs

_CSS = """
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;color:#1f2328;
background:#fff;max-width:820px;margin:40px auto;padding:0 16px;line-height:1.75;font-size:16px}
h1{font-size:28px;border-bottom:2px solid #2f5597;padding-bottom:8px}
h2{font-size:22px;border-bottom:1px solid #d0d7de;padding-bottom:4px;margin-top:32px}
h3{font-size:18px}
blockquote{margin:0;padding:4px 14px;border-left:4px solid #d0d7de;color:#57606a}
pre{background:#f6f8fa;padding:12px;border-radius:6px;overflow-x:auto;font-size:13.5px}
code{font-family:Consolas,"Courier New",monospace;background:#f6f8fa;padding:0 3px;
border-radius:3px}
pre code{background:none;padding:0}
table{border-collapse:collapse;margin:12px 0}
th,td{border:1px solid #d0d7de;padding:6px 10px;text-align:left;vertical-align:top}
th{background:#f6f8fa}
@media print{body{margin:0;max-width:none}h2{break-after:avoid}pre,table{break-inside:avoid}}
"""


def _inline(text: str) -> str:
    out = []
    for r in runs(text):
        t = escape(r.text)
        if r.code:
            t = f"<code>{t}</code>"
        if r.italic:
            t = f"<em>{t}</em>"
        if r.bold:
            t = f"<strong>{t}</strong>"
        out.append(t)
    return "".join(out)


def _body(blocks: list[Block]) -> str:
    out: list[str] = []
    stack: list[tuple[str, int]] = []  # 当前打开的列表（标签、缩进）

    def close(to: int = -1) -> None:
        while stack and stack[-1][1] > to:
            out.append(f"</{stack.pop()[0]}>")

    for b in blocks:
        if b.kind != "bullet":
            close()
        if b.kind == "heading":
            out.append(f"<h{b.level}>{_inline(b.text)}</h{b.level}>")
        elif b.kind == "bullet":
            tag = "ol" if b.ordered else "ul"
            close(b.level)
            if stack and stack[-1][1] == b.level and stack[-1][0] != tag:
                close(b.level - 1)
            if not stack or stack[-1][1] < b.level:
                start = f' start="{b.number}"' if b.ordered and b.number > 1 else ""
                out.append(f"<{tag}{start}>")
                stack.append((tag, b.level))
            out.append(f"<li>{_inline(b.text)}</li>")
        elif b.kind == "code":
            out.append(f"<pre><code>{escape(b.text)}</code></pre>")
        elif b.kind == "table":
            head = "".join(f"<th>{_inline(c)}</th>" for c in b.rows[0])
            rows = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
                for row in b.rows[1:]
            )
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
        elif b.kind == "quote":
            out.append(f"<blockquote>{_inline(b.text)}</blockquote>")
        elif b.kind == "hr":
            out.append("<hr>")
        else:
            out.append(f"<p>{_inline(b.text)}</p>")
    close()
    return "\n".join(out)


def render(md: str, title: str = "") -> bytes:
    page = (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title or '圆桌成果')}</title><style>{_CSS}</style></head>"
        f"<body>\n{_body(parse(md))}\n</body></html>\n"
    )
    return page.encode("utf-8")
