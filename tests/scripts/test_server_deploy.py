"""云服务器部署（deploy/server/、scripts/pack_server_data.py）的测试。不联网。"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "server"
SCRIPT = ROOT / "scripts" / "pack_server_data.py"
spec = importlib.util.spec_from_file_location("pack_server_data", SCRIPT)
pack_server_data = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pack_server_data
spec.loader.exec_module(pack_server_data)


def test_service_only_opens_to_tailscale():
    unit = (DEPLOY / "roundtable.service").read_text(encoding="utf-8")
    assert "Environment=ROUNDTABLE_LAN=tailscale" in unit
    assert "User=roundtable" in unit  # 不用 root 跑
    assert "--port 8000" in unit


def test_install_never_opens_port_publicly_and_never_touches_keys():
    script = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "ufw allow in on tailscale0" in script
    assert "ufw allow 8000" not in script
    assert "API_KEY=" not in script  # key 只由用户在服务器上填
    assert "chmod 600" in script
    assert "[dev]" not in script  # 服务器不装测试依赖和浏览器


def test_scripts_use_lf_line_endings():
    for name in ("install.sh", "update.sh", "roundtable.service"):
        assert b"\r\n" not in (DEPLOY / name).read_bytes(), name


def test_pack_takes_db_snapshot_and_skips_private_dirs(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    con = sqlite3.connect(data / "roundtable.db")
    con.execute("create table t (x)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()
    (data / "roundtable.db-wal").write_bytes(b"x")
    (data / "lan_access.json").write_text("{}", encoding="utf-8")
    for keep in ("avatars/a.png", "stickers/s.png", "chat_media/m.jpg", "growth_backups/g.json"):
        (data / keep).parent.mkdir(parents=True, exist_ok=True)
        (data / keep).write_bytes(b"k")
    for skip in ("_chatgpt/cookies", "mail_browser/x", "wander/w", "dsh/d", "logs/l.txt"):
        (data / skip).parent.mkdir(parents=True, exist_ok=True)
        (data / skip).write_bytes(b"s")

    out = tmp_path / "pack.zip"
    assert pack_server_data.main(["--data", str(data), "-o", str(out)]) == 0
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        zf.extract("data/roundtable.db", tmp_path / "x")
    assert names == {
        "data/roundtable.db",
        "data/avatars/a.png",
        "data/stickers/s.png",
        "data/chat_media/m.jpg",
        "data/growth_backups/g.json",
    }
    snap = sqlite3.connect(tmp_path / "x" / "data" / "roundtable.db")
    assert snap.execute("select x from t").fetchall() == [(42,)]
    snap.close()


def test_pack_missing_data_dir(tmp_path, capsys):
    assert pack_server_data.main(["--data", str(tmp_path / "nope")]) == 1
    assert "找不到" in capsys.readouterr().err
