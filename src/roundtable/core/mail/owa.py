"""学校邮箱（Outlook 网页版）只读读取：专用浏览器窗口 + 本机保存的登录状态。

- 登录：弹出一个单独的浏览器窗口（优先用电脑自带的浏览器程序，配一个独立的配置目录，
  与平时用的浏览器互不相干），打开微软官方登录页，由用户本人输入账号密码、完成多重验证。
  圆桌不代填、不读取输入框，也不保存密码；登录状态（cookie）由浏览器自己保存在
  data/mail_browser/（Windows 上浏览器用系统 DPAPI 加密，只有当前 Windows 账户能解开）。
- 读取：打开网页版收件箱，收集网页版自己加载的邮件数据；再借网页版这一次用的请求头
  （只放在内存里，用完即丢）补发一个只读的「列出收件箱」请求，正文同样用只读请求取纯文本。
- 只读守卫：窗口只放行登录和邮箱需要的网站；邮箱网站上一切写入类请求（发送、删除、移动、
  标已读、改设置……）在浏览器里直接拦掉。窗口因此「只能看、不能改」。
- 出错信息先去掉网址参数、脱敏、截断，再给界面看。

微软改版网页版时这里可能要跟着调；调之前邮件栏显示「暂时读不了」，不会影响邮箱本身。
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from roundtable.core.providers import redact

BASE_URL = "https://outlook.office.com"
MAIL_PATH = "/mail/"
LOGIN_TIMEOUT_S = 600  # 等用户登录（含多重验证）最多 10 分钟
LOAD_TIMEOUT_S = 45  # 已登录时等网页版加载收件箱
MAX_JSON_BYTES = 20_000_000
BODY_BATCH = 10
# 先试电脑自带的浏览器程序（Windows 一定有），没有再用随测试环境装的
CHANNELS: tuple[str | None, ...] = ("msedge", "chrome", None)

# 只放行这些网站（后缀匹配）：微软登录、多重验证、网页版本身和它的静态资源。
# 学校如果把登录转到自己的统一认证页，加在 MAIL_HOSTS_EXTRA 设置里（界面里可改）。
ALLOWED_HOST_SUFFIXES = (
    "microsoftonline.com",
    "microsoftonline-p.com",
    "microsoftazuread-sso.com",
    "windows.net",
    "live.com",
    "msauth.net",
    "msftauth.net",
    "msauthimages.net",
    "msftauthimages.net",
    "microsoft.com",
    "cloud.microsoft",
    "office.com",
    "office.net",
    "office365.com",
    "outlook.com",
    "msocdn.com",
    "static.microsoft",
    "manchester.ac.uk",
)
# 邮箱本身所在的网站：这些网站上的写入类请求一律拦掉
MAIL_HOST_SUFFIXES = (
    "outlook.office.com",
    "outlook.office365.com",
    "outlook.cloud.microsoft",
    "outlook.live.com",
    "substrate.office.com",
)
# 网页版的 service.svc 接口按 action 区分读写：只放行读类
READ_ACTION_PREFIXES = (
    "Get",
    "Find",
    "Sync",
    "Expand",
    "Resolve",
    "Search",
    "Subscribe",
    "Ping",
    "Log",
)
WRITE_METHODS = frozenset({"PUT", "PATCH", "DELETE"})
# 借用网页版请求里的这些头去补发只读请求（只在内存里，用完即丢；不含 cookie，cookie 由浏览器带）
AUTH_HEADERS = frozenset(
    {
        "authorization",
        "x-owa-canary",
        "x-anchormailbox",
        "x-owa-hosted-ux",
        "x-req-source",
        "x-owa-sessionid",
        "prefer",
    }
)


class MailError(RuntimeError):
    """读邮箱失败（文字已脱敏，可直接给界面看）。"""


class SessionExpired(MailError):
    """登录状态过期或还没登录：要用户重新登录。"""


class LoginCancelled(MailError):
    """用户关掉了登录窗口，或超时没登录。"""


class BrowserMissing(MailError):
    """电脑上找不到能用的浏览器程序。"""


# --- 出错信息 ------------------------------------------------------------------


def clean_error(text: str, limit: int = 200) -> str:
    """去掉网址里的参数（可能带登录码）、脱敏、截断。"""
    text = re.sub(r"(https?://[^\s?#\"']+)[?#][^\s\"']*", r"\1", str(text))
    text = redact(" ".join(text.split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- 只读守卫 ------------------------------------------------------------------


def _host_in(host: str, suffixes: tuple[str, ...] | list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in suffixes)


def owa_action(url: str, headers: dict[str, str]) -> str:
    """service.svc 请求的 action（在网址参数或请求头里）。"""
    query = parse_qs(urlsplit(url).query)
    found = query.get("action") or query.get("Action")
    if found:
        return found[0]
    lower = {k.lower(): v for k, v in headers.items()}
    return lower.get("action", "")


def block_reason(
    url: str,
    method: str,
    headers: dict[str, str] | None = None,
    body: str | None = None,
    extra_hosts: tuple[str, ...] | list[str] = (),
    *,
    mail_hosts: tuple[str, ...] = MAIL_HOST_SUFFIXES,
    plain_http: tuple[str, ...] = (),
) -> str | None:
    """这个请求要不要拦：返回拦截原因，放行返回 None。plain_http 只在测试里用（本机假邮箱）。"""
    parts = urlsplit(url)
    if parts.scheme in {"data", "blob", "about", "chrome-extension"}:
        return None
    host = parts.hostname or ""
    if parts.scheme not in {"https", "wss"} and not (parts.scheme == "http" and host in plain_http):
        return "只允许加密连接"
    if not _host_in(host, (*ALLOWED_HOST_SUFFIXES, *mail_hosts, *extra_hosts)):
        return f"不在允许的网站里：{host}"
    if not _host_in(host, mail_hosts):
        return None
    method = method.upper()
    if method in WRITE_METHODS:
        return f"邮箱写入请求（{method}）"
    path = parts.path.lower()
    if "service.svc" in path:
        action = owa_action(url, headers or {})
        if action and not action.startswith(READ_ACTION_PREFIXES):
            return f"邮箱写入操作（{action}）"
    if path.endswith("/graphql") and body and re.search(r"\bmutation\b", body, re.I):
        return "邮箱写入操作（mutation）"
    if re.search(r"/(sendmail|send|move|copy|delete)$", path) and method == "POST":
        return "邮箱写入操作"
    return None


# --- 解析网页版的数据 ------------------------------------------------------------------


@dataclass(frozen=True)
class MailItem:
    id: str
    received_at: str  # UTC ISO
    sender_name: str = ""
    sender_addr: str = ""
    subject: str = ""
    preview: str = ""
    is_read: bool = False
    flagged: bool = False
    high: bool = False
    attachments: bool = False


def _get(d: dict[str, Any], *names: str) -> Any:
    """大小写不敏感地取字段（网页版旧接口是 Subject，新接口可能是 subject）。"""
    lower = {k.lower(): v for k, v in d.items() if isinstance(k, str)}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return None


def _when(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _sender(d: dict[str, Any]) -> tuple[str, str]:
    who = _get(d, "From", "Sender")
    if not isinstance(who, dict):
        return "", ""
    box = _get(who, "Mailbox", "EmailAddress")
    box = box if isinstance(box, dict) else who
    name = _get(box, "Name") or ""
    addr = _get(box, "EmailAddress", "Address") or ""
    if not isinstance(addr, str) or "@" not in addr:  # 校内地址可能是 X500 形式，不显示
        addr = ""
    return str(name)[:200], addr.lower()[:200]


def _item_id(d: dict[str, Any]) -> str | None:
    raw = _get(d, "ItemId", "Id")
    if isinstance(raw, dict):
        raw = _get(raw, "Id")
    return raw if isinstance(raw, str) and raw else None


def _as_item(d: dict[str, Any]) -> MailItem | None:
    kind = str(_get(d, "__type", "@odata.type") or "")
    if kind and "message" not in kind.lower():  # 日历项、联系人等不要
        return None
    received = _when(_get(d, "DateTimeReceived", "ReceivedDateTime"))
    item_id = _item_id(d)
    if not received or not item_id or _get(d, "Subject") is None:
        return None
    if _get(d, "From", "Sender") is None:
        return None
    name, addr = _sender(d)
    flag = _get(d, "Flag")
    flag_status = _get(flag, "FlagStatus") if isinstance(flag, dict) else None
    return MailItem(
        id=item_id,
        received_at=received,
        sender_name=name,
        sender_addr=addr,
        subject=str(_get(d, "Subject") or "")[:500],
        preview=" ".join(str(_get(d, "Preview", "BodyPreview") or "").split())[:400],
        is_read=bool(_get(d, "IsRead")),
        flagged=str(flag_status or "").lower() == "flagged",
        high=str(_get(d, "Importance") or "").lower() == "high",
        attachments=bool(_get(d, "HasAttachments")),
    )


def extract_items(data: Any) -> list[MailItem]:
    """从任意 JSON 里找出像邮件的对象（按 id 去重，保持出现顺序）。"""
    found: dict[str, MailItem] = {}
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            item = _as_item(node)
            if item and item.id not in found:
                found[item.id] = item
            stack.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            stack.extend(reversed(node))
    return list(found.values())


class _Text(HTMLParser):
    BLOCK = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table", "blockquote"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"script", "style", "head"}:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"}:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return tidy_text("".join(parser.parts))


def tidy_text(text: str, limit: int = 50_000) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r", "").split("\n")]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out[:limit]


def extract_bodies(data: Any) -> dict[str, str]:
    """GetItem 的结果里取纯文本正文：{邮件 id: 正文}。"""
    out: dict[str, str] = {}
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            body = _get(node, "Body", "UniqueBody")
            item_id = _item_id(node)
            if isinstance(body, dict) and item_id:
                value = _get(body, "Value", "Content")
                if isinstance(value, str):
                    kind = str(_get(body, "BodyType", "ContentType") or "").lower()
                    out[item_id] = html_to_text(value) if kind == "html" else tidy_text(value)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def _prop(name: str) -> dict[str, str]:
    return {"__type": "PropertyUri:#Exchange", "FieldURI": name}


def _header() -> dict[str, Any]:
    return {
        "__type": "JsonRequestHeaders:#Exchange",
        "RequestServerVersion": "Exchange2013",
        "TimeZoneContext": {
            "__type": "TimeZoneContext:#Exchange",
            "TimeZoneDefinition": {"__type": "TimeZoneDefinitionType:#Exchange", "Id": "UTC"},
        },
    }


def find_items_request(limit: int) -> dict[str, Any]:
    """只读：收件箱最新的 limit 封（按收到时间倒序）。"""
    fields = ["Subject", "DateTimeReceived", "From", "IsRead", "Flag", "Importance"]
    fields += ["HasAttachments", "Preview"]
    return {
        "__type": "FindItemJsonRequest:#Exchange",
        "Header": _header(),
        "Body": {
            "__type": "FindItemRequest:#Exchange",
            "ItemShape": {
                "__type": "ItemResponseShape:#Exchange",
                "BaseShape": "IdOnly",
                "AdditionalProperties": [_prop(f) for f in fields],
            },
            "ParentFolderIds": [{"__type": "DistinguishedFolderId:#Exchange", "Id": "inbox"}],
            "Traversal": "Shallow",
            "Paging": {
                "__type": "IndexedPageView:#Exchange",
                "BasePoint": "Beginning",
                "Offset": 0,
                "MaxEntriesReturned": limit,
            },
            "SortOrder": [
                {
                    "__type": "SortResults:#Exchange",
                    "Order": "Descending",
                    "Path": _prop("DateTimeReceived"),
                }
            ],
        },
    }


def get_items_request(ids: list[str]) -> dict[str, Any]:
    """只读：取这几封的纯文本正文（GetItem 不会把邮件标成已读）。"""
    return {
        "__type": "GetItemJsonRequest:#Exchange",
        "Header": _header(),
        "Body": {
            "__type": "GetItemRequest:#Exchange",
            "ItemShape": {
                "__type": "ItemResponseShape:#Exchange",
                "BaseShape": "IdOnly",
                "BodyType": "Text",
                "AdditionalProperties": [_prop("Body")],
            },
            "ItemIds": [{"__type": "ItemId:#Exchange", "Id": i} for i in ids],
        },
    }


# --- 浏览器 ------------------------------------------------------------------


class MailSession(Protocol):
    blocked: list[str]

    def items(self, limit: int) -> list[MailItem]: ...

    def bodies(self, ids: list[str]) -> dict[str, str]: ...


class MailSource(Protocol):
    def open(self, *, interactive: bool) -> contextlib.AbstractContextManager[MailSession]: ...

    def has_login(self) -> bool: ...

    def clear(self) -> None: ...


@dataclass
class _Capture:
    """网页版加载时看到的数据与请求头（只在这一次读取期间存在于内存）。"""

    items: dict[str, MailItem] = field(default_factory=dict)
    auth: dict[str, str] = field(default_factory=dict)
    blocked: list[str] = field(default_factory=list)


class _OwaSession:
    def __init__(self, context: Any, base_url: str, capture: _Capture) -> None:
        self.ctx = context
        self.base = base_url.rstrip("/")
        self.capture = capture

    @property
    def blocked(self) -> list[str]:
        return self.capture.blocked

    def _headers(self, action: str) -> dict[str, str]:
        headers = dict(self.capture.auth)
        if "x-owa-canary" not in headers:
            for c in self.ctx.cookies(self.base):
                if c["name"].lower() == "x-owa-canary":
                    headers["x-owa-canary"] = c["value"]
        headers.update(
            {
                "action": action,
                "content-type": "application/json; charset=utf-8",
                "x-requested-with": "XMLHttpRequest",
            }
        )
        return headers

    def _call(self, action: str, payload: dict[str, Any]) -> Any:
        resp = self.ctx.request.post(
            f"{self.base}/owa/service.svc?action={action}&app=Mail",
            headers=self._headers(action),
            data=json.dumps(payload),
            timeout=30_000,
        )
        if resp.status in {401, 440}:
            raise SessionExpired("登录状态过期了，需要重新登录")
        if not resp.ok:
            raise MailError(f"邮箱返回错误（{resp.status}）")
        return resp.json()

    def items(self, limit: int) -> list[MailItem]:
        """优先用补发的只读列表请求；不行就用网页版自己加载到的那些。"""
        try:
            got = extract_items(self._call("FindItem", find_items_request(limit)))
        except SessionExpired:
            raise
        except Exception:  # noqa: BLE001 - 网页版接口改了就退回被动收集的数据
            got = []
        if not got:
            got = list(self.capture.items.values())
        got.sort(key=lambda m: m.received_at, reverse=True)
        return got[:limit]

    def bodies(self, ids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for start in range(0, len(ids), BODY_BATCH):
            chunk = ids[start : start + BODY_BATCH]
            out.update(extract_bodies(self._call("GetItem", get_items_request(chunk))))
        return out


class OwaBrowser:
    """用 Playwright 驱动专用浏览器。profile_dir 是登录状态所在目录（在 data/ 下，不进 git）。"""

    def __init__(
        self,
        profile_dir: Path,
        *,
        base_url: str = BASE_URL,
        extra_hosts: Callable[[], list[str]] = list,
        show_window: Callable[[], bool] = lambda: False,
        channels: tuple[str | None, ...] = CHANNELS,
        login_timeout_s: float = LOGIN_TIMEOUT_S,
        load_timeout_s: float = LOAD_TIMEOUT_S,
        expire_after_s: float = 12,
        mail_hosts: tuple[str, ...] = MAIL_HOST_SUFFIXES,
        plain_http: tuple[str, ...] = (),
        executable_path: str | None = None,
    ) -> None:
        """后四项只在测试里改（本机假邮箱用 http、主机名是 127.0.0.1；指定 Chromium 位置）。"""
        self.profile_dir = Path(profile_dir)
        self.base = base_url.rstrip("/")
        self.extra_hosts = extra_hosts
        self.show_window = show_window
        self.channels = channels
        self.login_timeout_s = login_timeout_s
        self.load_timeout_s = load_timeout_s
        self.expire_after_s = expire_after_s
        self.mail_hosts = mail_hosts
        self.plain_http = plain_http
        self.executable_path = executable_path

    def has_login(self) -> bool:
        return self.profile_dir.is_dir() and any(self.profile_dir.iterdir())

    def clear(self) -> None:
        """退出登录：删掉专用浏览器的整个配置目录（cookie、缓存都在里面）。"""
        if self.profile_dir.exists():
            shutil.rmtree(self.profile_dir, ignore_errors=True)

    def _launch(self, pw: Any, headless: bool) -> Any:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        for channel in self.channels:
            try:
                return pw.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    channel=channel,
                    executable_path=None if channel else self.executable_path,
                    headless=headless,
                    no_viewport=not headless,
                    accept_downloads=False,
                )
            except Exception as exc:  # noqa: BLE001 - 这个浏览器没有就试下一个
                errors.append(clean_error(str(exc).splitlines()[0] if str(exc) else exc, 80))
        raise BrowserMissing(
            "找不到能用的浏览器程序。可以在项目目录运行 python -m playwright install chromium"
            "（约 150 MB）后再试。" + (f"（{errors[-1]}）" if errors else "")
        )

    def _guard(self, context: Any, capture: _Capture) -> None:
        extra = tuple(self.extra_hosts())

        def route(r: Any) -> None:
            req = r.request
            try:
                body = req.post_data
            except Exception:  # noqa: BLE001 - 二进制请求体读不出文字，按无正文判断
                body = None
            reason = block_reason(
                req.url,
                req.method,
                req.headers,
                body,
                extra,
                mail_hosts=self.mail_hosts,
                plain_http=self.plain_http,
            )
            if reason:
                if len(capture.blocked) < 20:
                    capture.blocked.append(reason)
                r.abort("blockedbyclient")
            else:
                r.continue_()

        def on_request(req: Any) -> None:
            if "service.svc" not in req.url:
                return
            if not _host_in(urlsplit(req.url).hostname or "", self.mail_hosts):
                return
            try:
                headers = req.all_headers()
            except Exception:  # noqa: BLE001
                return
            kept = {k.lower(): v for k, v in headers.items() if k.lower() in AUTH_HEADERS}
            if kept:
                capture.auth.update(kept)

        def on_response(resp: Any) -> None:
            url = resp.url
            if not _host_in(urlsplit(url).hostname or "", self.mail_hosts):
                return
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            try:
                raw = resp.body()
                if len(raw) > MAX_JSON_BYTES:
                    return
                for item in extract_items(json.loads(raw)):
                    capture.items.setdefault(item.id, item)
            except Exception:  # noqa: BLE001 - 不是邮件数据就跳过
                return

        context.route("**/*", route)
        context.on("request", on_request)
        context.on("response", on_response)

    def _on_mail(self, page: Any) -> bool:
        parts = urlsplit(page.url)
        return _host_in(parts.hostname or "", self.mail_hosts) and parts.path.startswith("/mail")

    def _wait(self, page: Any, capture: _Capture, seconds: float, interactive: bool) -> None:
        """等到在邮箱页面上、并且看到了邮件数据或请求头。"""
        deadline = time.monotonic() + seconds
        on_since = off_since = None
        while time.monotonic() < deadline:
            if page.is_closed():
                raise LoginCancelled("登录窗口被关掉了，没有登录")
            now = time.monotonic()
            if self._on_mail(page):
                off_since, on_since = None, on_since or now
                if capture.items or capture.auth:
                    return
                if now - on_since > 15:  # 空收件箱：页面停了 15 秒也算加载完
                    return
            else:
                on_since, off_since = None, off_since or now
                if not interactive and now - off_since > self.expire_after_s:
                    # 不弹窗刷新时一直停在登录页：登录状态过期了
                    raise SessionExpired("登录状态过期了，需要重新登录")
            page.wait_for_timeout(500)
        if interactive:
            raise LoginCancelled("等了 10 分钟还没登录完，先停下了")
        raise SessionExpired("打不开收件箱，可能需要重新登录")

    @contextlib.contextmanager
    def open(self, *, interactive: bool) -> Iterator[MailSession]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserMissing("没有安装 playwright：运行 pip install playwright") from None
        capture = _Capture()
        with sync_playwright() as pw:
            context = self._launch(pw, headless=not (interactive or self.show_window()))
            try:
                self._guard(context, capture)
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(self.base + MAIL_PATH, wait_until="commit", timeout=60_000)
                    timeout = self.login_timeout_s if interactive else self.load_timeout_s
                    self._wait(page, capture, timeout, interactive)
                except MailError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 浏览器层面的错误统一脱敏
                    if interactive and page.is_closed():
                        raise LoginCancelled("登录窗口被关掉了，没有登录") from None
                    raise MailError(f"打开邮箱失败：{clean_error(exc)}") from None
                origin = urlsplit(page.url)
                yield _OwaSession(context, f"{origin.scheme}://{origin.netloc}", capture)
            finally:
                with contextlib.suppress(Exception):
                    context.close()
