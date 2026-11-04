"""用预付 / 赠送 API 额度的助手（via=credit，docs/SUPERCOMPUTER.md「API 额度」）。

和 ``CommandLauncher`` 一样按 argv 运行电脑上装好的命令行，再把 ``credit_channel`` 那个渠道的 key
放进子进程的环境变量：花的是那个渠道的 API 额度，不是命令行自己登录的订阅。
2026-11-04 用户批准（会员每月送的 Claude API 额度给工作台用），这里是除适配器外少数能拿出 key 的
地方之一（架构测试登记了这个文件）。输出里出现 key 时替换成 ``***``。
渠道到了每月上限（``monthly_cap_usd``）时不再派活。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from roundtable.core.config.schema import SuperWorker
from roundtable.core.providers.secrets import Secret
from roundtable.core.supercomputer.engine import Launch
from roundtable.core.supercomputer.launch import CommandLauncher


class CreditLauncher:
    def __init__(
        self,
        base: CommandLauncher,
        keys: Mapping[str, tuple[str, Secret | None]],
        gate: Callable[[str], str | None] | None = None,
    ) -> None:
        self.base = base
        self.keys = dict(keys)  # 渠道 → (环境变量名, key)
        self.gate = gate  # 渠道到了花费上限时返回原因

    def blocked(self, worker: SuperWorker) -> str | None:
        missing = self.base.blocked(worker)
        if missing:
            return missing
        channel = worker.credit_channel or ""
        env_name, key = self.keys.get(channel, ("", None))
        if key is None:
            return f"没有 {env_name or channel} 的 key：填进 .env 后重启圆桌"
        if self.gate is not None:
            try:
                return self.gate(channel)
            except Exception:  # 账本读不到不挡
                return None
        return None

    def launch(self, worker: SuperWorker) -> Launch:
        launch = self.base.launch(worker)
        env_name, key = self.keys.get(worker.credit_channel or "", ("", None))
        if key is None:
            return launch
        value = key.reveal()
        return Launch(launch.argv, {**launch.env, env_name: value}, [*launch.secrets, value])

    async def before(self, worker: SuperWorker) -> None:
        return None

    async def after(self, worker: SuperWorker) -> None:
        return None
