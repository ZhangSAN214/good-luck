import plistlib
from pathlib import Path

from roundtable.desktop import install


def test_two_entries_and_only_the_second_opens_lan():
    assert [e.args for e in install.ENTRIES] == [(), ("--lan",)]


def test_mac_app_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(install.sys, "platform", "darwin")
    root = Path("/Users/me/good luck")
    app = install.build_mac_app(root, tmp_path, install.ENTRIES[1], 1, None)
    assert app.name == "圆桌（平板可连）.app"
    exe = app / "Contents" / "MacOS" / "roundtable"
    script = exe.read_text(encoding="utf-8")
    assert exe.stat().st_mode & 0o111
    assert 'cd "/Users/me/good luck"' in script
    assert '"/Users/me/good luck/.venv/bin/python" -m roundtable.desktop --lan "$@"' in script
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "roundtable"
    assert info["CFBundleIdentifier"] == "local.roundtable.desktop.1"
    assert info["NSAppTransportSecurity"] == {"NSAllowsLocalNetworking": True}
    assert "CFBundleIconFile" not in info


def test_mac_app_rebuild_replaces_old_bundle(tmp_path):
    root = tmp_path / "repo"
    icns = tmp_path / "x.icns"
    icns.write_bytes(b"icns")
    first = install.build_mac_app(root, tmp_path, install.ENTRIES[0], 0, None)
    (first / "stale").write_text("old")
    again = install.build_mac_app(root, tmp_path, install.ENTRIES[0], 0, icns)
    assert not (again / "stale").exists()
    assert (again / "Contents" / "Resources" / "roundtable.icns").read_bytes() == b"icns"
    info = plistlib.loads((again / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "local.roundtable.desktop"
    assert info["CFBundleIconFile"] == "roundtable.icns"


def test_windows_shortcut_uses_pythonw_without_console(monkeypatch, tmp_path):
    monkeypatch.setattr(install.sys, "platform", "win32")
    root = Path("E:/good-luck")
    env = install.shortcut_env(root, tmp_path / "圆桌.lnk", install.ENTRIES[1], None)
    assert env["RT_TARGET"].endswith("pythonw.exe")
    assert env["RT_ARGS"] == "-m roundtable.desktop --lan"
    assert env["RT_CWD"] == str(root)
    assert env["RT_ICON"] == ""


def test_ico_from_app_icon(tmp_path):
    png = Path(__file__).resolve().parents[2] / "web" / "img" / "app" / "icon-512.png"
    out = install.make_ico(png, tmp_path / "a.ico")
    assert out is not None and out.read_bytes()[:4] == b"\x00\x00\x01\x00"
