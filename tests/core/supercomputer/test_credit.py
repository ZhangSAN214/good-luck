from __future__ import annotations

from roundtable.core.config.schema import SuperWorker
from roundtable.core.providers.secrets import Secret
from roundtable.core.supercomputer.credit import CreditLauncher
from roundtable.core.supercomputer.launch import CommandLauncher

KEY = "sk-ant-" + "y" * 24
OTHER = "sk-or-v1-" + "z" * 24


def api_worker(**kw):
    return SuperWorker(
        id="api", label="API", via="credit", credit_channel="anthropic", argv=["agent"], **kw
    )


def base():
    env = {"PATH": "/bin", "ANTHROPIC_API_KEY": "from-shell", "OPENROUTER_API_KEY": OTHER}
    return CommandLauncher(environ=env, which=lambda name: f"/usr/bin/{name}")


def test_injects_only_that_channels_key():
    lau = CreditLauncher(base(), {"anthropic": ("ANTHROPIC_API_KEY", Secret(KEY))})
    w = api_worker()
    assert lau.blocked(w) is None
    launch = lau.launch(w)
    assert launch.argv == ["/usr/bin/agent"]
    assert launch.env == {"PATH": "/bin", "ANTHROPIC_API_KEY": KEY}  # 别的 key 照样不给
    assert KEY in launch.secrets  # 输出里出现 key 会被替换成 ***


def test_blocked_without_key_or_when_capped():
    w = api_worker()
    none = CreditLauncher(base(), {"anthropic": ("ANTHROPIC_API_KEY", None)})
    assert "ANTHROPIC_API_KEY" in (none.blocked(w) or "")
    assert none.launch(w).env == {"PATH": "/bin"}
    capped = CreditLauncher(
        base(),
        {"anthropic": ("ANTHROPIC_API_KEY", Secret(KEY))},
        gate=lambda ch: "本月已花约 $100，到了每月上限 $100" if ch == "anthropic" else None,
    )
    assert "每月上限" in (capped.blocked(w) or "")

    def broken(ch):
        raise OSError("账本读不到")

    ok = CreditLauncher(base(), {"anthropic": ("ANTHROPIC_API_KEY", Secret(KEY))}, gate=broken)
    assert ok.blocked(w) is None


def test_missing_tool_reported_first():
    lau = CreditLauncher(
        CommandLauncher(environ={}, which=lambda name: None),
        {"anthropic": ("ANTHROPIC_API_KEY", Secret(KEY))},
    )
    assert "agent" in (lau.blocked(api_worker()) or "")
