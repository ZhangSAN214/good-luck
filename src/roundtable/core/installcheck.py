"""装后核对（docs/FIRSTAID.md）：start.bat / update_and_start.bat 装完以后逐项核对是不是真的生效，
不只看安装命令有没有报错。结果写到 ``data/install_check.json``，「出问题点这里」会显示。

核对的项：Python 版本、是不是在 .venv 里、圆桌是不是从这个项目目录装的（可编辑安装）、
pyproject.toml 里的依赖是不是都装上了且版本对、数据目录能不能写、.env 在不在、配置能不能读、
代码沙箱、8000 端口是不是空着。dsh 的安装核对在 scripts/setup_dsh.py 里，也写到同一个文件。

模块层只用标准库：依赖没装全时也要能跑出结果。
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

RECORD = "install_check.json"  # 和 core/firstaid.py 的 INSTALL_CHECK 一致
OK, WARN, BAD = "ok", "warn", "bad"
MIN_PYTHON = (3, 11)


@dataclass(frozen=True)
class Item:
    id: str
    ok: bool
    title: str
    detail: str = ""
    fix: str = ""
    level: str = OK  # 没过时：warn（能用但缺东西）/ bad（会起不来）

    def view(self) -> dict[str, Any]:
        return asdict(self)


def passed(id_: str, title: str, detail: str = "") -> Item:
    return Item(id_, True, title, detail)


def failed(id_: str, title: str, detail: str, fix: str = "", level: str = BAD) -> Item:
    return Item(id_, False, title, detail, fix, level)


def check_python(version: Sequence[int] = sys.version_info) -> Item:
    text = ".".join(str(v) for v in version[:3])
    if tuple(version[:2]) < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        return failed(
            "python", "Python 版本太旧", f"现在是 {text}，需要 {need} 以上", "装新版 Python"
        )
    return passed("python", "Python 版本", text)


def check_venv(prefix: str = sys.prefix, base: str = sys.base_prefix) -> Item:
    if os.path.realpath(prefix) == os.path.realpath(base):
        return failed(
            "venv",
            "没在 .venv 里运行",
            "装的包会进系统 Python，可能和别的程序打架",
            "用 start.bat 启动（它会建并激活 .venv）",
            WARN,
        )
    return passed("venv", "在 .venv 里运行", prefix)


def check_editable(root: Path) -> Item:
    """圆桌要从这个项目目录装（pip install -e .），否则更新了代码也不生效。"""
    try:
        import roundtable
    except ImportError as exc:
        return failed("editable", "圆桌没装上", str(exc), 'pip install -e ".[dev]"')
    where = Path(roundtable.__file__).resolve()
    src = (root / "src").resolve()
    if src not in where.parents:
        return failed(
            "editable",
            "用的不是这个目录里的圆桌",
            f"实际加载的是 {where.parent}",
            '在项目目录里重新 pip install -e ".[dev]"',
        )
    return passed("editable", "圆桌从这个项目目录加载")


def requirements(pyproject: Path, extras: Iterable[str]) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    out = list(project.get("dependencies", []))
    optional = project.get("optional-dependencies", {})
    for extra in extras:
        out += optional.get(extra, [])
    return out


def check_requirements(reqs: Iterable[str]) -> list[Item]:
    """每个依赖都装上了、版本对得上（平台标记不符的跳过）。"""
    try:
        from packaging.requirements import InvalidRequirement, Requirement
    except ImportError:  # pragma: no cover - pip 自带 packaging，几乎不会走到
        return [failed("deps", "核对不了依赖", "缺少 packaging", "pip install packaging", WARN)]
    missing, wrong, total = [], [], 0
    for text in reqs:
        try:
            req = Requirement(text)
        except InvalidRequirement:
            continue
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        total += 1
        try:
            got = metadata.version(req.name)
        except metadata.PackageNotFoundError:
            missing.append(req.name)
            continue
        if req.specifier and not req.specifier.contains(got, prereleases=True):
            wrong.append(f"{req.name} {got}（要 {req.specifier}）")
    items = []
    if missing:
        items.append(
            failed(
                "deps_missing",
                f"有 {len(missing)} 个依赖没装上",
                "、".join(missing),
                "重新运行 start.bat；还不行就看 pip 的报错",
            )
        )
    if wrong:
        items.append(
            failed("deps_version", "有依赖版本不对", "、".join(wrong), "重新运行 start.bat", WARN)
        )
    if not items:
        items.append(passed("deps", "依赖都装上了", f"{total} 个"))
    return items


def check_writable(folder: Path) -> Item:
    probe = folder / ".write_test"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return failed(
            "data_dir", "数据目录写不进去", f"{folder}：{exc}", "检查磁盘空间和文件夹权限"
        )
    return passed("data_dir", "数据目录能写", str(folder))


def check_env_file(root: Path) -> Item:
    if not (root / ".env").is_file():
        return failed(
            "env",
            "没有 .env",
            "成员没有 key 就调用不了模型",
            "把 .env.example 复制成 .env，至少填一个 key",
            WARN,
        )
    return passed("env", ".env 在")


def check_config() -> Item:
    try:
        from roundtable.core.config import load_config

        load_config()
    except Exception as exc:  # noqa: BLE001 - 任何错误都要报给用户
        return failed("config", "配置读不了", str(exc)[:300], "按提示改回出错的那一行")
    return passed("config", "配置能读")


def check_sandbox() -> Item:
    try:
        from roundtable.core.config import load_config
        from roundtable.core.tools import pick_sandbox

        reason = pick_sandbox(load_config().roundtable.tools.python)[1]
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)
    if reason:
        return failed(
            "sandbox",
            "代码沙箱不可用",
            reason[:300],
            "python scripts/setup_sandbox.py（不装也行，成员只是不能跑代码）",
            WARN,
        )
    return passed("sandbox", "代码沙箱可用")


def check_port(host: str = "127.0.0.1", port: int = 8000) -> Item:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return failed(
                "port",
                f"{port} 端口被占着",
                "旧的圆桌没关掉，或者别的程序在用",
                "运行 stop.bat；还不行就关掉占着端口的程序",
                WARN,
            )
    return passed("port", f"{port} 端口空着")


def run_checks(
    root: Path, data: Path, extras: Iterable[str], *, port: int | None = 8000
) -> list[Item]:
    items = [check_python(), check_venv(), check_editable(root)]
    items += check_requirements(requirements(root / "pyproject.toml", extras))
    items += [check_writable(data), check_env_file(root), check_config(), check_sandbox()]
    if port:
        items.append(check_port(port=port))
    return items


def save_record(data: Path, source: str, items: Sequence[Any], now: datetime) -> Path:
    """写到 install_check.json 的 source 一节（start / update / dsh），其他节不动。"""
    path = data / RECORD
    record: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            record = loaded
    record[source] = {
        "at": now.isoformat(timespec="seconds"),
        "items": [i.view() if hasattr(i, "view") else dict(i) for i in items],
    }
    data.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def print_items(items: Sequence[Item], say: Any = print) -> None:
    for i in items:
        mark = "✓" if i.ok else ("✗" if i.level == BAD else "!")
        line = f"  {mark} {i.title}"
        if i.detail and not i.ok:
            line += f"：{i.detail}"
        say(line)
        if i.fix and not i.ok:
            say(f"      怎么办：{i.fix}")
