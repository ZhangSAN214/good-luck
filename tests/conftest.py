"""全部测试共用：不联网。"""

from __future__ import annotations

import pytest

import roundtable.core.browse as browse


@pytest.fixture(autouse=True)
def _no_feed_network(monkeypatch):
    """成员上网的订阅源抓取一律失败（需要内容的测试自己注入 fetch）。"""

    async def offline(url, timeout_s, max_bytes):
        raise browse.FeedError("测试不联网")

    monkeypatch.setattr(browse, "http_fetch", offline)
