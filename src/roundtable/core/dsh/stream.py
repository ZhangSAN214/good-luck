"""dsh 一次性活的事件流（``--json``）：数步数，到了上限先停，再让它用几句话交代清楚。

dsh 0.2.1-rc.3 的 headless 方式没有「最多几步」的设置，所以圆桌自己数：每出现一个
``tool_call`` 事件算一步。超过 ``dsh.job_max_steps`` 就结束进程，再用 ``--session-id``
接着同一段对话跑一次收尾（提示词 ``dsh_wrapup``，最多 ``dsh.wrapup_steps`` 步），
把「做了什么、还差什么」交回去。见 docs/DSH.md「每件活的步数和上下文」。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable

from roundtable.core.supercomputer.runner import RunResult


class DshWatch:
    def __init__(
        self,
        max_steps: int,
        wrapup: Callable[[str], tuple[list[str], str, DshWatch]] | None = None,
    ) -> None:
        self.max_steps = max_steps  # 0 = 不限
        self.wrapup = wrapup  # 会话 id → (收尾的 argv, 收尾的任务正文, 收尾的计数器)
        self.steps = 0
        self.session_id: str | None = None
        self.texts: list[str] = []
        self.final: str | None = None
        self.stopped = False

    def feed(self, line: str) -> str | None:
        """读一行事件；该停下时返回原因。"""
        try:
            event = json.loads(line)
        except ValueError:
            return None
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        if kind == "session" and isinstance(event.get("sessionId"), str):
            self.session_id = event["sessionId"]
        elif kind == "text" and isinstance(event.get("text"), str):
            self.texts.append(event["text"])
        elif kind == "final" and isinstance(event.get("text"), str):
            self.final = event["text"]
        elif kind == "tool_call":
            self.steps += 1
            if self.max_steps and self.steps > self.max_steps and not self.stopped:
                self.stopped = True
                return f"到了步数上限（{self.max_steps} 步）"
        return None

    def followup(self) -> tuple[list[str], str, DshWatch] | None:
        """停下了就接着同一段对话收尾一次；没停或拿不到会话 id 时不收尾。"""
        if not self.stopped or self.wrapup is None or not self.session_id:
            return None
        return self.wrapup(self.session_id)

    def answer(self) -> str:
        if self.final:
            return self.final
        return "\n\n".join(t for t in self.texts if t.strip())

    def finish(self, result: RunResult, wrapped: RunResult | None = None) -> RunResult:
        """把事件流换成回答；停下过的活标明到了上限，收尾说明接在后面。"""
        if not self.stopped:
            return dataclasses.replace(result, output=self.answer() or result.output)
        note = f"（到了步数上限 {self.max_steps} 步，dsh 先停下了；文件夹里是做到这里的成果）"
        parts = [note]
        if self.answer():
            parts.append(self.answer())
        if wrapped is not None and wrapped.ok and wrapped.output.strip():
            parts.append("收尾说明：\n" + wrapped.output.strip())
        code = 0 if wrapped is not None and wrapped.ok else 1
        return dataclasses.replace(result, code=code, output="\n\n".join(parts), timed_out=False)
