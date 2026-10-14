from __future__ import annotations

import pytest

from roundtable.core.providers._http import error_fields, kind_for_status, retry_after
from roundtable.core.providers.errors import ErrorKind, ProviderError


@pytest.mark.parametrize(
    "status, code, kind",
    [
        (429, None, ErrorKind.RATE_LIMIT),
        (429, "rate_limit_error", ErrorKind.RATE_LIMIT),
        (429, "insufficient_quota", ErrorKind.QUOTA),
        (402, None, ErrorKind.QUOTA),
        (401, None, ErrorKind.AUTH),
        (403, None, ErrorKind.AUTH),
        (404, None, ErrorKind.NOT_FOUND),
        (408, None, ErrorKind.TIMEOUT),
        (500, None, ErrorKind.SERVER),
        (529, "overloaded_error", ErrorKind.SERVER),
        (400, None, ErrorKind.BAD_REQUEST),
        (422, None, ErrorKind.BAD_REQUEST),
    ],
)
def test_kind_for_status(status, code, kind):
    assert kind_for_status(status, code) == kind


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"error": {"code": "insufficient_quota", "message": "m"}}, ("insufficient_quota", "m")),
        ({"error": {"type": "rate_limit_error", "message": "m"}}, ("rate_limit_error", "m")),
        ({"error": {"status": "RESOURCE_EXHAUSTED", "message": "m"}}, ("RESOURCE_EXHAUSTED", "m")),
        ({"error": "plain"}, (None, "plain")),
        ("not json", (None, "")),
    ],
)
def test_error_fields(body, expected):
    assert error_fields(body) == expected


def test_retry_after():
    assert retry_after({"retry-after": "7"}) == 7.0
    assert retry_after({"retry-after": "soon"}) is None
    assert retry_after({}) is None


@pytest.mark.parametrize(
    "kind, failover",
    [
        (ErrorKind.RATE_LIMIT, True),
        (ErrorKind.QUOTA, True),
        (ErrorKind.NETWORK, True),
        (ErrorKind.TIMEOUT, True),
        (ErrorKind.SERVER, True),
        (ErrorKind.AUTH, True),
        (ErrorKind.NOT_FOUND, True),
        (ErrorKind.BAD_REQUEST, False),
        (ErrorKind.REFUSAL, False),
    ],
)
def test_failover_policy(kind, failover):
    assert ProviderError(kind, "c").failover is failover
