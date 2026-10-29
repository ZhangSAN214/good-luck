from __future__ import annotations

import pytest

from roundtable.core.dsh.sidecar import Sidecar


def make(tmp_path, argv, **env):
    import os

    base = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP")}
    return Sidecar(
        argv, cwd=tmp_path / "work", env={**base, **env}, start_timeout_s=20, secrets=["S3CRET"]
    )


async def test_start_reads_url_and_redacts_log(tmp_path, fake_dsh):
    sc = make(tmp_path, fake_dsh, FAKE_KEY_ENV_VALUE_ECHO="S3CRET")
    url = await sc.start()
    try:
        assert url == "http://127.0.0.1:3999/?token=SECRETTOKEN123"
        assert sc.state == "running" and sc.pid
        # 口令和 key 不进日志
        await __import__("asyncio").sleep(0.3)
        text = "\n".join(sc.log)
        assert "SECRETTOKEN123" not in text and "S3CRET" not in text
        assert "token=***" in text
    finally:
        assert await sc.stop("测试结束")
    assert sc.state == "stopped" and sc.url is None
    assert sc.last_exit and sc.last_exit.why == "测试结束"
    assert not await sc.stop()  # 再停一次什么都不做


async def test_process_that_dies_before_url_reports_why(tmp_path, fake_dsh):
    sc = make(tmp_path, fake_dsh, FAKE_DSH_FAIL="1")
    with pytest.raises(RuntimeError) as exc:
        await sc.start()
    assert "退出码 3" in str(exc.value) and "boom" in str(exc.value)
    assert sc.state == "stopped"


async def test_missing_executable(tmp_path):
    sc = make(tmp_path, [str(tmp_path / "no-such-dsh")])
    with pytest.raises(RuntimeError) as exc:
        await sc.start()
    assert "启动不了 dsh" in str(exc.value)
