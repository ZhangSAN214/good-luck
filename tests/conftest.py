"""全部测试共用：不联网。"""

from __future__ import annotations

import os

import pytest

import roundtable.core.browse as browse
from roundtable.core.config.loader import NO_LOCAL_ENV

# 本机的 config/*.local.yaml 不影响测试。导入时就设：子目录的 conftest 会在
# pytest_configure 之前导入，并在模块级读配置。
os.environ[NO_LOCAL_ENV] = "1"


@pytest.fixture(autouse=True)
def _no_feed_network(monkeypatch):
    """成员上网的订阅源抓取一律失败（需要内容的测试自己注入 fetch）。"""

    async def offline(url, timeout_s, max_bytes):
        raise browse.FeedError("测试不联网")

    monkeypatch.setattr(browse, "http_fetch", offline)


@pytest.fixture(autouse=True)
def _members_awake(request, monkeypatch):
    """测试用的是真实钟点：成员一律当醒着，免得半夜跑测试时消息被「睡着没看到」。
    要测作息的测试请求 ``real_rhythm`` 夹具。"""
    if "real_rhythm" in request.fixturenames:
        return
    from roundtable.core.member.rhythm import Rhythm

    monkeypatch.setattr(Rhythm, "asleep", lambda self, member, at: False)


@pytest.fixture
def real_rhythm():
    """照真实作息判断成员睡没睡（配合固定的 budget.now 用）。"""
