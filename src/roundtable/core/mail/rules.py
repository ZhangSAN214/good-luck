"""邮件栏的「重要」判断与日期提取：只在本机按规则算，不调用模型、不花钱。

- 重要：发件人域名在白名单（默认学校域名，学生互发的除外）、你手动加的发件人、
  标题或摘要里有关键词、你在邮箱里插了旗标、发件人标了「重要」。命中任一条就算，
  并写明原因。规则存在本机数据库，可随时改；改了以后列表马上按新规则重算。
- 日期：只看含「截止 / due / deadline / exam…」这类词的句子，从里面找日期（可带时间），
  生成「待确认的提醒」，用户点一下才算加入。年份缺省时取邮件收到之后最近的那一天；
  早于收到日期的不要。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_ENTRIES = 100
MAX_DATES_PER_MAIL = 3

DEFAULT_DOMAINS = ("manchester.ac.uk",)
# 同学之间互发的邮件不算「学校发来的」
DEFAULT_IGNORE_DOMAINS = ("student.manchester.ac.uk",)
DEFAULT_KEYWORDS = (
    "deadline",
    "due",
    "exam",
    "assessment",
    "coursework",
    "submission",
    "extension",
    "timetable",
    "resit",
    "mitigating circumstances",
    "results",
    "urgent",
    "截止",
    "考试",
    "作业",
    "成绩",
    "提交",
    "补考",
    "课表",
)
# 句子里有这些词才去找日期
DATE_TRIGGERS = (
    "due",
    "deadline",
    "by ",
    "before",
    "submit",
    "submission",
    "exam",
    "assessment",
    "closes",
    "截止",
    "之前",
    "前提交",
    "考试",
)

MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}
_MON = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
)
_TIME = r"(?:\s*(?:at|,|@)?\s*(\d{1,2})(?!\d)(?:[:.](\d{2}))?\s*(am|pm)?)?"
_YEAR = r"(?:,?\s+(\d{4}))?"
_PATTERNS = [
    # 5 Nov 2026 23:59 / 5th November at 5pm
    ("dmy", re.compile(rf"\b(\d{{1,2}})(?!\d)(?:st|nd|rd|th)?\s+{_MON}\.?{_YEAR}{_TIME}", re.I)),
    # Nov 5, 2026 11:59pm / November 5th
    ("mdy", re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}})(?!\d)(?:st|nd|rd|th)?{_YEAR}{_TIME}", re.I)),
    # 2026-11-05 23:59
    ("iso", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?")),
    # 05/11/2026（英国写法：日/月/年）
    ("uk", re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")),
    # 11月5日 23:59
    ("zh", re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号](?:\s*(\d{1,2})[:：点](\d{2})?)?")),
]


class MailRules(BaseModel):
    """「重要」规则。全部小写保存；条目去重、去空。"""

    model_config = ConfigDict(extra="forbid")

    domains: list[str] = Field(default_factory=lambda: list(DEFAULT_DOMAINS))
    ignore_domains: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORE_DOMAINS))
    senders: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    use_flags: bool = True

    @field_validator("domains", "ignore_domains", "senders", "keywords")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for v in values:
            v = str(v).strip().lower().lstrip("@")
            if not v:
                continue
            if len(v) > 100:
                raise ValueError(f"「{v[:20]}…」太长（最多 100 个字）")
            if v not in out:
                out.append(v)
        if len(out) > MAX_ENTRIES:
            raise ValueError(f"最多 {MAX_ENTRIES} 条")
        return out


def _domain_match(addr: str, domains: list[str]) -> bool:
    host = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    return any(host == d or host.endswith("." + d) for d in domains)


def _has_word(text: str, word: str) -> bool:
    if re.search(r"[a-z0-9]", word):  # 英文按整词，避免 due 命中 overdue / residue
        return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None
    return word in text


def judge(mail: dict[str, Any], rules: MailRules) -> list[str]:
    """返回判为重要的原因（空列表 = 不重要）。mail 是邮件栏里一封信的 dict。

    你手动标过的优先：标「重要」就算重要，标「不重要」就不算（规则不再管它）。"""
    if mail.get("mark") == "important":
        return ["你标为重要"]
    if mail.get("mark") == "normal":
        return []
    reasons: list[str] = []
    addr = str(mail.get("sender_addr") or "").lower()
    if addr and addr in rules.senders:
        reasons.append("你加的发件人")
    elif _domain_match(addr, rules.domains) and not _domain_match(addr, rules.ignore_domains):
        reasons.append("学校发件人")
    text = f"{mail.get('subject') or ''}\n{mail.get('preview') or ''}".lower()
    words = [k for k in rules.keywords if _has_word(text, k)]
    if words:
        reasons.append("关键词：" + "、".join(words[:3]))
    if rules.use_flags and mail.get("flagged"):
        reasons.append("你插了旗标")
    if rules.use_flags and mail.get("high"):
        reasons.append("发件人标为重要")
    return reasons


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？；;])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _hour(h: str | None, m: str | None, ampm: str | None) -> tuple[int, int] | None:
    if h is None:
        return None
    hour, minute = int(h), int(m or 0)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
    elif m is None:  # 光一个数字（如「5 Nov 2026」后面的页码）不当时间
        return None
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _pick_year(month: int, day: int, year: str | None, received: date) -> date | None:
    try:
        if year:
            return date(int(year), month, day)
        for y in (received.year, received.year + 1):
            d = date(y, month, day)
            if d >= received:
                return d
    except ValueError:
        return None
    return None


def _parse(kind: str, g: tuple[str | None, ...], received: date):
    if kind == "dmy":
        day, mon, year, h, m, ap = g
        return _pick_year(MONTHS[mon.lower()], int(day), year, received), _hour(h, m, ap)
    if kind == "mdy":
        mon, day, year, h, m, ap = g
        return _pick_year(MONTHS[mon.lower()], int(day), year, received), _hour(h, m, ap)
    if kind == "iso":
        year, mon, day, h, m = g
        return _pick_year(int(mon), int(day), year, received), _hour(h, m, None)
    if kind == "uk":
        day, mon, year = g
        return _pick_year(int(mon), int(day), year, received), None
    year, mon, day, h, m = g  # zh
    at = _hour(h, m or ("00" if h else None), None)
    return _pick_year(int(mon), int(day), year, received), at


def extract_dates(text: str, received: datetime) -> list[dict[str, str]]:
    """从正文里找截止日期。返回 [{due: 'YYYY-MM-DD' 或 'YYYY-MM-DDTHH:MM', snippet}]。"""
    got: list[dict[str, str]] = []
    start = received.date()
    for sentence in _sentences(text or ""):
        low = sentence.lower()
        if not any(t in low for t in DATE_TRIGGERS):
            continue
        for kind, pattern in _PATTERNS:
            for match in pattern.finditer(sentence):
                day, at = _parse(kind, match.groups(), start)
                if day is None or day < start or day > start + timedelta(days=400):
                    continue
                due = day.isoformat() + (f"T{at[0]:02d}:{at[1]:02d}" if at else "")
                if any(x["due"] == due for x in got):
                    continue
                snippet = sentence if len(sentence) <= 160 else sentence[:157] + "…"
                got.append({"due": due, "snippet": snippet})
                if len(got) >= MAX_DATES_PER_MAIL:
                    return got
    return got


# --- 分类（只在本机按规则分，你可以逐封改） ---------------------------------------------

# 按先后判断：先认安全类系统通知，再按内容，最后按发件人
CATEGORIES: dict[str, str] = {
    "exam": "考试成绩",
    "deadline": "作业截止",
    "money": "费用财务",
    "course": "课程教学",
    "event": "活动讲座",
    "notice": "学校通知",
    "system": "系统通知",
    "personal": "个人往来",
    "other": "其他",
}
_SECURITY = (
    "verification code",
    "security code",
    "one-time",
    "password",
    "sign-in",
    "sign in",
    "security alert",
    "multi-factor",
    "mfa",
    "验证码",
    "密码",
    "登录提醒",
)
_CONTENT: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "exam",
        (
            "exam",
            "exams",
            "examination",
            "results",
            "grade",
            "grades",
            "marks",
            "transcript",
            "resit",
            "invigilat",
            "考试",
            "成绩",
            "补考",
        ),
    ),
    (
        "deadline",
        (
            "coursework",
            "assignment",
            "submission",
            "submit",
            "deadline",
            "due",
            "extension",
            "turnitin",
            "作业",
            "截止",
            "提交",
            "延期",
        ),
    ),
    (
        "money",
        (
            "tuition",
            "fee",
            "fees",
            "payment",
            "invoice",
            "refund",
            "scholarship",
            "bursary",
            "rent",
            "学费",
            "账单",
            "退款",
            "奖学金",
        ),
    ),
    (
        "course",
        (
            "lecture",
            "lectures",
            "seminar",
            "tutorial",
            "lab",
            "module",
            "unit",
            "course",
            "timetable",
            "blackboard",
            "canvas",
            "reading list",
            "课程",
            "课表",
            "上课",
        ),
    ),
    (
        "event",
        (
            "event",
            "workshop",
            "talk",
            "webinar",
            "careers",
            "society",
            "fair",
            "invitation",
            "invite",
            "register now",
            "活动",
            "讲座",
            "邀请",
        ),
    ),
)
_NOREPLY = re.compile(r"(^|[._-])(no-?reply|do-?not-?reply|notifications?|alerts?|mailer)([._-]|@)")


def classify(mail: dict[str, Any], rules: MailRules | None = None) -> str:
    """一封邮件的分类键（CATEGORIES 里的一个）。你手动改过的优先。"""
    if mail.get("category") in CATEGORIES:
        return mail["category"]
    text = f"{mail.get('subject') or ''}\n{mail.get('preview') or ''}".lower()
    if any(_has_word(text, w) for w in _SECURITY):
        return "system"
    for key, words in _CONTENT:
        if any(_has_word(text, w) for w in words):
            return key
    addr = str(mail.get("sender_addr") or "").lower()
    if _NOREPLY.search(addr):
        return "system"
    rules = rules or MailRules()
    if _domain_match(addr, rules.ignore_domains):
        return "personal"
    if _domain_match(addr, rules.domains):
        return "notice"
    return "personal" if addr else "other"
