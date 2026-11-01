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


def _fake_npm(paths, version):
    """假装 npm 装好了某个版本的运行时。"""

    def make():
        paths.bin_js.parent.mkdir(parents=True, exist_ok=True)
        paths.bin_js.write_text("//", encoding="utf-8")
        (paths.bin_js.parent.parent / "package.json").write_text(
            f'{{"version": "{version}"}}', encoding="utf-8"
        )

    return make


def _good_fetch(cat):
    payload = b"tarball"

    def fetch(plugin, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return dest

    sha = hashlib.sha256(payload).hexdigest()
    plugins = [p.model_copy(update={"sha256": sha}) for p in cat.plugins]
    return cat.model_copy(update={"plugins": plugins}), fetch


def _runner(paths, version):
    base = fake_run({"node": "v22.1.0", "npm": "10.0.0", "pnpm": "10.2.0"})
    make = _fake_npm(paths, version)

    def run(argv, **kw):
        if "install" in argv:
            make()
        return base(argv, **kw)

    return run


def test_install_safely_verifies_everything(tmp_path, tools):
    cat, fetch = _good_fetch(load_catalog(REPO_CATALOG))
    paths = DshPaths(tmp_path / "dsh", cat.runtime.package)
    run = _runner(paths, cat.runtime.version)
    checks = inst.install_safely(cat, paths, {}, run=run, fetch=fetch, say=lambda s: None)
    assert checks and all(c.ok for c in checks)
    assert {c.name for c in checks} >= {"node", "runtime", "runtime_version", "plugin_worktable"}
    assert inst.runtime_version(paths) == cat.runtime.version
    assert not (paths.root / inst.PREVIOUS).exists()


def test_failed_check_rolls_back_to_previous_runtime(tmp_path, tools):
    cat, fetch = _good_fetch(load_catalog(REPO_CATALOG))
    paths = DshPaths(tmp_path / "dsh", cat.runtime.package)
    # 已经装着一版能用的
    inst.install_safely(
        cat, paths, {}, run=_runner(paths, cat.runtime.version), fetch=fetch, say=lambda s: None
    )
    marker = paths.runtime / "old-runtime.txt"
    marker.write_text("旧的", encoding="utf-8")
    # 重装时 npm 装成了错的版本：核对没过 → 退回旧的那版
    said = []
    with pytest.raises(inst.InstallError, match="装完核对没过") as err:
        inst.install_safely(
            cat, paths, {}, run=_runner(paths, "0.0.1"), fetch=fetch, say=said.append
        )
    assert marker.read_text(encoding="utf-8") == "旧的"
    assert inst.runtime_version(paths) == cat.runtime.version
    assert any(not c.ok and c.name == "runtime_version" for c in err.value.checks)
    assert "已退回到安装之前的样子。" in said


def test_failed_fresh_install_leaves_nothing_behind(tmp_path, tools):
    cat = load_catalog(REPO_CATALOG)
    paths = DshPaths(tmp_path / "dsh", cat.runtime.package)

    def bad_fetch(plugin, dest):
        raise inst.InstallError("哈希对不上")

    with pytest.raises(inst.InstallError):
        inst.install_safely(
            cat,
            paths,
            {},
            run=_runner(paths, cat.runtime.version),
            fetch=bad_fetch,
            say=lambda s: None,
        )
    assert not paths.runtime.exists()


def test_verify_without_install(tmp_path, tools):
    cat = load_catalog(REPO_CATALOG)
    paths = DshPaths(tmp_path / "dsh", cat.runtime.package)
    checks = {c.name: c for c in inst.verify(cat, paths, fake_run({"node": "v22.1.0"}))}
    assert not checks["runtime"].ok and not checks["runtime_version"].ok
    assert not checks["plugin_worktable"].ok
