"""把测试里的样例修图台装进本机圆桌，方便在浏览器里看（docs/KIT.md「K 通用底座」）。

不调模型、不联网、不跑生成器：按 tests/web/photo_bench/ 的规格生成应用外壳，照搬三个功能文件，
放进 data/bench/demo-photo/。之后启动圆桌，打开 http://127.0.0.1:8000/bench/demo-photo/index.html
（加 ?selftest=1 看企业级验收的自检结果）。

    python scripts/demo_photo_bench.py [--data data]
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from roundtable.core.benchkit.spec import normalize, write_shell
from roundtable.core.config.loader import load_config

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "web" / "photo_bench"
BID = "demo-photo"


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description="装样例修图台")
    ap.add_argument("--data", default=str(ROOT / "data"), help="圆桌的数据目录（默认 data）")
    args = ap.parse_args(argv)
    apps = load_config(ROOT / "config").roundtable.bench.apps
    raw = json.loads((SAMPLE / "plan.json").read_text(encoding="utf-8"))
    spec = normalize(raw, max_windows=8, kinds={raw["kind"]}, apps=apps.bases)
    folder = Path(args.data) / "bench" / BID
    checks = {"undoMin": apps.undo_min, "bigPx": apps.big_px, "bigMaxMs": apps.big_max_s * 1000}
    write_shell(folder, spec, BID, app_checks=checks)
    shutil.copytree(SAMPLE / "windows", folder / "windows", dirs_exist_ok=True)
    build = {"id": BID, "request": "修照片（样例）", "status": "done", "title": spec.title}
    (folder / "build.json").write_text(json.dumps(build, ensure_ascii=False), encoding="utf-8")
    print(f"已装好：{folder}")
    print(f"启动圆桌后打开 http://127.0.0.1:8000/bench/{BID}/index.html（加 ?selftest=1 看自检）")
    return folder


if __name__ == "__main__":
    main()
