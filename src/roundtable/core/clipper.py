"""办公室的资料浏览器：搜索、打开网页、提取正文、把全文或选段存成资料（docs/CLIPPER.md）。

- 打开网页由代码直接抓取（不调用模型、不花钱）：只读 GET，不带 cookie / key；只接受 http / https，
  拒绝内网、本机和保留地址（每一跳重定向都检查），防止局域网模式下被人拿来探测家里的设备。
- 只把**提取后的正文**交给界面和成员：去掉脚本、样式、导航、页脚、表单，优先取 ``<article>`` /
  ``<main>``，按段落返回；省 token。
- 搜索走现有的联网搜索服务（有花费，计入总预算，记为 ``step="clip_search"``）。
- 存下来的资料：正文按内容哈希存进上传目录（``FileStore``），清单在 ``app_settings``
  （``clipper.clips``）；提问时「附上」= 用这份正文新建一个 Markdown 附件（附件只能用一次）。
  所以资料和普通附件一样经过 ``attachments`` 提示词、放在 ``<attachment>`` 标签里
  （标签内指令无效）。
"""

from __future__ import annotations

import asyncio
import html as _html
import ipaddress
import json
import re
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

CLIPS_KEY = "clipper.clips"
SEARCH_ROOM = "clipper"  # 搜索调用挂在这个隐藏聊天会话上
SEARCH_STEP = "clip_search"
USER_AGENT = "Mozilla/5.0 (compatible; Roundtable/1.0; personal reader; read-only)"
MAX_REDIRECTS = 5

_SKIP = frozenset(
    [
        "script",
        "style",
        "noscript",
        "svg",
        "template",
        "iframe",
        "object",
        "embed",
        "canvas",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
        "button",
        "select",
        "textarea",
        "dialog",
        "menu",
    ]
)
_BLOCK = frozenset(
    [
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "pre",
        "blockquote",
        "td",
        "th",
        "dt",
        "dd",
        "figcaption",
    ]
)
_BREAK = frozenset(
    ["div", "section", "article", "main", "br", "tr", "table", "ul", "ol", "dl", "figure"]
)
_VOID = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
_SPACE = re.compile(r"[ \t\r\f\v\u00a0\u3000]+")
_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", re.I)
_TEXTY = ("text/html", "application/xhtml+xml", "text/plain")


class ClipError(ValueError):
    """打不开 / 存不了；message 可以直接给用户看。"""


# --- 网址安全 ------------------------------------------------------------------


# 代理软件的 fake-ip 模式（如 TUN 模式）把所有域名解析到这一段；它不是家里的设备，放行
_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip in _FAKE_IP:
        return False
    return not ip.is_global or ip.is_multicast


Resolve = Callable[[str], Awaitable[list[str]]]


