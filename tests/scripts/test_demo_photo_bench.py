"""装样例修图台的脚本：生成应用外壳、照搬功能文件、写 build.json（不调模型、不联网）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "demo_photo_bench", ROOT / "scripts" / "demo_photo_bench.py"
)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)  # type: ignore[union-attr]


def test_installs_the_sample_photo_bench(tmp_path, capsys):
    folder = demo.main(["--data", str(tmp_path)])
    assert folder == tmp_path / "bench" / "demo-photo"
    app = json.loads((folder / "app.json").read_text("utf-8"))
    assert app["base"] == "editor" and app["checks"]["undoMin"] == 20
    assert {p.name for p in (folder / "windows").iterdir()} == {
        "adjust.js",
        "crop.js",
        "background.js",
    }
    assert "createEditor" in (folder / "index.html").read_text("utf-8")
    assert json.loads((folder / "build.json").read_text("utf-8"))["id"] == "demo-photo"
    assert "/bench/demo-photo/index.html" in capsys.readouterr().out
