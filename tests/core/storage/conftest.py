from __future__ import annotations

import pytest

from roundtable.core.config import load_config
from roundtable.core.providers import ChannelRouter, FakeProvider, Message
from roundtable.core.storage import Repository, connect

CONFIG = load_config()


class Clock:
    """单调递增的假时间，保证排序稳定。"""

    def __init__(self):
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"2026-10-08T00:00:{self.n:06d}"


@pytest.fixture
def repo() -> Repository:
    return Repository(connect(), clock=Clock())


@pytest.fixture
def router() -> ChannelRouter:
    providers = {name: FakeProvider(name) for name in CONFIG.models.channels}
    return ChannelRouter(CONFIG.models, providers)


MSG = [Message("system", "s"), Message("user", "题目")]
