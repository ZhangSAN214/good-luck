"""名册：经理在前、只列能上桌的模型、工资标准来自价格、本月已领工资来自调用记录。"""

from __future__ import annotations

import pytest

from roundtable.core.config import load_config
from roundtable.core.config.schema import ModelsConfig
from roundtable.core.office import roster

CFG = load_config()


def test_managers_come_from_config_and_lead_the_roster():
    managers = [m.id for m in CFG.models.managers]
    assert len(managers) >= 1
    entries = roster(CFG, [m.id for m in CFG.models.enabled], {})
    assert [e.model_id for e in entries[: len(managers)]] == sorted(
        managers, key=lambda i: -CFG.models.get(i).price.output
    )
    assert {e.role for e in entries[len(managers) :]} == {"staff"}


def test_only_seat_models_and_wages_are_prices():
    entries = roster(CFG, [], {})
    ids = {e.model_id for e in entries}
    assert ids == {m.id for m in CFG.models.enabled if m.seat}
    for e in entries:
        spec = CFG.models.get(e.model_id)
        assert (e.wage_input, e.wage_output) == (spec.price.input, spec.price.output)
        assert not e.available and e.month_paid_usd == 0.0
    staff = [e for e in entries if e.role == "staff"]
    assert [e.wage_output for e in staff] == sorted((e.wage_output for e in staff), reverse=True)


def test_month_usage_is_merged():
    mid = CFG.models.managers[0].id
    usage = {mid: {"calls": 3, "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.25}}
    entry = next(e for e in roster(CFG, [mid], usage) if e.model_id == mid)
    assert entry.available and entry.month_calls == 3 and entry.month_paid_usd == 0.25
    assert entry.month_input_tokens == 100 and entry.month_output_tokens == 50


def test_manager_must_take_a_seat():
    raw = CFG.models.model_dump()
    media = next(m for m in raw["models"] if not m["seat"])
    media["office_role"] = "manager"
    with pytest.raises(ValueError, match="是经理，必须能上桌"):
        ModelsConfig.model_validate(raw)


def test_unknown_role_is_rejected():
    raw = CFG.models.model_dump()
    raw["models"][0]["office_role"] = "boss"
    with pytest.raises(ValueError):
        ModelsConfig.model_validate(raw)
