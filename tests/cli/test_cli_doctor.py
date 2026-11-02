"""roundtable doctor：不建运行时也能跑（圆桌起不来时用）；--report / -o 输出体检报告。"""

from __future__ import annotations

import io

from roundtable.cli import run
from roundtable.core.runtime import DB_ENV
from roundtable.core.storage import connect


async def _doctor(*argv):
    out = io.StringIO()
    code = await run(["doctor", *argv], out=out)
    return code, out.getvalue()


async def test_doctor_without_runtime(tmp_path, monkeypatch):
    db = tmp_path / "rt.db"
    connect(db).close()
    monkeypatch.setenv(DB_ENV, str(db))
    code, text = await _doctor()
    assert code in (0, 1)
    assert "圆桌自检" in text and "数据库完好" in text and "还没有任何备份" in text
    code, text = await _doctor("--report")
    assert text.startswith("# 圆桌体检报告")
    target = tmp_path / "报告.txt"
    code, text = await _doctor("-o", str(target))
    assert "已存到" in text and "圆桌体检报告" in target.read_text(encoding="utf-8")


async def test_doctor_reports_broken_database(tmp_path, monkeypatch):
    db = tmp_path / "rt.db"
    db.write_bytes(b"garbage" * 500)
    monkeypatch.setenv(DB_ENV, str(db))
    code, text = await _doctor()
    assert code == 1 and "数据库打不开" in text
