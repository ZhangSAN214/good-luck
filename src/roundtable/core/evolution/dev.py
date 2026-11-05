"""革新例会·交给开发侧（docs/evolution/README.md「做这条之后」）。

用户在网页上对一条建议点「做这条」后（这一下就是用户拍板），如果开启了 ``evolution.implement``：

- 在数据目录的专用工作副本里（第一次自动 clone，不碰用户平时用的目录）叫起开发侧命令行
  （默认 Claude Code，``claude -p``），任务说明来自 ``docs/evolution/开发任务模板.md``；
- 开发侧按项目规则实现、测试、推送分支、开 PR，最后一行输出 PR 网址；
- **从不自动合并**：用户在网页上点「合并」才执行 ``gh pr merge``。

外部命令只通过可注入的 ``run`` 调用（测试用假的）；输出写进 evolution/开发记录/，不进数据库。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
LOG_DIR = "开发记录"
TAIL_CHARS = 4000  # 日志里只扫最后这么多字找 PR 网址

# (命令, 工作目录, 标准输入, 日志文件, 超时秒数) → 退出码；超时返回 None
Run = Callable[[Sequence[str], Path, str, Path, float], Awaitable[int | None]]


class DevError(RuntimeError):
    """叫不起开发侧（没开启、找不到命令、工作副本准备失败）。"""


def resolve(program: str) -> str | None:
    """命令的完整路径：先查 PATH，再查常见的用户安装位置（安装器不一定改 PATH）。"""
    found = shutil.which(program)
    if found:
        return found
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    candidates = [home / ".local" / "bin", local / "Microsoft" / "WinGet" / "Links"]
    packages = local / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        candidates += [p / "bin" for p in packages.iterdir() if p.is_dir()]
    for folder in candidates:
        for name in (program, f"{program}.exe", f"{program}.cmd"):
            if (folder / name).is_file():
                return str(folder / name)
    return None


def render_brief(template: str, **values: str) -> str:
    """把 {week} 这类占位换成实际内容（只换已知的，建议原文里的花括号不展开）。"""
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def find_pr(text: str) -> str | None:
    found = PR_URL.findall(text[-TAIL_CHARS:])
    return found[-1] if found else None


def branch_name(week: str, idea: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9-]", "-", f"{week}-{idea}").strip("-").lower()
    return f"evolve/{safe}"


async def run_process(
    argv: Sequence[str], cwd: Path, stdin: str, log: Path, timeout_s: float
) -> int | None:
    """真正跑外部命令：标准输入喂任务说明，标准输出 / 错误都写进日志。"""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=out,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await asyncio.wait_for(proc.communicate(stdin.encode("utf-8")), timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return None
    return proc.returncode


class DevAgent:
    def __init__(self, rules, evolution_dir: Path, project_root: Path, run: Run = run_process):
        self.rules = rules  # EvolutionImplementRules
        self.evolution_dir = evolution_dir
        self.project_root = project_root
        self.run = run

    @property
    def workspace(self) -> Path:
        return self.evolution_dir / self.rules.workspace_dir

    def log_path(self, week: str, idea: str) -> Path:
        return self.evolution_dir / LOG_DIR / f"{week}-{idea}.log"

    def _argv(self, argv: Sequence[str]) -> list[str]:
        if not argv:
            raise DevError("没有配置命令（evolution.implement）")
        program = resolve(argv[0])
        if program is None:
            raise DevError(f"找不到命令「{argv[0]}」：先安装，或在配置里写完整路径")
        return [program, *argv[1:]]

    async def ensure_workspace(self, log: Path) -> None:
        """第一次用时把仓库 clone 到工作副本（远端地址取用户目录里的 origin）。"""
        if (self.workspace / ".git").exists():
            return
        git = self._argv(["git"])
        url_file = log.with_suffix(".origin")
        code = await self.run(
            [*git, "-C", str(self.project_root), "remote", "get-url", "origin"],
            self.project_root,
            "",
            url_file,
            60,
        )
        url = url_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        url_file.unlink(missing_ok=True)
        if code != 0 or not url:
            raise DevError("读不到仓库的 origin 地址")
        self.workspace.parent.mkdir(parents=True, exist_ok=True)
        code = await self.run(
            [*git, "clone", url[-1], str(self.workspace)], self.evolution_dir, "", log, 600
        )
        if code != 0:
            raise DevError("准备工作副本失败（git clone），详见开发记录")

    async def implement(
        self, *, week: str, session_id: str, idea: str, text: str
    ) -> tuple[str, str | None, str]:
        """叫起开发侧实现一条建议。返回 (状态 done / failed / timeout, PR 网址, 日志路径)。"""
        log = self.log_path(week, idea)
        argv = self._argv(self.rules.agent)
        await self.ensure_workspace(log)
        template = (self.project_root / self.rules.brief).read_text(encoding="utf-8")
        brief = render_brief(
            template,
            week=week,
            session_id=session_id,
            idea=idea,
            text=text,
            base=self.rules.base_branch,
            branch=branch_name(week, idea),
        )
        code = await self.run(argv, self.workspace, brief, log, self.rules.timeout_minutes * 60)
        output = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        pr = find_pr(output)
        if code is None:
            return "timeout", pr, str(log)
        return ("done" if code == 0 and pr else "failed"), pr, str(log)

    async def merge(self, pr: str, log: Path) -> bool:
        """用户点了「合并」：gh pr merge（合并方式见配置）。"""
        gh = self._argv(self.rules.gh)
        code = await self.run(
            [*gh, "pr", "merge", pr, f"--{self.rules.merge_method}"],
            self.evolution_dir,
            "",
            log,
            300,
        )
        return code == 0
