"""一次性安装：dsh 运行时（npm 包，版本钉死）+ 白名单插件（下载后核对 sha256 再装）。

由 ``scripts/setup_dsh.py`` 调用，在用户电脑上跑；云端测试只测检查逻辑，不真的装。
- 需要 Node（``runtime.node_min`` 以上）和 npm；装插件时 dsh 会转交给 pnpm，所以还要 pnpm。
- 运行时装在 ``data/dsh/runtime/``，插件装进 ``data/dsh/home/profiles/web/``，都不进 git。
- 只装白名单里 ``enabled`` 的插件；哈希不对就拒绝，并删掉下载的文件。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from roundtable.core.dsh.catalog import DshCatalog, DshPlugin
from roundtable.core.dsh.paths import DshPaths

Run = Callable[..., subprocess.CompletedProcess[str]]


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def node_major(version_text: str) -> int | None:
    m = re.match(r"\s*v?(\d+)\.", version_text)
    return int(m.group(1)) if m else None


def _version(cmd: str, run: Run) -> str | None:
    exe = shutil.which(cmd)
    if exe is None:
        return None
    try:
        out = run([exe, "--version"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout or "").strip() or None


def check_tools(catalog: DshCatalog, run: Run = subprocess.run) -> list[Check]:
    node = _version("node", run)
    major = node_major(node or "")
    need = catalog.runtime.node_min
    checks = [
        Check(
            "node",
            major is not None and major >= need,
            f"Node {node}"
            if node
            else f"没装 Node（需要 {need} 以上：https://nodejs.org 下载 LTS 版）",
        )
    ]
    if node and (major is None or major < need):
        checks[0] = Check("node", False, f"Node {node} 太旧，需要 {need} 以上")
    npm = _version("npm", run)
    checks.append(
        Check("npm", npm is not None, f"npm {npm}" if npm else "没找到 npm（随 Node 一起装）")
    )
    pnpm = _version("pnpm", run)
    checks.append(
        Check(
            "pnpm",
            pnpm is not None,
            f"pnpm {pnpm}"
            if pnpm
            else "没找到 pnpm：运行 npm install -g pnpm（装插件时 dsh 要用）",
        )
    )
    return checks


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def download(plugin: DshPlugin, dest: Path, *, client: httpx.Client | None = None) -> Path:
    """下载插件安装包并核对哈希；不对就删掉并报错。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(timeout=120, follow_redirects=True)
    try:
        with client.stream("GET", plugin.url) as resp:
            if resp.status_code != 200:
                raise InstallError(f"{plugin.id}：下载失败 HTTP {resp.status_code}")
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as exc:
        raise InstallError(f"{plugin.id}：下载失败（{type(exc).__name__}）") from None
    finally:
        if own:
            client.close()
    got = sha256_of(dest)
    if got != plugin.sha256:
        dest.unlink(missing_ok=True)
        raise InstallError(f"{plugin.id}：安装包哈希对不上（{got[:12]}…），已删除，没有安装")
    return dest


def runtime_argv(catalog: DshCatalog, paths: DshPaths) -> list[str]:
    npm = shutil.which("npm") or "npm"
    spec = f"{catalog.runtime.package}@{catalog.runtime.version}"
    return [npm, "install", "--prefix", str(paths.runtime), "--no-audit", "--no-fund", spec]


def plugin_argv(node: str, paths: DshPaths, package: Path) -> list[str]:
    return [
        node,
        str(paths.bin_js),
        "plugin",
        "--profile",
        "web",
        "add",
        f"file:{package.resolve().as_posix()}",
    ]


