"""邮件的图文版正文：把网页版给的 HTML 洗干净后才存、才显示。

- 只留排版用的标签和属性（白名单）；脚本、表单、内嵌框架、事件属性（on*）、javascript: 链接全去掉。
- 链接只留 http / https / mailto，并且一律在新窗口打开。
- 图片：邮件里自带的（cid:）换成取回来的 data: 图片；外部图片（https:）保留地址，
  界面默认拦着，你点「加载外部图片」才放行（加载外部图片可能让发件人知道你打开了这封信）。
- 样式里的 @import、expression()、外部 url() 去掉（只留 data: 图片）。

界面再用无脚本的沙箱框（sandbox、CSP）显示，这里是第一道。
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from typing import Any

MAX_HTML = 6_000_000  # 洗完后超过这么大（多半是图片太多）就不存图文版


def _set(words: str) -> frozenset[str]:
    return frozenset(words.split())


_TAGS = _set(
    "a abbr address b bdi bdo big blockquote br caption center cite code col colgroup dd del div"
    " dl dt em font h1 h2 h3 h4 h5 h6 hr i img ins kbd li mark ol p pre q s small span strike"
    " strong sub sup table tbody td tfoot th thead tr tt u ul wbr"
)
_VOID = frozenset({"br", "col", "hr", "img", "wbr"})
# 连同里面的内容一起丢掉
_DROP = _set(
    "script noscript iframe frame frameset object embed applet form input button select textarea"
    " option template svg math title meta link base audio video source track canvas"
)
_NO_END = frozenset({"meta", "link", "base", "input", "source", "track", "embed"})
_ATTRS = _set(
    "align alt bgcolor border cellpadding cellspacing class color colspan dir face height href"
    " lang rowspan size span src start style title type valign width"
)
_SAFE_LINK = re.compile(r"^(https?:|mailto:)", re.I)
_DATA_IMG = re.compile(r"^data:image/(png|gif|jpe?g|webp|bmp);base64,[a-z0-9+/=\s]+$", re.I)
_CSS_BAD = re.compile(r"@import[^;]*;?|expression\s*\(|behavior\s*:|-moz-binding|javascript:", re.I)
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)


def _clean_css(css: str) -> str:
    def url(m: re.Match[str]) -> str:
        return m.group(0) if _DATA_IMG.match(m.group(2).strip()) else "none"

    return _CSS_BAD.sub("", _CSS_URL.sub(url, css))


class _Cleaner(HTMLParser):
    def __init__(self, inline: dict[str, str]) -> None:
        super().__init__(convert_charrefs=True)
        self.inline = {k.lower(): v for k, v in inline.items()}
        self.out: list[str] = []
        self.drop = 0  # 正在丢弃的标签层数
        self.in_style = False
        self.styles: list[str] = []
        self.remote = 0
        self.embedded = 0

    def _img(self, src: str) -> str | None:
        src = src.strip()
        if src.lower().startswith("cid:"):
            got = self.inline.get(src[4:].strip("<>").lower())
            if got:
                self.embedded += 1
            return got
        if _DATA_IMG.match(src):
            self.embedded += 1
            return src
        if src.lower().startswith(("https:", "http:")):
            self.remote += 1
            return "https:" + src.split(":", 1)[1] if src.lower().startswith("http:") else src
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP or self.drop:
            if tag in _DROP and tag not in _NO_END:
                self.drop += 1
            return
        if tag == "style":
            self.in_style = True
            return
        if tag not in _TAGS:
            return
        kept: list[str] = []
        for name, value in attrs:
            name = name.lower()
            if name not in _ATTRS or value is None:
                continue
            if name == "href":
                value = value.strip()
                if tag != "a" or not _SAFE_LINK.match(value):
                    continue
            elif name == "src":
                if tag != "img":
                    continue
                value = self._img(value)
                if value is None:
                    continue
            elif name == "style":
                value = _clean_css(value)
            kept.append(f' {name}="{escape(value, quote=True)}"')
        if tag == "a":
            kept.append(' target="_blank" rel="noopener noreferrer"')
        self.out.append(f"<{tag}{''.join(kept)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP or tag == "style":  # <script/> 之类：没有内容，直接跳过
            return
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and tag in _TAGS and not self.drop:
            self.out.append(f"</{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if self.drop:
            if tag in _DROP and tag not in _NO_END:
                self.drop -= 1
            return
        if tag == "style":
            self.in_style = False
            return
        if tag in _TAGS and tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.drop:
            return
        if self.in_style:
            self.styles.append(data)
        else:
            self.out.append(escape(data, quote=False))


def sanitize(html: str, inline: dict[str, str] | None = None) -> dict[str, Any]:
    """洗干净的 HTML 与图片计数：{html, remote, embedded}。inline 是 {cid: data: 地址}。"""
    cleaner = _Cleaner(inline or {})
    cleaner.feed(html)
    cleaner.close()
    css = _clean_css("\n".join(cleaner.styles)).replace("</", "<\\/")
    body = "".join(cleaner.out)
    if css.strip():
        body = f"<style>{css}</style>{body}"
    return {"html": body, "remote": cleaner.remote, "embedded": cleaner.embedded}
