"""终端邮件栏：开关、登录 / 刷新 / 读正文的后台任务、规则、提醒、审计。

- 默认关；打开后用户点「登录学校邮箱」，弹出专用窗口由用户本人登录（owa.py）。
- 只读：不发、不删、不移动、不标已读。邮件缓存只存本机数据库，只保留最近 N 天（默认 14）。
- 「重要」、分类和日期都按本机规则算（rules.py），不调用模型、不花钱；你可以逐封标重要、改分类
  （只记在本机，不写回邮箱）。成员看不到这些邮件。AI 总结 / 翻译在 ai.py，点了才调用。
- 图文版正文（含邮件自带的图片）点「显示图片」才去取，洗干净后才存（html.py）。
- 同一时间只跑一件事（登录 / 刷新 / 读正文 / 取图文版），在后台线程里跑，界面轮询状态。
- 自动刷新由网页端按间隔发起；这里限制最短间隔，防止频繁打开浏览器。
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from roundtable.core.mail.html import MAX_HTML, sanitize
from roundtable.core.mail.owa import (
    LoginCancelled,
    MailError,
    MailItem,
    MailSource,
    SessionExpired,
    clean_error,
)
from roundtable.core.mail.rules import CATEGORIES, MailRules, classify, extract_dates, judge
from roundtable.core.storage import Repository

ENABLED_KEY = "mail.enabled"
RULES_KEY = "mail.rules"
DAYS_KEY = "mail.days"
WINDOW_KEY = "mail.show_window"
HOSTS_KEY = "mail.extra_hosts"
STATE_KEY = "mail.state"
DIGEST_KEY = "mail.digest"  # AI 汇总的结果（ai.py 写）

DEFAULT_DAYS = 14
MAX_DAYS = 60
FETCH_LIMIT = 100  # 每次最多列出收件箱最新的这么多封
BODY_PREFETCH = 30  # 每次刷新最多顺手取这么多封的正文
AUTO_MIN_S = 10 * 60  # 自动刷新的最短间隔
MANUAL_MIN_S = 20  # 手动刷新的最短间隔
MAX_HOSTS = 10
STATES = ("never", "connected", "expired", "error")
JOBS = {
    "login": "等你在弹出的窗口里登录",
    "refresh": "刷新收件箱",
    "body": "读取正文",
    "images": "读取图文版正文",
}
MARKS = ("important", "normal")


class MailBusy(RuntimeError):
    """已经有一件事在跑。"""


class MailBox:
    def __init__(
        self,
        repo: Repository,
        source: MailSource | None = None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self.repo = repo
        self.source = source
        self.now = now
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self._lock = threading.Lock()
        self.busy: str | None = None
        self.blocked: list[str] = []

    # --- 设置 --------------------------------------------------------------------

    def _json_setting(self, key: str, default: Any) -> Any:
        raw = self.repo.setting(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except ValueError:
            return default

    def enabled(self) -> bool:
        return self.repo.setting(ENABLED_KEY) == "1"

    def rules(self) -> MailRules:
        try:
            return MailRules.model_validate(self._json_setting(RULES_KEY, {}))
        except ValidationError:
            return MailRules()

    def days(self) -> int:
        value = self._json_setting(DAYS_KEY, DEFAULT_DAYS)
        return value if isinstance(value, int) and 1 <= value <= MAX_DAYS else DEFAULT_DAYS

    def show_window(self) -> bool:
        return self.repo.setting(WINDOW_KEY) == "1"

    def extra_hosts(self) -> list[str]:
        value = self._json_setting(HOSTS_KEY, [])
        return [str(h) for h in value] if isinstance(value, list) else []

    def state(self) -> dict[str, Any]:
        value = self._json_setting(STATE_KEY, {})
        value = value if isinstance(value, dict) else {}
        status = value.get("status")
        return {
            "status": status if status in STATES else "never",
            "message": str(value.get("message") or ""),
            "last_ok": value.get("last_ok"),
            "last_try": value.get("last_try"),
        }

    def _set_state(self, **changes: Any) -> None:
        state = {**self.state(), **changes}
        self.repo.set_setting(STATE_KEY, json.dumps(state, ensure_ascii=False))

    def set_settings(
        self,
        *,
        enabled: bool | None = None,
        rules: dict[str, Any] | None = None,
        days: int | None = None,
        show_window: bool | None = None,
        extra_hosts: list[str] | None = None,
    ) -> None:
        """改设置。参数不合法时抛 ValueError（文字可直接给用户看）。"""
        if rules is not None:
            try:
                parsed = MailRules.model_validate(rules)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(x) for x in first["loc"])
                raise ValueError(f"规则有误（{where}）：{first['msg']}") from None
        if days is not None and not 1 <= days <= MAX_DAYS:
            raise ValueError(f"保留天数要在 1～{MAX_DAYS} 之间")
        if extra_hosts is not None:
            hosts = [h.strip().lower().lstrip("*.") for h in extra_hosts if h.strip()]
            if len(hosts) > MAX_HOSTS or any(
                " " in h or "/" in h or "." not in h or len(h) > 100 for h in hosts
            ):
                raise ValueError(f"额外网站写域名即可（如 sso.example.ac.uk），最多 {MAX_HOSTS} 个")
        if enabled is not None:
            self.repo.set_setting(ENABLED_KEY, "1" if enabled else "0")
        if rules is not None:
            self.repo.set_setting(RULES_KEY, parsed.model_dump_json())
        if days is not None:
            self.repo.set_setting(DAYS_KEY, json.dumps(days))
        if show_window is not None:
            self.repo.set_setting(WINDOW_KEY, "1" if show_window else "0")
        if extra_hosts is not None:
            self.repo.set_setting(HOSTS_KEY, json.dumps(hosts))

    # --- 展示 --------------------------------------------------------------------

    def _cutoff(self) -> str:
        return (
            (self.now() - timedelta(days=self.days())).astimezone(UTC).isoformat(timespec="seconds")
        )

    def view(self) -> dict[str, Any]:
        state = self.state()
        enabled = self.enabled()
        rules = self.rules()
        messages: list[dict[str, Any]] = []
        reminders: list[dict[str, Any]] = []
        if enabled:
            for m in self.repo.mail_messages(self._cutoff()):
                messages.append(self._decorate(m, rules))
            today = self.now().date().isoformat()
            reminders = [r for r in self.repo.mail_reminders() if r["due"][:10] >= today]
        unread = [m for m in messages if not m["is_read"]]
        by_cat = {k: 0 for k in CATEGORIES}
        for m in messages:
            by_cat[m["category"]] += 1
        if not enabled:
            status = "off"
        elif self.busy:
            status = "busy"
        else:
            status = state["status"]
        return {
            "available": self.source is not None,
            "enabled": enabled,
            "status": status,
            "busy": self.busy,
            "busy_text": JOBS.get(self.busy or "", ""),
            "message": state["message"],
            "last_ok": state["last_ok"],
            "last_try": state["last_try"],
            "days": self.days(),
            "show_window": self.show_window(),
            "extra_hosts": self.extra_hosts(),
            "rules": rules.model_dump(),
            "counts": {
                "total": len(messages),
                "unread": len(unread),
                "important": sum(1 for m in unread if m["important"]),
            },
            "categories": [
                {"key": k, "label": label, "count": by_cat[k]} for k, label in CATEGORIES.items()
            ],
            "digest": self._digest(messages, reminders),
            "messages": messages,
            "reminders": reminders,
            "blocked": list(self.blocked),
            "auto_min_s": AUTO_MIN_S,
        }

    @staticmethod
    def _decorate(m: dict[str, Any], rules: MailRules) -> dict[str, Any]:
        reasons = judge(m, rules)
        return {
            **m,
            "important": bool(reasons),
            "reasons": reasons,
            "category": classify(m, rules),
            "category_set": m.get("category") in CATEGORIES,  # 你手动改过分类
        }

    def _digest(
        self, messages: list[dict[str, Any]], reminders: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """本机速览（不花钱）：今天收到几封、重要未读几封、最近的截止日期。"""
        today = self.now().astimezone(UTC).date().isoformat()
        important = [m for m in messages if m["important"] and not m["is_read"]]
        soon = sorted(reminders, key=lambda r: r["due"])[:3]
        return {
            "today": sum(1 for m in messages if m["received_at"][:10] == today),
            "important_unread": len(important),
            "top": [
                {"id": m["id"], "subject": m["subject"], "sender": m["sender_name"]}
                for m in important[:3]
            ],
            "due": [
                {"due": r["due"], "snippet": r["snippet"], "message_id": r["message_id"]}
                for r in soon
            ],
        }

    def message(self, message_id: str) -> dict[str, Any] | None:
        """一封信的详情；还没有正文时在后台去取（loading=True，界面稍后再问）。"""
        m = self.repo.mail_message(message_id)
        if m is None or not self.enabled():
            return None
        loading = False
        if m.get("body") is None and self.state()["status"] == "connected" and not self.busy:
            try:
                self._start("body", lambda: self._fetch_bodies([message_id]))
                loading = True
            except MailBusy:
                loading = False
        reminders = [
            r
            for r in self.repo.mail_reminders(("pending", "accepted", "dismissed"))
            if r["message_id"] == message_id
        ]
        html = None
        if m.get("html"):
            with contextlib.suppress(ValueError):
                html = json.loads(m["html"])
        return {
            **self._decorate(m, self.rules()),
            "html": html,
            "loading": loading or self.busy == "body",
            "loading_html": self.busy == "images",
            "reminders": reminders,
        }

    # --- 动作 --------------------------------------------------------------------

    def _need(self) -> MailSource:
        if self.source is None:
            raise ValueError("这里用不了邮件栏（没有本机数据目录）")
        if not self.enabled():
            raise ValueError("邮件栏还没打开：先在设置里打开")
        return self.source

    def _start(self, kind: str, job: Callable[[], None]) -> None:
        with self._lock:
            if self.busy:
                raise MailBusy(f"正在{JOBS[self.busy]}，请稍等")
            self.busy = kind

        def run() -> None:
            try:
                job()
            finally:
                self.busy = None

        self.spawn(run)

    def login(self) -> None:
        self._need()
        self._start("login", lambda: self._fetch(interactive=True))

    def refresh(self, *, manual: bool) -> bool:
        """开始刷新；离上次太近时不刷，返回 False。"""
        self._need()
        state = self.state()
        if state["status"] not in {"connected", "error"}:
            if manual:
                raise ValueError("还没登录，或登录过期了：先点「登录学校邮箱」")
            return False
        last = state["last_try"]
        gap = MANUAL_MIN_S if manual else AUTO_MIN_S
        if last and (self.now() - datetime.fromisoformat(last)).total_seconds() < gap:
            return False
        self._start("refresh", lambda: self._fetch(interactive=False))
        return True

    def logout(self) -> None:
        """退出登录：删掉专用浏览器的登录状态，清空邮件缓存和提醒。"""
        if self.busy:
            raise MailBusy(f"正在{JOBS[self.busy]}，等它结束再退出")
        if self.source is not None:
            self.source.clear()
        removed = self.repo.clear_mail()
        self.repo.set_setting(DIGEST_KEY, "{}")
        self.repo.set_setting(STATE_KEY, json.dumps({"status": "never"}))
        self.blocked = []
        self.repo.add_mail_audit("logout", True, f"清除登录状态，删掉缓存 {removed} 封")

    def clear_cache(self) -> int:
        removed = self.repo.clear_mail()
        self.repo.set_setting(DIGEST_KEY, "{}")
        self.repo.add_mail_audit("clear", True, f"删掉缓存 {removed} 封")
        return removed

    def set_mark(self, message_id: str, mark: str | None) -> bool:
        """手动标「重要」/「不重要」，None 是交回规则判断。只记在本机，不写回邮箱。"""
        if mark is not None and mark not in MARKS:
            raise ValueError("只能标 important / normal，或交回规则（null）")
        return self.repo.set_mail_fields(message_id, mark=mark)

    def set_category(self, message_id: str, category: str | None) -> bool:
        """手动改分类，None 是交回规则判断。只记在本机。"""
        if category is not None and category not in CATEGORIES:
            raise ValueError("没有这个分类")
        return self.repo.set_mail_fields(message_id, category=category)

    def load_images(self, message_id: str) -> None:
        """在后台取这封信的图文版正文（含邮件自带的图片），洗干净后存本机。"""
        self._need()
        if self.repo.mail_message(message_id) is None:
            raise ValueError("没有这封邮件（可能已超出保留天数）")
        if self.state()["status"] != "connected":
            raise ValueError("还没登录，或登录过期了：先点「登录学校邮箱」")
        self._start("images", lambda: self._fetch_html(message_id))

    def set_reminder(self, reminder_id: int, action: str) -> bool:
        status = {"accept": "accepted", "dismiss": "dismissed", "undo": "pending"}.get(action)
        if status is None:
            raise ValueError("只能 accept / dismiss / undo")
        return self.repo.set_mail_reminder(reminder_id, status)

    # --- 后台任务 --------------------------------------------------------------------

    def _stamp(self) -> str:
        return self.now().astimezone(UTC).isoformat(timespec="seconds")

    def _reminders(self, rows: list[dict[str, Any]]) -> None:
        rules = self.rules()
        for m in rows:
            if not judge(m, rules):
                continue
            text = f"{m.get('subject') or ''}\n{m.get('body') or m.get('preview') or ''}"
            received = datetime.fromisoformat(m["received_at"])
            for found in extract_dates(text, received):
                self.repo.add_mail_reminder(m["id"], found["due"], found["snippet"])

    def _fetch(self, *, interactive: bool) -> None:
        action = "login" if interactive else "refresh"
        self._set_state(last_try=self._stamp())
        try:
            assert self.source is not None
            with self.source.open(interactive=interactive) as session:
                cutoff = self._cutoff()
                items: list[MailItem] = [
                    i for i in session.items(FETCH_LIMIT) if i.received_at >= cutoff
                ]
                added = self.repo.upsert_mail([asdict(i) for i in items])
                need = self.repo.mail_without_body([i.id for i in items])[:BODY_PREFETCH]
                # 正文取不到不影响列表，点开时再试
                with contextlib.suppress(Exception):
                    if need:
                        self.repo.set_mail_bodies(session.bodies(need))
                self.blocked = list(session.blocked)
            self.repo.prune_mail(cutoff)
            rows = [self.repo.mail_message(i.id) for i in items]
            self._reminders([r for r in rows if r])
            note = f"读到最近 {len(items)} 封" + (f"，新 {len(added)} 封" if added else "")
            self._set_state(status="connected", message=note, last_ok=self._stamp())
            self.repo.add_mail_audit(action, True, note)
        except SessionExpired as exc:
            self._set_state(status="expired", message=str(exc))
            self.repo.add_mail_audit(action, False, str(exc))
        except LoginCancelled as exc:
            self._set_state(message=str(exc))
            self.repo.add_mail_audit(action, False, str(exc))
        except MailError as exc:
            self._set_state(status="error", message=str(exc))
            self.repo.add_mail_audit(action, False, str(exc))
        except Exception as exc:  # noqa: BLE001 - 后台线程里的意外错误只记下来，不让服务崩
            text = f"出错了：{clean_error(exc)}"
            self._set_state(status="error", message=text)
            self.repo.add_mail_audit(action, False, text)

    def _fetch_bodies(self, ids: list[str]) -> None:
        try:
            assert self.source is not None
            with self.source.open(interactive=False) as session:
                bodies = session.bodies(ids)
            self.repo.set_mail_bodies(bodies)
            self._reminders([r for r in (self.repo.mail_message(i) for i in bodies) if r])
            self.repo.add_mail_audit("body", True, f"读了 {len(bodies)} 封的正文")
        except SessionExpired as exc:
            self._set_state(status="expired", message=str(exc))
            self.repo.add_mail_audit("body", False, str(exc))
        except Exception as exc:  # noqa: BLE001
            text = clean_error(exc)
            self._set_state(message=f"正文没读到：{text}")
            self.repo.add_mail_audit("body", False, text)

    def _fetch_html(self, message_id: str) -> None:
        try:
            assert self.source is not None
            with self.source.open(interactive=False) as session:
                got = session.html([message_id])
            if message_id not in got:
                raise MailError("邮箱没给图文版正文")
            raw, inline = got[message_id]
            clean = sanitize(raw, inline)
            if len(clean["html"]) > MAX_HTML:
                raise MailError("这封信的图文版太大了，请到网页版邮箱看")
            self.repo.set_mail_fields(message_id, html=json.dumps(clean, ensure_ascii=False))
            note = f"取了 1 封的图文版（自带图片 {clean['embedded']} 张）"
            self.repo.add_mail_audit("images", True, note)
        except SessionExpired as exc:
            self._set_state(status="expired", message=str(exc))
            self.repo.add_mail_audit("images", False, str(exc))
        except Exception as exc:  # noqa: BLE001
            text = str(exc) if isinstance(exc, MailError) else clean_error(exc)
            self._set_state(message=f"图文版没取到：{text}")
            self.repo.add_mail_audit("images", False, text)