def install(
    catalog: DshCatalog,
    paths: DshPaths,
    env: dict[str, str],
    *,
    run: Run = subprocess.run,
    fetch: Callable[[DshPlugin, Path], Path] = download,
    say: Callable[[str], None] = print,
    plugins: Sequence[str] | None = None,
) -> None:
    bad = [c for c in check_tools(catalog, run) if not c.ok]
    if bad:
        raise InstallError("；".join(c.detail for c in bad))
    paths.ensure()
    say(f"安装 dsh 运行时 {catalog.runtime.version}（约 0.9 GB，几分钟）…")
    out = run(runtime_argv(catalog, paths), capture_output=True, text=True, check=False)
    if out.returncode != 0 or not paths.bin_js.is_file():
        raise InstallError(f"npm 安装失败：{(out.stderr or out.stdout or '')[-400:]}")
    node = shutil.which("node") or "node"
    wanted = set(plugins) if plugins is not None else None
    for plugin in catalog.enabled:
        if wanted is not None and plugin.id not in wanted:
            continue
        say(f"下载并核对 {plugin.id} {plugin.version} …")
        package = fetch(plugin, paths.cache / f"{plugin.id}-{plugin.version}.tgz")
        say(f"安装 {plugin.id} …")
        out = run(
            plugin_argv(node, paths, package),
            capture_output=True,
            text=True,
            check=False,
            cwd=str(paths.work),
            env=env,
        )
        if out.returncode != 0:
            raise InstallError(f"{plugin.id} 安装失败：{(out.stderr or out.stdout or '')[-400:]}")
    say("装好了。打开圆桌 →「工作台」→ 启动。")


# ---- 装后核对 + 整批回滚（docs/DSH.md）：装完逐项核对是否真生效，没过就退回装之前的样子 ----

PREVIOUS = "runtime.prev"


def runtime_version(paths: DshPaths) -> str | None:
    """装好的 dsh 运行时的版本（读 npm 包的 package.json）。"""
    try:
        data = json.loads((paths.bin_js.parent.parent / "package.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if version else None


def verify(catalog: DshCatalog, paths: DshPaths, run: Run = subprocess.run) -> list[Check]:
    """逐项核对：工具、运行时文件、版本是不是钉死的那个、白名单插件的安装包是不是核对过的那份。"""
    checks = check_tools(catalog, run)
    checks.append(
        Check(
            "runtime",
            paths.installed,
            "dsh 运行时文件在" if paths.installed else f"没找到 dsh 运行时（{paths.bin_js}）",
        )
    )
    got = runtime_version(paths)
    want = catalog.runtime.version
    checks.append(
        Check(
            "runtime_version",
            got == want,
            f"运行时版本 {got}" if got == want else f"运行时版本是 {got or '未知'}，应该是 {want}",
        )
    )
    for plugin in catalog.enabled:
        pkg = paths.cache / f"{plugin.id}-{plugin.version}.tgz"
        ok = pkg.is_file() and sha256_of(pkg) == plugin.sha256.lower()
        checks.append(
            Check(
                f"plugin_{plugin.id}",
                ok,
                f"插件 {plugin.id} {plugin.version} 安装包核对通过"
                if ok
                else f"插件 {plugin.id} 的安装包不在或哈希不对",
            )
        )
    return checks


def install_safely(
    catalog: DshCatalog,
    paths: DshPaths,
    env: dict[str, str],
    *,
    run: Run = subprocess.run,
    fetch: Callable[[DshPlugin, Path], Path] = download,
    say: Callable[[str], None] = print,
) -> list[Check]:
    """安装 + 装后核对；任何一步没过，运行时整批退回到装之前（原来没有就删掉），再把异常抛出去。
    抛出的 InstallError 带 ``checks``（核对结果，没走到核对时为空）。"""
    previous = paths.root / PREVIOUS
    had = paths.runtime.exists()
    if had:
        shutil.rmtree(previous, ignore_errors=True)
        paths.runtime.rename(previous)
    checks: list[Check] = []
    try:
        install(catalog, paths, env, run=run, fetch=fetch, say=say)
        checks = verify(catalog, paths, run)
        bad = [c for c in checks if not c.ok]
        if bad:
            raise InstallError("装完核对没过：" + "；".join(c.detail for c in bad))
    except BaseException as exc:
        shutil.rmtree(paths.runtime, ignore_errors=True)
        if had:
            previous.rename(paths.runtime)
        say("已退回到安装之前的样子。" if had else "已删掉装了一半的运行时。")
        if isinstance(exc, InstallError):
            exc.checks = checks  # type: ignore[attr-defined]
        raise
    shutil.rmtree(previous, ignore_errors=True)
    return checks
