from __future__ import annotations

import pickle

import pytest

from roundtable.core.providers import KeyRing, Secret, redact

from .conftest import FAKE_KEYS


def test_secret_never_shows_value():
    s = Secret("sk-or-v1-" + "a" * 40)
    for text in (repr(s), str(s), f"{s}", f"{s}", f"{s!r}", f"{s:>50}"):
        assert "aaaa" not in text
    assert s.reveal().endswith("a" * 40)


def test_secret_cannot_be_pickled():
    with pytest.raises(TypeError):
        pickle.dumps(Secret("value-1234567"))


def test_empty_secret_rejected():
    with pytest.raises(ValueError):
        Secret("")


def test_keyring_from_env_skips_missing_and_blank():
    ring = KeyRing.from_env(
        ["A_API_KEY", "B_API_KEY", "C_API_KEY"],
        environ={"A_API_KEY": "value-aaaaaaaa", "B_API_KEY": "   "},
    )
    assert ring.has("A_API_KEY")
    assert not ring.has("B_API_KEY")
    assert not ring.has("C_API_KEY")
    assert not ring.has(None)
    assert "value-aaaaaaaa" not in repr(ring)


def test_keyring_reads_dotenv_and_env_wins(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("A_API_KEY=from-file-1234\nB_API_KEY=from-file-5678\nC_API_KEY=\n")
    ring = KeyRing.from_env(
        ["A_API_KEY", "B_API_KEY", "C_API_KEY"],
        environ={"B_API_KEY": "from-env-9999"},
        dotenv_path=dotenv,
    )
    assert ring.get("A_API_KEY").reveal() == "from-file-1234"
    assert ring.get("B_API_KEY").reveal() == "from-env-9999"
    assert not ring.has("C_API_KEY")


def test_keyring_missing_dotenv_is_fine(tmp_path):
    ring = KeyRing.from_env(["A_API_KEY"], environ={}, dotenv_path=tmp_path / "nope")
    assert not ring.has("A_API_KEY")


@pytest.mark.parametrize("key", list(FAKE_KEYS.values()))
def test_redact_known_formats_without_being_told(key):
    text = f"Incorrect API key provided: {key}. Check your settings."
    assert key not in redact(text)


def test_redact_literal_secret_of_unknown_format():
    secret = "plain-local-token-123456"
    assert secret not in redact(f"bad token {secret}", [secret])


def test_redact_leaves_normal_text():
    assert redact("rate limit exceeded, retry in 2s") == "rate limit exceeded, retry in 2s"
