"""全部测试共用：不联网。"""

from __future__ import annotations

import pytest

import roundtable.core.browse as browse
from roundtable.core.config.loader import NO_LOCAL_ENV


@pytest.fixture(autouse=True)
def _no_local_config(monkeypatch):
    """本机的 config/*.local.yaml 不影响测试。"""
    monkeypatch.setenv(NO_LOCAL_ENV, "1")


@pytest.fixture(autouse=True)
def _no_feed_network(monkeypatch):
    """成员上网的订阅源抓取一律失败（需要内容的测试自己注入 fetch）。"""

    async def offline(url, timeout_s, max_bytes):
        raise browse.FeedError("测试不联网")

    monkeypatch.setattr(browse, "http_fetch", offline)
