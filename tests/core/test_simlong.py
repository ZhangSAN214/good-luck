"""半年快进（成长 G3）：假模型每周都想把 8 位往同一个方向推、尽量引用你说的话，快进 180 天后
防退化指标全部正常——以后每次改动都跑这一条，护栏被改松了这里会失败。"""

from __future__ import annotations

import io
import json

import pytest

from roundtable.cli import run
from roundtable.core.runtime import DEFAULT_DB
from roundtable.core.simlab import SimError
from roundtable.core.simlong import LongRun


def test_refuses_data_dir_and_unknown_pressure(tmp_path):
    with pytest.raises(SimError, match="data/"):
        LongRun(DEFAULT_DB.parent / "long_x", days=1)
    with pytest.raises(SimError, match="施压"):
        LongRun(tmp_path / "a", days=1, pressure="hard")


async def test_half_year_under_pressure_stays_healthy(tmp_path):
    out = tmp_path / "half"
    res = await LongRun(out, days=180, seed=0).run()
    assert res.days == 180 and not res.errors, res.errors
    assert res.problems == [], res.problems  # 护栏挡住了：不撞型、不走极端、不被带偏、口癖还在
    v = res.vitals
    assert len(res.weekly) >= 25  # 每周一都反思了
    assert v["user_share"]["changes"] > 8  # 真的改了东西（不是全拦下了才「正常」）
    assert v["spread"]["min"] >= 2
    assert all(abs(m) <= 1.0 for m in v["pushed"].values())
    assert all(x["kaomoji"] >= 2 for x in v["lingo_left"].values())
    assert any(n for n in v["moved"].values())  # 性格确实会变，只是有边
    report = json.loads((out / "report.json").read_text("utf-8"))
    assert report["problems"] == [] and report["base"] and report["end"]
    md = (out / "report.md").read_text("utf-8")
    assert "半年后的她们" in md and "全部指标正常" in md and "每周反思" in md


async def test_cli_sim_growth(tmp_path):
    buf = io.StringIO()
    code = await run(["sim-growth", "--days", "8", "--out", str(tmp_path / "g")], out=buf)
    assert code == 0 and "全部指标正常" in buf.getvalue() and "report.md" in buf.getvalue()
    buf = io.StringIO()
    code = await run(["sim-growth", "--out", str(DEFAULT_DB.parent / "x")], out=buf)
    assert code == 2 and "data/" in buf.getvalue()
