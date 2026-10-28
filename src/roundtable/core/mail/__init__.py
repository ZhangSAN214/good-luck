"""终端邮件栏：用户本人学校邮箱的只读接入（docs/MAIL.md）。"""

from .ai import MailAI, MailAIError
from .box import MailBox, MailBusy
from .owa import (
    BrowserMissing,
    LoginCancelled,
    MailError,
    MailItem,
    MailSource,
    OwaBrowser,
    SessionExpired,
)
from .rules import CATEGORIES, MailRules, classify, extract_dates, judge

__all__ = [
    "CATEGORIES",
    "MailAI",
    "MailAIError",
    "BrowserMissing",
    "LoginCancelled",
    "MailBox",
    "MailBusy",
    "MailError",
    "MailItem",
    "MailRules",
    "MailSource",
    "OwaBrowser",
    "SessionExpired",
    "classify",
    "extract_dates",
    "judge",
]
