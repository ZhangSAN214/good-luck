from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import httpx
import pytest

import roundtable.core.dsh.install as inst
from roundtable.core.dsh.catalog import load_catalog
from roundtable.core.dsh.paths import DshPaths

REPO_CATALOG = Path(__file__).resolve().parents[3] / "config" / "dsh_plugins.yaml"


def fake_run(versions, calls=None):
    def run(argv, **kw):
        if calls is not None:
            calls.append((argv, kw))
        name = Path(argv[0]).name
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, versions.get(name, ""), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(inst.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_node_major():
    assert inst.node_major("v22.22.0") == 22
    assert inst.node_major("garbage") is None


def test_check_tools(tools):
    cat = load_catalog(REPO_CATALOG)
    ok = inst.check_tools(cat, fake_run({"node": "v22.1.0", "npm": "10.0.0", "pnpm": "10.2.0"}))
    assert all(c.ok for c in ok)
    old = inst.check_tools(cat, fake_run({"node": "v18.0.0", "npm": "9", "pnpm": ""}))
    assert [c.ok for c in old] == [False, True, False]
    assert "太旧" in old[0].detail and "npm install -g pnpm" in old[2].detail


def test_download_checks_hash(tmp_path):
    cat = load_catalog(REPO_CATALOG)
    plugin = cat.enabled[0]
    payload = b"tarball"
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=payload))
    )
    with pytest.raises(inst.InstallError, match="哈希对不上"):
        inst.download(plugin, tmp_path / "x.tgz", client=client)
    assert not (tmp_path / "x.tgz").exists()  # 不对就删掉
    good = plugin.model_copy(update={"sha256": hashlib.sha256(payload).hexdigest()})
    assert inst.download(good, tmp_path / "x.tgz", client=client).read_bytes() == payload


def test_install_runs_npm_then_adds_plugins(tmp_path, tools):
    cat = load_catalog(REPO_CATALOG)
    paths = DshPaths(tmp_path / "dsh", cat.runtime.package)
    calls = []
    run = fake_run({"node": "v22.1.0", "npm": "10.0.0", "pnpm": "10.2.0"}, calls)

    def run_and_create(argv, **kw):
        out = run(argv, **kw)
        if "install" in argv:  # 假装 npm 装好了
            paths.bin_js.parent.mkdir(parents=True, exist_ok=True)
            paths.bin_js.write_text("//", encoding="utf-8")
        return out

    fetched = []

    def fetch(plugin, dest):
        fetched.append(plugin.id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")
        return dest

    inst.install(
        cat,
        paths,
        {"DSH_HOME": str(paths.home)},
        run=run_and_create,
        fetch=fetch,
        say=lambda s: None,
    )
    real = [c for c in calls if c[0][1:] != ["--version"]]
    npm_argv = real[0][0]
    assert (
        f"{cat.runtime.package}@{cat.runtime.version}" in npm_argv
        and str(paths.runtime) in npm_argv
    )
    add_argv, kw = real[1]
    assert add_argv[2:6] == ["plugin", "--profile", "web", "add"] and add_argv[6].startswith(
        "file:"
    )
    assert kw["env"] == {"DSH_HOME": str(paths.home)}  # 装插件时不给任何 key
    assert fetched == ["worktable"]


def test_install_stops_when_tools_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(inst.shutil, "which", lambda name: None)
    cat = load_catalog(REPO_CATALOG)
    with pytest.raises(inst.InstallError, match="没装 Node"):
        inst.install(cat, DshPaths(tmp_path, cat.runtime.package), {}, run=fake_run({}))
