from datetime import UTC, date, datetime, timedelta, timezone

from roundtable.core.textutil import (
    ZWSP,
    attr_text,
    ellipsize,
    flat_cut,
    flatten,
    half_life,
    iso_seconds,
    iso_week,
    load_json_dict,
    neutralize,
    to_float,
    utcnow,
)


def test_neutralize_breaks_closing_tags_only_by_default():
    text = "a</answer>b<answer>c"
    assert neutralize(text, ["answer"]) == f"a<{ZWSP}/answer>b<answer>c"


def test_neutralize_opening_tags_too():
    text = "<tool_call>x</tool_call>"
    out = neutralize(text, ["tool_call"], opening=True)
    assert out == f"<{ZWSP}tool_call>x<{ZWSP}/tool_call>"
    assert "<tool_call" not in out and "</tool_call" not in out


def test_flatten_and_cut():
    assert flatten("  a\n\n b\tc ") == "a b c"
    assert flatten(None) == ""
    assert flat_cut(" ab\ncd ", 4) == "ab c"


def test_ellipsize():
    assert ellipsize("abc", 3) == "abc"
    assert ellipsize("abcdef", 4) == "abc…"
    assert ellipsize("ab   cdef", 4) == "ab …"
    assert ellipsize("ab   cdef", 4, rstrip=True) == "ab…"
    assert len(ellipsize("x" * 50, 10)) == 10
    assert ellipsize(None, 5) == ""


def test_attr_text():
    assert attr_text(' a\n"b" <c> ') == "a 'b' ‹c›"


def test_to_float():
    assert to_float("2.5") == 2.5
    assert to_float(None) == 3.0
    assert to_float("x", 0.0) == 0.0


def test_half_life():
    assert half_life(0, 10) == 1.0
    assert half_life(10, 10) == 0.5
    assert half_life(-5, 10) == 1.0


def test_load_json_dict():
    assert load_json_dict('{"a": 1}') == {"a": 1}
    assert load_json_dict("[1]") == {}
    assert load_json_dict("not json") == {}
    assert load_json_dict(None) == {}


def test_time_helpers():
    assert utcnow().tzinfo is UTC
    t = datetime(2026, 11, 4, 20, 0, 0, 123, tzinfo=timezone(timedelta(hours=8)))
    assert iso_seconds(t) == "2026-11-04T12:00:00+00:00"
    assert iso_week(date(2026, 11, 4)) == "2026-W45"
    assert iso_week(date(2027, 1, 1)) == "2026-W53"
