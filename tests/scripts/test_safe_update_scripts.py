"""scripts/safe_update.py 与 scripts/install_check.py 的命令行入口。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from roundtable.core.storage import connect

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_install_check_writes_record(tmp_path, monkeypatch, capsys):
    mod = load("install_check")
    monkeypatch.setenv(mod.DB_ENV, str(tmp_path / "rt.db"))
    code = mod.main(["--source", "update", "--extras", "dev", "--port", "0"])
    assert code in (0, 1)
    assert "装后核对" in capsys.readouterr().out
    data = json.loads((tmp_path / "install_check.json").read_text(encoding="utf-8"))
    assert list(data) == ["update"]


def test_safe_update_smoke_and_undo_entry(tmp_path, monkeypatch, capsys):
    mod = load("safe_update")
    db = tmp_path / "rt.db"
    connect(db).close()
    monkeypatch.setenv(mod.DB_ENV, str(db))
    assert mod.db_path() == db
    assert mod.main(["smoke", "--db", str(db)]) == 0
    assert "SMOKE OK" in capsys.readouterr().out
    assert mod.main(["undo"]) == 1  # 没有通过的更新可撤销
    assert "没有可以撤销的更新" in capsys.readouterr().out


def test_real_smoke_on_a_copy(tmp_path):
    mod = load("safe_update")
    db = tmp_path / "rt.db"
    connect(db).close()
    assert mod.run_smoke(db) == []
    assert mod.smoke_main([]) == 2
