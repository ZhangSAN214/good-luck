"""工具调用循环：成员申请工具 → 代码执行 → 结果追加到同一对话 → 再调用，直到不再申请工具。

- 工具说明（prompts/tools）接在该步骤的系统提示之后；同一步骤所有成员相同。
- 每一轮都是一次独立记录的模型调用（照常计费）；每个工具调用记在 tool_calls 表。
- 达到最多轮数后仍申请工具时，去掉工具调用，剩下的内容作为最终结果。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from roundtable.core.prompts import RenderedPrompt
from roundtable.core.providers import Message
from roundtable.core.tools import ToolResult, has_unclosed_call, parse_calls, strip_calls

from .base import CallOutcome, TableContext, call_once

INCOMPLETE = "工具调用不完整（缺少 </tool_call>，可能是输出过长被截断），请写短一些后重新申请。"


async def call_with_tools(
    ctx: TableContext,
    *,
    step: str,
    role: str,
    model_id: str,
    prompt: RenderedPrompt,
    code: str | None,
    messages: Sequence[Message],
    tools: list[str],
) -> CallOutcome:
    box = ctx.toolbox
    assert box is not None
    rules = ctx.config.roundtable.tools
    template = ctx.prompts.get("tools", ctx.prompt_version("tools"))

    def render(results: str, remaining: int) -> RenderedPrompt:
        return template.render(
            available="、".join(tools),
            max_rounds=str(rules.max_rounds),
            max_runs=str(rules.python.max_runs),
            timeout_s=f"{rules.python.timeout_s:g}",
            max_images=str(rules.image.max_per_step),
            max_searches=str(rules.search.max_per_step),
            max_fetches=str(rules.search.max_fetch_per_step),
            results=results,
            remaining=str(remaining),
        )

    guide = next(m.content for m in render("", rules.max_rounds).messages if m.role == "system")
    convo = list(messages)
    first = next(i for i, m in enumerate(convo) if m.role == "system")
    convo[first] = Message("system", f"{convo[first].content}\n\n{guide}")

    def record_model_call(tool_model: str, tool_prompt, completion, failure) -> None:
        ctx.repo.record_call(
            ctx.session_id,
            step=step,
            role="tool",
            model_id=tool_model,
            messages=tool_prompt.messages,
            table_no=ctx.table_no,
            code=code,
            prompt=tool_prompt,
            completion=completion,
            failure=failure,
        )

    round_no = 0
    while True:
        out = await call_once(
            ctx, step=step, role=role, model_id=model_id, prompt=prompt, code=code, messages=convo
        )
        if out.completion is None:
            return out
        text = out.completion.text
        requests = parse_calls(text)
        incomplete = not requests and has_unclosed_call(text)
        if (not requests and not incomplete) or round_no >= rules.max_rounds:
            break
        round_no += 1
        blocks = []
        if incomplete:
            blocks.append(ToolResult("?", "rejected", INCOMPLETE).block())
        for req in requests:
            ctx.emit("tool_started", step, code, tool=req.name, round=round_no)
            result = await box.execute(
                req,
                step=step,
                code=code,
                round_no=round_no,
                call_id=out.call_id,
                allowed=tools,
                on_model_call=record_model_call,
            )
            ctx.emit(
                "tool_finished",
                step,
                code,
                tool=result.tool,
                status=result.status,
                files=list(result.files),
            )
            blocks.append(result.block())
        remaining = rules.max_rounds - round_no
        reply = render("\n\n".join(blocks), remaining)
        user = next(m.content for m in reply.messages if m.role == "user")
        convo += [Message("assistant", text), Message("user", user)]
    return CallOutcome(replace(out.completion, text=strip_calls(out.completion.text)), out.call_id)
