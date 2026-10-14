"""日志脱敏：任何 logger（包括第三方库）输出的消息、参数和回溯都不含已加载的 key。"""

from __future__ import annotations

import logging

from roundtable.core.providers import Secret

KEY = "sk-or-v1-" + "7" * 48
PLAIN = "local-token-" + "q" * 20  # 不符合任何已知格式，只能靠注册表脱敏


def test_third_party_logger_message_args_and_traceback(caplog):
    Secret(KEY)
    Secret(PLAIN)
    caplog.set_level(logging.DEBUG)
    lib = logging.getLogger("some.third_party.lib")
    lib.debug("headers=%s", {"authorization": f"Bearer {KEY}", "x": PLAIN})
    try:
        raise RuntimeError(f"boom {PLAIN}")
    except RuntimeError:
        lib.exception("request failed")
    assert KEY not in caplog.text
    assert PLAIN not in caplog.text
    assert "request failed" in caplog.text and "RuntimeError" in caplog.text


def test_unrelated_messages_untouched(caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("x").info("调用 %s 成功，用时 %.1fs", "openrouter", 1.25)
    assert "调用 openrouter 成功，用时 1.2s" in caplog.text


def test_records_without_secrets_keep_their_format_args():
    """不含密钥的日志记录保持原样：uvicorn 的 color_message % args 等仍能格式化。"""
    record = logging.getLogRecordFactory()(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        "Uvicorn running on %s://%s:%d",
        ("http", "127.0.0.1", 8000),
        None,
    )
    record.color_message = "Uvicorn running on %s://%s:%d"
    assert record.args == ("http", "127.0.0.1", 8000)
    assert record.getMessage() == "Uvicorn running on http://127.0.0.1:8000"
    assert record.color_message % record.args == "Uvicorn running on http://127.0.0.1:8000"


def test_records_with_secrets_are_rewritten_including_color_message():
    from roundtable.core.providers import Secret

    key = "sk-or-v1-" + "c" * 48
    Secret(key)
    record = logging.getLogRecordFactory()("x", logging.INFO, __file__, 1, "key=%s", (key,), None)
    record.color_message = "key=%s"
    assert key not in record.getMessage() and record.args is None
