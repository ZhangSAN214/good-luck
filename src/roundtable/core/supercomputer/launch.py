"""启动器：怎么把一位助手跑起来（配置里的 via）。

- ``CommandLauncher``：via=command，按 argv 直接运行电脑上装好的官方命令行（用它自己登录的订阅）。
  先查 PATH，再查常见的用户安装位置（如 ``~/.local/bin``：安装器不一定改 PATH）。
  环境变量照搬当前进程，但去掉圆桌的所有 key 和一切 ``*_API_KEY``：助手用自己登录的订阅额度，
  不会因为环境里有 key 就改走按量计费，也拿不到圆桌的钱。
- ``HookLauncher``：via=dsh 等由服务层拼好的启动方式（这里不导入 dsh）。
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Iterable, Mapping

from roundtable.core.config.schema import SuperWorker
from roundtable.core.evolution_dev import resolve
from roundtable.core.supercomputer.engine import Launch


class CommandLauncher:
    def __init__(
        self,
        drop_env: Iterable[str] = (),
        environ: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = resolve,
    ) -> None:
        self.drop = {n.upper() for n in drop_env}
        self.environ = environ
        self.which = which

    def _exe(self, worker: SuperWorker) -> str | None:
        return self.which(worker.argv[0]) if worker.argv else None

    def blocked(self, worker: SuperWorker) -> str | None:
        if self._exe(worker) is None:
            name = worker.argv[0] if worker.argv else "?"
            return f"电脑上没找到 {name}：装好并登录后重启圆桌"
        return None

    def launch(self, worker: SuperWorker) -> Launch:
        source = os.environ if self.environ is None else self.environ
        env = {
            k: v
            for k, v in source.items()
            if k.upper() not in self.drop and not k.upper().endswith("_API_KEY")
        }
        exe = self._exe(worker) or worker.argv[0]
        return Launch([exe, *worker.argv[1:]], env)

    async def before(self, worker: SuperWorker) -> None:
        return None

    async def after(self, worker: SuperWorker) -> None:
        return None


class HookLauncher:
    def __init__(
        self,
        blocked: Callable[[], str | None],
        launch: Callable[[], tuple[list[str], dict[str, str], list[str]]],
        before: Callable[[], Awaitable[None]],
        after: Callable[[], Awaitable[None]],
    ) -> None:
        self._blocked, self._launch, self._before, self._after = blocked, launch, before, after

    def blocked(self, worker: SuperWorker) -> str | None:
        return self._blocked()

    def launch(self, worker: SuperWorker) -> Launch:
        argv, env, secrets = self._launch()
        return Launch(argv, env, secrets)

    async def before(self, worker: SuperWorker) -> None:
        await self._before()

    async def after(self, worker: SuperWorker) -> None:
        await self._after()
