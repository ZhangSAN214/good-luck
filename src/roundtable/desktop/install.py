"""生成桌面版的图标入口（scripts/install_desktop.py，由 install_desktop.bat / .command 调用）。

- Windows：桌面和开始菜单各放两个快捷方式「圆桌」「圆桌（平板可连）」，指向 .venv 里的 pythonw.exe
  （没有命令行窗口）。快捷方式用系统自带的 PowerShell + WScript.Shell 生成，不需要额外依赖。
- macOS：在 ~/Applications 生成「圆桌.app」「圆桌（平板可连）.app」。
  .app 只是一个指向本机代码目录的小外壳，在本机生成、不是从网上下载的，所以不需要苹果签名和公证。

只生成入口，不复制代码：代码照旧在仓库目录里，更新照旧 git pull。
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "圆桌"
BUNDLE_ID = "local.roundtable.desktop"


@dataclass(frozen=True)
class Entry:
    name: str
    args: tuple[str, ...]
    comment: str


ENTRIES = (
    Entry(APP_NAME, (), "打开圆桌（只有这台电脑能用）"),
    Entry(f"{APP_NAME}（平板可连）", ("--lan",), "打开圆桌，并让家里 Wi-Fi 下的平板 / 手机连进来"),
)


def venv_python(root: Path, windowed: bool) -> Path:
    if sys.platform == "win32":
        return root / ".venv" / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    return root / ".venv" / "bin" / "python"


# ---------- 图标 ----------


def make_ico(png: Path, out: Path) -> Path | None:
    """PNG → .ico（需要 pillow；没有就用默认图标）。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    Image.open(png).convert("RGBA").save(out, format="ICO", sizes=sizes)
    return out


def make_icns(png: Path, out: Path) -> Path | None:
    """PNG → .icns（用 macOS 自带的 sips 与 iconutil；不是 macOS 时返回 None）。"""
    if not (shutil.which("sips") and shutil.which("iconutil")):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                suffix = "@2x" if scale == 2 else ""
                target = iconset / f"icon_{size}x{size}{suffix}.png"
                cmd = ["sips", "-z", str(px), str(px), str(png), "--out", str(target)]
                subprocess.run(cmd, check=True, capture_output=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    return out


# ---------- macOS ----------


def mac_launcher_script(root: Path, args: tuple[str, ...]) -> str:
    python = venv_python(root, windowed=True)
    extra = "".join(f" {a}" for a in args)
    return (
        "#!/bin/sh\n"
        "# 圆桌桌面版入口（由 scripts/install_desktop.py 生成）\n"
        f'cd "{root}" || exit 1\n'
        f'exec "{python}" -m roundtable.desktop{extra} "$@"\n'
    )


def mac_info_plist(entry: Entry, index: int, has_icon: bool) -> bytes:
    info = {
        "CFBundleName": entry.name,
        "CFBundleDisplayName": entry.name,
        "CFBundleIdentifier": f"{BUNDLE_ID}{'' if index == 0 else f'.{index}'}",
        "CFBundleExecutable": "roundtable",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        # 本机 http://127.0.0.1 与局域网地址
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    }
    if has_icon:
        info["CFBundleIconFile"] = "roundtable.icns"
    return plistlib.dumps(info)


def build_mac_app(root: Path, dest: Path, entry: Entry, index: int, icns: Path | None) -> Path:
    app = dest / f"{entry.name}.app"
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "roundtable"
    exe.write_text(mac_launcher_script(root, entry.args), encoding="utf-8")
    exe.chmod(0o755)
    if icns is not None:
        res = app / "Contents" / "Resources"
        res.mkdir(parents=True)
        shutil.copy2(icns, res / "roundtable.icns")
    info = mac_info_plist(entry, index, icns is not None)
    (app / "Contents" / "Info.plist").write_bytes(info)
    return app


# ---------- Windows ----------

# 路径与名字经环境变量传入，不拼进命令行（中文和空格都不用转义）
_PS_SHORTCUT = r"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($env:RT_LNK)
$s.TargetPath = $env:RT_TARGET
$s.Arguments = $env:RT_ARGS
$s.WorkingDirectory = $env:RT_CWD
$s.Description = $env:RT_DESC
if ($env:RT_ICON) { $s.IconLocation = $env:RT_ICON }
$s.Save()
"""

_PS_FOLDER = "[Environment]::GetFolderPath($env:RT_FOLDER)"


def _powershell(script: str, env: dict[str, str]) -> str:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    out = subprocess.run(
        cmd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(f"PowerShell 出错：{out.stderr.strip()[-800:]}")
    return out.stdout.strip()


def windows_folders() -> list[Path]:
    """桌面（含 OneDrive 重定向）与开始菜单「程序」。"""
    return [Path(_powershell(_PS_FOLDER, {"RT_FOLDER": n})) for n in ("Desktop", "Programs")]


def shortcut_env(root: Path, lnk: Path, entry: Entry, icon: Path | None) -> dict[str, str]:
    return {
        "RT_LNK": str(lnk),
        "RT_TARGET": str(venv_python(root, windowed=True)),
        "RT_ARGS": " ".join(("-m", "roundtable.desktop", *entry.args)),
        "RT_CWD": str(root),
        "RT_DESC": entry.comment,
        "RT_ICON": str(icon) if icon else "",
    }


def build_windows_shortcuts(root: Path, folders: list[Path], icon: Path | None) -> list[Path]:
    made = []
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        for i, entry in enumerate(ENTRIES):
            # WScript.Shell 按系统代码页处理文件名，中文名可能存不了：先存英文名，再由 Python 改名
            tmp = folder / f"roundtable-{i}.lnk"
            lnk = folder / f"{entry.name}.lnk"
            _powershell(_PS_SHORTCUT, shortcut_env(root, tmp, entry, icon))
            os.replace(tmp, lnk)
            made.append(lnk)
    return made


# ---------- 入口 ----------


def install(root: Path, dest: Path | None = None) -> list[Path]:
    """生成入口，返回生成的文件。

    dest 为空时放到系统默认位置（Windows 桌面 + 开始菜单；macOS ~/Applications）。
    """
    png = root / "web" / "img" / "app" / "icon-512.png"
    icons = root / "data" / "desktop"
    if sys.platform == "win32":
        icon = make_ico(png, icons / "roundtable.ico")
        folders = [dest] if dest else windows_folders()
        return build_windows_shortcuts(root, folders, icon)
    if sys.platform == "darwin":
        icns = make_icns(png, icons / "roundtable.icns")
        target = dest or Path.home() / "Applications"
        target.mkdir(parents=True, exist_ok=True)
        return [build_mac_app(root, target, e, i, icns) for i, e in enumerate(ENTRIES)]
    raise SystemExit("桌面图标只支持 Windows 和 macOS；Linux 直接运行 python -m roundtable.desktop")
