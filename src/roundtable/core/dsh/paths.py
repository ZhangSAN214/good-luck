"""dsh 的本机目录：全部在 ``data/dsh/`` 下（不进 git），删掉整个目录圆桌不受影响。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DshPaths:
    root: Path
    package: str  # 运行时的 npm 包名（config/dsh_plugins.yaml 的 runtime.package）

    @property
    def runtime(self) -> Path:  # npm 装的 dsh 本体
        return self.root / "runtime"

    @property
    def bin_js(self) -> Path:
        return self.runtime / "node_modules" / Path(*self.package.split("/")) / "lib" / "bin.js"

    @property
    def home(self) -> Path:  # DSH_HOME：profile、会话、插件、dsh 自己的设置
        return self.root / "home"

    @property
    def work(self) -> Path:  # dsh 的工作区（默认只读模式下它也只能读这里）
        return self.root / "work"

    @property
    def cache(self) -> Path:  # 下载的插件安装包
        return self.root / "cache"

    @property
    def overlay(self) -> Path:
        return self.root / "overlay.yml"

    @property
    def usage(self) -> Path:
        return self.root / "usage.json"

    @property
    def installed(self) -> bool:
        return self.bin_js.is_file()

    def ensure(self) -> None:
        for d in (self.runtime, self.home, self.work, self.cache):
            d.mkdir(parents=True, exist_ok=True)
