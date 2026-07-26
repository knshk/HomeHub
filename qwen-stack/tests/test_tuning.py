"""Unit tests for app.tuning — pure, offline.

Covers the two rules that matter operationally: values are clamped to the
schema, and a caller's explicit request value is never overwritten by stored
tuning.
"""
import pytest

from app import tuning


# --- sanitize ---------------------------------------------------------------
def test_unknown_keys_dropped():
    assert tuning.sanitize({"nope": 1, "temperature": 0.5}) == {"temperature": 0.5}


def test_blank_values_clear():
    # "" / None mean "unset" so a cleared field returns to the default
    assert tuning.sanitize({"temperature": "", "system": "   ", "seed": None}) == {}


def test_numbers_are_clamped():
    out = tuning.sanitize({"temperature": 9, "top_p": -2, "max_tokens": 999999})
    assert out["temperature"] == 2.0
    assert out["top_p"] == 0.0
    assert out["max_tokens"] == 8192


def test_types_coerced():
    out = tuning.sanitize({"temperature": "0.7", "max_tokens": "256"})
    assert out == {"temperature": 0.7, "max_tokens": 256}


def test_non_numeric_rejected():
    with pytest.raises(ValueError):
        tuning.sanitize({"temperature": "hot"})


def test_non_dict_rejected():
    with pytest.raises(ValueError):
        tuning.sanitize(["temperature"])


def test_system_truncated():
    out = tuning.sanitize({"system": "x" * 9000})
    assert len(out["system"]) == tuning.SCHEMA["system"]["max_len"]


def test_none_is_empty():
    assert tuning.sanitize(None) == {}


# --- trim_history -----------------------------------------------------------
def _msgs(n, system=False):
    out = [{"role": "system", "content": "sys"}] if system else []
    for i in range(n):
        out.append({"role": "user" if i % 2 == 0 else "assistant", "content": str(i)})
    return out


def test_trim_keeps_recent():
    trimmed = tuning.trim_history(_msgs(10), 4)
    assert len(trimmed) == 4
    assert [m["content"] for m in trimmed] == ["6", "7", "8", "9"]


def test_trim_preserves_system():
    trimmed = tuning.trim_history(_msgs(10, system=True), 4)
    assert trimmed[0]["role"] == "system"
    assert len(trimmed) == 5                     # system + 4 recent
    assert [m["content"] for m in trimmed[1:]] == ["6", "7", "8", "9"]


def test_trim_zero_keeps_everything():
    msgs = _msgs(10)
    assert tuning.trim_history(msgs, 0) is msgs


def test_trim_shorter_than_limit_untouched():
    msgs = _msgs(3)
    assert tuning.trim_history(msgs, 10) is msgs


# --- apply ------------------------------------------------------------------
def test_apply_fills_missing_values():
    payload = {"model": "m", "messages": _msgs(2)}
    tuning.apply(payload, {"temperature": 0.3, "max_tokens": 100})
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 100


def test_request_value_wins_over_tuning():
    """A caller who sets a value explicitly must keep it."""
    payload = {"model": "m", "messages": _msgs(2), "temperature": 1.5}
    tuning.apply(payload, {"temperature": 0.1})
    assert payload["temperature"] == 1.5


def test_persona_inserted_when_absent():
    payload = {"messages": _msgs(2)}
    tuning.apply(payload, {"system": "Be brief."})
    assert payload["messages"][0] == {"role": "system", "content": "Be brief."}
    assert len(payload["messages"]) == 3


def test_persona_replaces_existing_system():
    payload = {"messages": _msgs(2, system=True)}
    tuning.apply(payload, {"system": "Be brief."})
    roles = [m["role"] for m in payload["messages"]]
    assert roles.count("system") == 1
    assert payload["messages"][0]["content"] == "Be brief."


def test_history_cap_applied():
    payload = {"messages": _msgs(12)}
    tuning.apply(payload, {"history_turns": 3})
    assert len(payload["messages"]) == 3


def test_persona_and_history_together():
    payload = {"messages": _msgs(12)}
    tuning.apply(payload, {"system": "Be brief.", "history_turns": 3})
    assert payload["messages"][0]["content"] == "Be brief."
    assert len(payload["messages"]) == 4          # persona + 3 recent


def test_empty_tuning_is_noop():
    payload = {"model": "m", "messages": _msgs(2)}
    before = dict(payload)
    tuning.apply(payload, {})
    assert payload == before