async def resolve_host(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def normalize_url(raw: str) -> str:
    """地址栏输入 → 完整网址（省略协议时补 https://）。"""
    url = (raw or "").strip()
    if not url:
        raise ClipError("请输入网址")
    if "://" not in url:
        if re.match(r"^[a-zA-Z][\w+.-]*:(?!\d)", url):  # javascript:、data:、mailto: 之类
            raise ClipError("只能打开 http / https 网址")
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ClipError("只能打开 http / https 网址")
    if parts.username or parts.password:
        raise ClipError("网址里不能带账号密码")
    if len(url) > 2000:
        raise ClipError("网址太长")
    return url


async def check_public(url: str, resolve: Resolve = resolve_host) -> None:
    host = urlsplit(url).hostname or ""
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
            raise ClipError("不能打开本机或内网地址") from None
        try:
            addrs = [ipaddress.ip_address(a.split("%", 1)[0]) for a in await resolve(host)]
        except (OSError, ValueError):
            raise ClipError("找不到这个网站（域名解析失败）") from None
    if not addrs or any(_blocked(a) for a in addrs):
        raise ClipError("不能打开本机或内网地址")


# --- 抓取 ----------------------------------------------------------------------


@dataclass(frozen=True)
class Fetched:
    url: str  # 重定向之后的最终地址
    content_type: str
    data: bytes


Fetch = Callable[[str, float, int], Awaitable[Fetched]]


async def http_fetch(
    url: str,
    timeout_s: float,
    max_bytes: int,
    *,
    resolve: Resolve = resolve_host,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Fetched:
    """只读 GET：手动跟随重定向，每一跳都检查是否公网地址；超过 max_bytes 放弃。"""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(
        timeout=timeout_s, follow_redirects=False, headers=headers, transport=transport
    ) as c:
        for _ in range(MAX_REDIRECTS + 1):
            await check_public(url, resolve)
            try:
                async with c.stream("GET", url) as resp:
                    if resp.is_redirect and resp.headers.get("location"):
                        url = normalize_url(urljoin(url, resp.headers["location"]))
                        continue
                    if resp.status_code >= 400:
                        code = resp.status_code
                        raise ClipError(f"网站返回错误 {code}（可能需要登录或拒绝访问）")
                    kind = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if kind and not kind.startswith(_TEXTY):
                        raise ClipError("这不是网页（可能是图片、视频或下载文件）")
                    chunks, size = [], 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ClipError("网页太大，打不开")
                        chunks.append(chunk)
                    raw_type = resp.headers.get("content-type", "")
                    return Fetched(str(resp.url), raw_type, b"".join(chunks))
            except httpx.TimeoutException:
                raise ClipError("网站响应太慢，超时了") from None
            except httpx.HTTPError:
                raise ClipError("连不上这个网站") from None
    raise ClipError("重定向次数太多")


def decode(data: bytes, content_type: str) -> str:
    charset = None
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    else:
        meta = _CHARSET.search(data[:4096])
        if meta:
            charset = meta.group(1).decode("ascii", "ignore")
    charset = (charset or "utf-8").lower()
    if charset in ("gb2312", "gbk", "gb_2312-80"):
        charset = "gb18030"
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


# --- 正文提取 ------------------------------------------------------------------


@dataclass
class Page:
    url: str
    title: str
    site: str
    blocks: list[str] = field(default_factory=list)  # 段落；标题以「## 」开头
    links: list[dict[str, str]] = field(default_factory=list)  # 正文里的链接（点了可以接着打开）

    @property
    def chars(self) -> int:
        return sum(len(b) for b in self.blocks)

    def public(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "site": self.site,
            "blocks": self.blocks,
            "links": self.links,
            "chars": self.chars,
        }


class _Extractor(HTMLParser):
    """把 HTML 切成段落：跳过脚本 / 导航等，记下每段属于第几个 article / main。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og_title = ""
        self.blocks: list[tuple[str, int | None, bool]] = []  # (文字, 所属容器, 是否标题)
        self.links: list[tuple[str, str, int | None]] = []
        self._stack: list[str] = []
        self._skip = 0
        self._in_title = False
        self._buf: list[str] = []
        self._heading = False
        self._containers: list[int] = []  # 当前所在的 article / main 编号
        self._n_containers = 0
        self._href: str | None = None
        self._link_buf: list[str] = []

    def _flush(self) -> None:
        text = _SPACE.sub(" ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            owner = self._containers[-1] if self._containers else None
            self.blocks.append((text, owner, self._heading))
        self._heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "meta" and (a.get("property") or a.get("name")) in ("og:title", "twitter:title"):
            self.og_title = self.og_title or (a.get("content") or "").strip()
        if tag in _VOID:
            if tag == "br" and not self._skip:
                self._buf.append("\n")
            return
        self._stack.append(tag)
        hidden = "hidden" in a or a.get("aria-hidden") == "true"
        role = a.get("role") or ""
        if self._skip or tag in _SKIP or hidden or role in ("navigation", "banner", "contentinfo"):
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag in ("article", "main"):
            self._flush()
            self._n_containers += 1
            self._containers.append(self._n_containers)
        elif tag in _BLOCK or tag in _BREAK:
            self._flush()
            self._heading = tag in ("h1", "h2", "h3", "h4", "h5", "h6")
        elif tag == "a" and a.get("href"):
            self._href = a["href"]
            self._link_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID or tag not in self._stack:
            return
        while self._stack:
            open_tag = self._stack.pop()
            self._close(open_tag)
            if open_tag == tag:
                break

    def _close(self, tag: str) -> None:
        if self._skip:
            self._skip -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag in ("article", "main"):
            self._flush()
            if self._containers:
                self._containers.pop()
        elif tag in _BLOCK or tag in _BREAK:
            self._flush()
        elif tag == "a" and self._href is not None:
            text = _SPACE.sub(" ", "".join(self._link_buf)).strip()
            owner = self._containers[-1] if self._containers else None
            if text:
                self.links.append((text, self._href, owner))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self._buf.append(data)
        if self._href is not None:
            self._link_buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _site(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def extract_page(html: str, url: str, *, max_chars: int = 60000, max_links: int = 60) -> Page:
    """HTML → 标题 + 正文段落 + 正文链接。有 article / main 时只取字最多的那一个。"""
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # 残缺的 HTML：能提取多少算多少
        parser._flush()
    totals: dict[int | None, int] = {}
    for text, owner, _ in parser.blocks:
        totals[owner] = totals.get(owner, 0) + len(text)
    best = max((k for k in totals if k is not None), key=lambda k: totals[k], default=None)
    # 主体容器太短（比如只包了一句话）时退回整页
    if best is not None and totals[best] < 200 and totals.get(None, 0) > totals[best] * 3:
        best = None
    blocks: list[str] = []
    seen: set[str] = set()
    used = 0
    for text, owner, heading in parser.blocks:
        if best is not None and owner != best:
            continue
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        text = "\n".join(lines)
        if not text or (len(text) < 3 and not heading) or text in seen:
            continue
        seen.add(text)
        if used + len(text) > max_chars:
            blocks.append("（后面还有内容，太长没有显示）")
            break
        used += len(text)
        blocks.append(f"## {text}" if heading else text)
    links: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for text, href, owner in parser.links:
        if best is not None and owner != best:
            continue
        full = urljoin(url, href).split("#", 1)[0]
        if not full.startswith(("http://", "https://")) or full in seen_urls or full == url:
            continue
        seen_urls.add(full)
        links.append({"text": text[:80], "url": full[:1000]})
        if len(links) >= max_links:
            break
    title = _SPACE.sub(" ", parser.og_title or parser.title).strip()[:200]
    return Page(url=url, title=title or _site(url), site=_site(url), blocks=blocks, links=links)


def plain_page(text: str, url: str, *, max_chars: int = 60000) -> Page:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, used = [], 0
    for p in paras:
        if used + len(p) > max_chars:
            out.append("（后面还有内容，太长没有显示）")
            break
        used += len(p)
        out.append(p)
    return Page(url=url, title=_site(url), site=_site(url), blocks=out)


def parse_fetched(fetched: Fetched, *, max_chars: int) -> Page:
    text = decode(fetched.data, fetched.content_type)
    if fetched.content_type.lower().startswith("text/plain"):
        return plain_page(text, fetched.url, max_chars=max_chars)
    return extract_page(text, fetched.url, max_chars=max_chars)


# --- 存成资料 ------------------------------------------------------------------


def clip_markdown(title: str, url: str, text: str, saved_at: str, note: str = "") -> str:
    """资料正文：标题、来源、保存时间，然后是正文（原样，不做模型处理）。"""
    head = [f"# {title}", "", f"来源：{url}", f"保存于：{saved_at}"]
    if note:
        head.append(f"备注：{note}")
    return "\n".join([*head, "", text.strip(), ""])


def clips_of(repo: Any) -> list[dict[str, Any]]:
    raw = repo.setting(CLIPS_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [c for c in data if isinstance(c, dict) and c.get("id") and c.get("key")]


def save_clips(repo: Any, clips: list[dict[str, Any]]) -> None:
    repo.set_setting(CLIPS_KEY, json.dumps(clips, ensure_ascii=False))


def new_clip_id() -> str:
    return uuid.uuid4().hex[:16]


def public_clip(clip: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "title", "url", "site", "chars", "excerpt", "partial", "saved_at")
    return {k: clip.get(k) for k in keys}


def safe_name(title: str) -> str:
    """附件显示名：去掉路径和控制字符，限长，扩展名 .md。"""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title).strip()[:60] or "网页资料"
    return f"{name}.md"


_TAG = re.compile(r"<[^>]+>")


def strip_tags(text: str | None) -> str:
    return _SPACE.sub(" ", _html.unescape(_TAG.sub(" ", text or ""))).strip()


def site_of(url: str) -> str:
    return _site(url)
