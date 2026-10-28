"""邮件栏的 AI 总结、翻译和「重要邮件汇总」：只在你点了按钮时调用。

- 第一次用之前要你点「同意」：之后点总结 / 翻译时，这封邮件的标题、发件人和正文（最多
  mail.ai.max_chars 字）会发给一位模型。没同意就不发。随时可以在设置里关掉。
- 谁来做：所选档位（mail.ai.plan）里按预计价格挑最便宜的一位，不看是谁家的。
- 花费：计入总预算；邮件栏另有每月上限（mail.ai.monthly_usd，默认 $0.30）和每天次数上限
  （mail.ai.daily_calls）。到了上限就不调用，告诉你原因。
- 结果只存本机（mail_messages 的 summary / translation 列，汇总在 app_settings），
  同一封再点直接给存下的，不重复花钱；「重新总结」才会再调用。
- 调用记录挂在隐藏会话（workflow = 'mail'）上，不出现在会话列表里；清空缓存 / 退出登录时
  发出去的邮件内容一并抹掉，只留花费。
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from roundtable.core.allocation import NotEnoughModels, cheapest
from roundtable.core.budget.guard import day_window, month_window
from roundtable.core.mail.box import DIGEST_KEY, MailBox
from roundtable.core.mail.rules import CATEGORIES, judge
from roundtable.core.providers import AllChannelsFailed, NoChannelAvailable, ProviderError

CONSENT_KEY = "mail.ai_ok"
WORKFLOW = "mail"
DIGEST_BODY_CHARS = 800  # 汇总时每封带正文的前这么多字


class MailAIError(ValueError):
    """不能调用（没同意、到上限、没有可用的模型……）。文字可直接给用户看。"""


class MailAI:
    def __init__(
        self,
        rt: Any,
        box: MailBox,
        *,
        rng: random.Random | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.rt = rt
        self.box = box
        self.rng = rng or random.Random()
        self.now = now or (lambda: datetime.now(UTC))
        self._busy: set[str] = set()

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.mail.ai

    # --- 状态 --------------------------------------------------------------------

    def consented(self) -> bool:
        return self.rt.repo.setting(CONSENT_KEY) == "1"

    def set_consent(self, ok: bool) -> None:
        self.rt.repo.set_setting(CONSENT_KEY, "1" if ok else "0")

    def _usage(self) -> tuple[float, int]:
        now = self.now()
        spent = self.rt.repo.spent_in_workflow(WORKFLOW, *month_window(now))
        calls = self.rt.repo.calls_in_workflow(WORKFLOW, *day_window(now))
        return spent, calls

    def status(self) -> dict[str, Any]:
        spent, calls = self._usage()
        rules = self.rules
        digest = self.box._json_setting(DIGEST_KEY, {})
        return {
            "ok": self.consented(),
            "language": rules.language,
            "month_spent": round(spent, 4),
            "monthly_usd": rules.monthly_usd,
            "today_calls": calls,
            "daily_calls": rules.daily_calls,
            "digest": digest if isinstance(digest, dict) and digest.get("text") else None,
        }

    # --- 调用 --------------------------------------------------------------------

    def _check(self) -> None:
        rules = self.rules
        if not self.box.enabled():
            raise MailAIError("邮件栏还没打开")
        if not self.consented():
            raise MailAIError("还没同意把邮件发给 AI：先点「同意」")
        spent, calls = self._usage()
        if calls >= rules.daily_calls:
            raise MailAIError(f"今天的 AI 总结 / 翻译已用满 {rules.daily_calls} 次，明天再来")
        if spent + rules.estimate_usd > rules.monthly_usd + 1e-9:
            raise MailAIError(
                f"邮件栏本月 AI 花费已到上限（${spent:.2f} / ${rules.monthly_usd:.2f}），"
                "下个月自动恢复"
            )
        if not self.rt.budget.check(rules.estimate_usd).allowed:
            raise MailAIError("总预算不够了")

    def _model(self) -> str:
        plan = self.rt.config.routing.plans.get(self.rules.plan)
        if plan is None:
            raise MailAIError(f"mail.ai.plan 不是已有的档位：{self.rules.plan!r}")
        tiers = set(plan.tiers)
        pool = [m for m in self.rt.router.available_models() if m.seat and m.tier in tiers]
        spec = self.rt.config.routing.planner
        try:
            return cheapest(
                pool,
                self.rng,
                input_tokens=spec.expected_input_tokens,
                output_tokens=spec.expected_output_tokens,
            ).id
        except NotEnoughModels:
            raise MailAIError(f"「{plan.label}」现在没有可用的模型") from None

    async def _ask(self, step: str, role: str, **variables: Any) -> str:
        self._check()
        if step in self._busy:
            raise MailAIError("上一次还没回来，请稍等")
        model_id = self._model()
        prompt = self.rt.prompts.render(role, self.rt.config.roundtable.prompts[role], **variables)
        session = self.rt.repo.mail_session()
        self._busy.add(step)
        try:
            completion = await self.rt.router.complete(
                model_id, prompt.messages, {"max_tokens": self.rules.max_tokens}
            )
        except (AllChannelsFailed, NoChannelAvailable, ProviderError) as e:
            self.rt.repo.record_call(
                session,
                step=step,
                role="member",
                model_id=model_id,
                messages=prompt.messages,
                prompt=prompt,
                failure=e,
            )
            raise MailAIError("这次没调用成功，稍后再试") from None
        finally:
            self._busy.discard(step)
        self.rt.repo.record_call(
            session,
            step=step,
            role="member",
            model_id=model_id,
            messages=prompt.messages,
            prompt=prompt,
            completion=completion,
        )
        text = (completion.text or "").strip()
        if not text:
            raise MailAIError("模型没有给出内容，稍后再试")
        return text

    def _mail(self, message_id: str) -> dict[str, Any]:
        m = self.rt.repo.mail_message(message_id)
        if m is None:
            raise MailAIError("没有这封邮件（可能已超出保留天数）")
        return m

    def _body(self, m: dict[str, Any]) -> str:
        body = m.get("body") or m.get("preview") or ""
        if not body.strip():
            raise MailAIError("这封信的正文还没读到：先点开它，等正文出来再试")
        limit = self.rules.max_chars
        return body if len(body) <= limit else body[:limit] + "\n……（后面省略）"

    async def summarize(self, message_id: str, *, again: bool = False) -> str:
        m = self._mail(message_id)
        if m.get("summary") and not again:
            return m["summary"]
        text = await self._ask(
            f"mail_summary:{message_id}",
            "mail_summary",
            subject=m.get("subject") or "（无标题）",
            sender=f"{m.get('sender_name') or ''} <{m.get('sender_addr') or ''}>".strip(),
            received=m.get("received_at") or "",
            body=self._body(m),
            language=self.rules.language,
        )
        self.rt.repo.set_mail_fields(message_id, summary=text)
        return text

    async def translate(self, message_id: str, *, again: bool = False) -> str:
        m = self._mail(message_id)
        if m.get("translation") and not again:
            return m["translation"]
        text = await self._ask(
            f"mail_translate:{message_id}",
            "mail_translate",
            subject=m.get("subject") or "（无标题）",
            body=self._body(m),
            language=self.rules.language,
        )
        self.rt.repo.set_mail_fields(message_id, translation=text)
        return text

    async def digest(self) -> dict[str, Any]:
        """把最近的重要邮件（未读在前）汇总成一段。"""
        view = self.box.view()
        rules = self.box.rules()
        picked = [m for m in view["messages"] if judge(m, rules)]  # 新的在前；下面未读排前
        picked = sorted(picked, key=lambda m: m["is_read"])[: self.rules.digest_mails]
        if not picked:
            raise MailAIError("最近没有重要邮件可以汇总")
        parts = []
        for n, m in enumerate(picked, 1):
            full = self.rt.repo.mail_message(m["id"]) or m
            body = (full.get("body") or full.get("preview") or "")[:DIGEST_BODY_CHARS]
            parts.append(
                f"〔{n}〕{full.get('received_at', '')}｜{full.get('sender_name') or ''}"
                f"｜{CATEGORIES[m['category']]}\n标题：{full.get('subject') or ''}\n{body}"
            )
        text = await self._ask(
            "mail_digest",
            "mail_digest",
            today=self.now().date().isoformat(),
            mails="\n\n".join(parts),
            language=self.rules.language,
        )
        digest = {
            "text": text,
            "at": self.now().astimezone(UTC).isoformat(timespec="seconds"),
            "ids": [m["id"] for m in picked],
        }
        self.rt.repo.set_setting(DIGEST_KEY, json.dumps(digest, ensure_ascii=False))
        return digest
