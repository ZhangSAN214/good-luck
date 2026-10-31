"""下载示例素材包的脚本：--check 只看、不联网；下载换成假的。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "fetch_bench_samples", ROOT / "scripts" / "fetch_bench_samples.py"
)
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)  # type: ignore[union-attr]


def test_check_lists_missing_items_without_network(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch, "data_dir", lambda: tmp_path)
    assert fetch.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "✗ portrait" in out and "0/" in out


def test_download_failures_are_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch, "data_dir", lambda: tmp_path)

    async def bad_get(client, url):
        return b"not the real file"

    monkeypatch.setattr(fetch, "_get", bad_get)
    assert fetch.main(["--only", "coffee"]) == 1
    out = capsys.readouterr().out
    assert "✗ 校验 coffee" in out
    assert not (tmp_path / "bench/_samples/photos/coffee.png").exists()
