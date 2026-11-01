from __future__ import annotations

from roundtable.core.config.schema import SuperWorker
from roundtable.core.supercomputer.launch import CommandLauncher, HookLauncher

KEY = "sk-" + "x" * 20


def test_command_launcher_drops_roundtable_keys():
    w = SuperWorker(id="a", label="A", argv=["agent", "exec", "-"])
    env = {
        "PATH": "/bin",
        "HOME": "/home/u",
        "OPENROUTER_API_KEY": KEY,
        "deepseek_api_key": KEY,
        "ANTHROPIC_API_KEY": KEY,  # 有它命令行会改走按量计费，不用订阅
        "SECRET_THING": "x",
    }
    lau = CommandLauncher(
        drop_env=["SECRET_THING"],
        environ=env,
        which=lambda name: f"/usr/bin/{name}",
    )
    assert lau.blocked(w) is None
    launch = lau.launch(w)
    assert launch.argv == ["/usr/bin/agent", "exec", "-"]
    assert launch.env == {"PATH": "/bin", "HOME": "/home/u"}  # 助手用自己的登录，不拿圆桌的 key


def test_command_launcher_missing_tool():
    w = SuperWorker(id="a", label="A", argv=["agent"])
    lau = CommandLauncher(which=lambda name: None)
    assert "没找到 agent" in lau.blocked(w)


def test_command_launcher_finds_user_install(tmp_path, monkeypatch):
    # Claude Code 的安装器装到 ~/.local/bin，但不一定加进 PATH
    folder = tmp_path / ".local" / "bin"
    folder.mkdir(parents=True)
    (folder / "agent.exe").write_bytes(b"")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    w = SuperWorker(id="a", label="A", argv=["agent", "-p"])
    lau = CommandLauncher(environ={})
    assert lau.blocked(w) is None
    assert lau.launch(w).argv == [str(folder / "agent.exe"), "-p"]


async def test_hook_launcher():
    calls = []

    async def before():
        calls.append("before")

    async def after():
        calls.append("after")

    w = SuperWorker(id="d", label="D", via="dsh")
    lau = HookLauncher(lambda: None, lambda: (["node", "x"], {"K": "v"}, ["v"]), before, after)
    assert lau.blocked(w) is None
    launch = lau.launch(w)
    assert launch.argv == ["node", "x"] and launch.secrets == ["v"]
    await lau.before(w)
    await lau.after(w)
    assert calls == ["before", "after"]
