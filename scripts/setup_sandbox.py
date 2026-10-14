"""一次性安装代码运行沙箱（wasm 后端）：Deno + Pyodide + 常用包，之后完全离线运行。

    python scripts/setup_sandbox.py                 # 安装到默认目录（见下）
    python scripts/setup_sandbox.py --dir D:\\sandbox
    python scripts/setup_sandbox.py --check         # 只检查是否已安装、能否运行

默认目录：Windows 为 %LOCALAPPDATA%\\roundtable\\sandbox，其他系统为 ~/.cache/roundtable/sandbox
（可用环境变量 ROUNDTABLE_SANDBOX_DIR 或 roundtable.yaml 的 tools.python.runtime_dir 修改）。
运行时目录不在项目目录里：沙箱只被允许读取这个目录和每次运行的临时工作目录。

下载来源与校验：
- Deno：GitHub Releases，按 .sha256sum 文件校验；
- Pyodide 核心：npm 包；各个包的 wheel：先试 jsDelivr CDN（逐个下载），失败时改为下载 GitHub 上的
  完整发行包（约 340 MB）只解出需要的文件；都按 pyodide-lock.json 中的 sha256 校验；
- 纯 Python 附加包（openpyxl、python-docx 等）：PyPI，按 PyPI 公布的 sha256 校验。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roundtable.core.config import load_config  # noqa: E402
from roundtable.core.tools.sandbox import (  # noqa: E402
    RUNNER,
    WasmSandbox,
    default_runtime_dir,
)

DENO_VERSION = "2.6.0"
PYODIDE_VERSION = "314.0.7"
# 中文字体（图表与图片中的中文）：Noto Sans SC 子集 OTF，固定版本并校验哈希
FONT_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/"
    "Sans/SubsetOTF/SC/NotoSansSC-Regular.otf"
)
FONT_SHA256 = "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9"
CORE_FILES = ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip")


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str, timeout: float = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "roundtable-setup"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定的 https 地址
        return resp.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_sha256sum(text: bytes | str) -> str:
    """读出 .sha256sum 文件里的 SHA-256。Linux / macOS 是 `哈希  文件名`；Windows 版是
    PowerShell Get-FileHash 的输出（`Algorithm : SHA256` / `Hash      : 哈希` / `Path ...`），
    所以不能简单取第一个词，而是找 64 位十六进制串。"""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    found = re.findall(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])", text)
    if len(set(h.lower() for h in found)) != 1:
        raise SystemExit("无法从 .sha256sum 文件中读出唯一的校验值")
    return found[0].lower()


def deno_target() -> str:
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    if sys.platform == "win32":
        return f"{arch}-pc-windows-msvc"
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    return f"{arch}-unknown-linux-gnu"


def _deno_version(exe: Path) -> bool:
    import subprocess

    try:
        out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.stdout.startswith(f"deno {DENO_VERSION} ")


def install_deno(target_dir: Path, archive: Path | None) -> None:
    exe = "deno.exe" if sys.platform == "win32" else "deno"
    if (target_dir / exe).is_file() and archive is None and _deno_version(target_dir / exe):
        return  # 已安装同一版本
    name = f"deno-{deno_target()}.zip"
    base = f"https://github.com/denoland/deno/releases/download/v{DENO_VERSION}/{name}"
    if archive:
        data = archive.read_bytes()
    else:
        log(f"下载 Deno {DENO_VERSION} …")
        data = fetch(base, timeout=600)
        expected = parse_sha256sum(fetch(base + ".sha256sum"))
        if sha256(data) != expected:
            raise SystemExit("Deno 下载内容校验失败")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        (target_dir / exe).write_bytes(z.read(exe))
    (target_dir / exe).chmod(0o755)


def resolve(lock: dict, names: list[str]) -> list[str]:
    packages = lock["packages"]
    need: set[str] = set()
    stack = [n.lower() for n in names]
    while stack:
        name = stack.pop()
        if name in need:
            continue
        if name not in packages:
            raise SystemExit(f"Pyodide {PYODIDE_VERSION} 中没有包 {name}")
        need.add(name)
        stack.extend(packages[name].get("depends", []))
    return sorted(need)


def install_pyodide(target: Path, packages: list[str], archive: Path | None) -> dict:
    pyo = target / "pyodide"
    pyo.mkdir(parents=True, exist_ok=True)
    wanted: dict[str, str] = {}  # 文件名 → sha256
    if archive is None:
        log(f"下载 Pyodide {PYODIDE_VERSION} 核心 …")
        tgz = fetch(f"https://registry.npmjs.org/pyodide/-/pyodide-{PYODIDE_VERSION}.tgz", 600)
        with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as t:
            for m in t.getmembers():
                base = Path(m.name).name
                if m.isfile() and base in (*CORE_FILES, "pyodide-lock.json"):
                    (pyo / base).write_bytes(t.extractfile(m).read())  # type: ignore[union-attr]
        lock = json.loads((pyo / "pyodide-lock.json").read_text(encoding="utf-8"))
        for name in resolve(lock, packages):
            info = lock["packages"][name]
            wanted[info["file_name"]] = info["sha256"]
        missing = dict(wanted)
        cdn = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
        try:
            for file_name, digest in wanted.items():
                if (pyo / file_name).is_file() and sha256((pyo / file_name).read_bytes()) == digest:
                    missing.pop(file_name)
                    continue
                log(f"  下载 {file_name}")
                data = fetch(cdn + file_name)
                if sha256(data) != digest:
                    raise SystemExit(f"{file_name} 校验失败")
                (pyo / file_name).write_bytes(data)
                missing.pop(file_name)
        except OSError as exc:
            log(f"CDN 不可用（{exc}），改为下载完整发行包 …")
        if not missing:
            return lock
        url = (
            "https://github.com/pyodide/pyodide/releases/download/"
            f"{PYODIDE_VERSION}/pyodide-{PYODIDE_VERSION}.tar.bz2"
        )
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "pyodide.tar.bz2"
            with urllib.request.urlopen(url, timeout=1800) as resp, archive.open("wb") as f:  # noqa: S310
                shutil.copyfileobj(resp, f)
            _extract(archive, pyo, set(missing) | set(CORE_FILES))
    else:
        _extract(archive, pyo, set(CORE_FILES) | {"pyodide-lock.json"})
        lock = json.loads((pyo / "pyodide-lock.json").read_text(encoding="utf-8"))
        for name in resolve(lock, packages):
            info = lock["packages"][name]
            wanted[info["file_name"]] = info["sha256"]
        _extract(archive, pyo, set(wanted))
    for file_name, digest in wanted.items():
        if sha256((pyo / file_name).read_bytes()) != digest:
            raise SystemExit(f"{file_name} 校验失败")
    return lock


def _extract(archive: Path, dest: Path, names: set[str]) -> None:
    with tarfile.open(archive, "r:*") as t:
        for m in t:
            base = Path(m.name).name
            if m.isfile() and base in names:
                (dest / base).write_bytes(t.extractfile(m).read())  # type: ignore[union-attr]


_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def install_pure(target: Path, names: list[str], lock: dict) -> tuple[dict, list[str]]:
    """从 PyPI 下载纯 Python 包（py3-none-any）。依赖中 Pyodide 自带的包改为由 Pyodide 加载。"""
    wheels = target / "wheels"
    if wheels.exists():
        shutil.rmtree(wheels)
    wheels.mkdir()
    pyodide_names = {norm(n): n for n in lock["packages"]}
    imports: dict[str, list[str]] = {}
    needed_pyodide: set[str] = set()
    seen: set[str] = set()

    def visit(name: str) -> list[str]:
        """安装 name，返回它（递归）需要的 Pyodide 包。"""
        key = norm(name)
        if key in pyodide_names:
            needed_pyodide.add(pyodide_names[key])
            return [pyodide_names[key]]
        if key in seen:
            return []
        seen.add(key)
        meta = json.loads(fetch(f"https://pypi.org/pypi/{name}/json"))
        files = [u for u in meta["urls"] if u["filename"].endswith("-py3-none-any.whl")]
        files += [u for u in meta["urls"] if u["filename"].endswith("-py2.py3-none-any.whl")]
        if not files:
            raise SystemExit(f"{name} 没有纯 Python 的 wheel，不能在沙箱中使用")
        item = files[0]
        log(f"  下载 {item['filename']}")
        data = fetch(item["url"])
        if sha256(data) != item["digests"]["sha256"]:
            raise SystemExit(f"{item['filename']} 校验失败")
        (wheels / item["filename"]).write_bytes(data)
        deps: list[str] = []
        for req in meta["info"].get("requires_dist") or []:
            if ";" in req and ("extra" in req or "python_version <" in req):
                continue
            m = _REQ.match(req)
            if m:
                deps += visit(m.group(1))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            tops = {
                Path(n).parts[0].removesuffix(".py")
                for n in z.namelist()
                if not Path(n).parts[0].endswith((".dist-info", ".data"))
            }
        for top in tops:
            imports[top] = sorted(set(deps))
        return deps

    for name in names:
        visit(name)
    return imports, sorted(needed_pyodide)


def install_font(target: Path, archive: Path | None) -> None:
    fonts = target / "fonts"
    fonts.mkdir(exist_ok=True)
    dest = fonts / "cjk.otf"
    if dest.is_file() and sha256(dest.read_bytes()) == FONT_SHA256:
        return
    log("下载中文字体（Noto Sans SC，约 8 MB）…")
    data = archive.read_bytes() if archive else fetch(FONT_URL, timeout=600)
    if sha256(data) != FONT_SHA256:
        raise SystemExit("中文字体校验失败")
    dest.write_bytes(data)


def check(target: Path) -> int:
    rules = load_config().roundtable.tools.python
    sandbox = WasmSandbox(rules, target)
    reason = sandbox.unavailable_reason()
    if reason:
        log(f"✗ {reason}")
        return 1
    with tempfile.TemporaryDirectory(prefix="roundtable-check-") as tmp:
        job = Path(tmp)
        (job / "in").mkdir()
        (job / "out").mkdir()
        (job / "main.py").write_text(
            "import numpy, os\nprint('ok', numpy.__version__,"
            " 'cjk' if os.path.exists('/usr/share/fonts/roundtable/cjk.otf') else 'no-cjk')\n"
        )
        result = asyncio.run(sandbox.run(job))
    if result.ok and "no-cjk" in result.stdout:
        log("✗ 缺少中文字体（重新运行 python scripts/setup_sandbox.py 补装）")
        return 1
    if result.ok and result.stdout.startswith("ok"):
        took = f"{result.duration_s:.1f}s"
        log(f"✓ 代码运行环境可用（{target}）：{result.stdout.strip()}，用时 {took}")
        return 0
    log(f"✗ 运行失败：{result.stderr[-500:]}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="安装代码运行沙箱（Deno + Pyodide）")
    parser.add_argument("--dir", help="安装目录（默认见说明）")
    parser.add_argument("--check", action="store_true", help="只检查")
    parser.add_argument("--deno-archive", type=Path, help="使用已下载的 Deno zip")
    parser.add_argument("--pyodide-archive", type=Path, help="使用已下载的 Pyodide 完整发行包")
    parser.add_argument("--font-file", type=Path, help="使用已下载的 NotoSansSC-Regular.otf")
    args = parser.parse_args()
    rules = load_config().roundtable.tools.python
    target = (Path(args.dir) if args.dir else default_runtime_dir(rules.runtime_dir)).resolve()
    if args.check:
        return check(target)
    if target == ROOT or ROOT in target.parents:
        raise SystemExit("运行时目录不能放在项目目录里（沙箱会被允许读取它）")
    target.mkdir(parents=True, exist_ok=True)
    install_deno(target, args.deno_archive)
    lock = install_pyodide(target, [*rules.packages, "micropip"], args.pyodide_archive)
    log("下载纯 Python 附加包 …")
    imports, extra = install_pure(target, rules.pure_packages, lock)
    if extra:
        install_pyodide(target, [*rules.packages, "micropip", *extra], args.pyodide_archive)
    install_font(target, args.font_file)
    (target / "extras.json").write_text(json.dumps(imports, indent=1), encoding="utf-8")
    shutil.copyfile(RUNNER, target / "runner.mjs")
    log(f"已安装到 {target}")
    return check(target)


if __name__ == "__main__":
    sys.exit(main())
