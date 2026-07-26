"""Unit tests for the admin-PIN logic in app.auth — offline, no filesystem.

The secret store is monkeypatched to an in-memory dict so we exercise the auth
layer (stored-vs-config precedence, verify, clear, no-lockout) without touching
the real hub.db or the Fernet key file.
"""
import pytest

from app import auth


@pytest.fixture
def mem_pin(monkeypatch):
    store = {}
    monkeypatch.setattr(auth.secrets_store, "get_secret",
                        lambda ns, n: store.get((ns, n)))
    monkeypatch.setattr(auth.secrets_store, "set_secret",
                        lambda ns, n, v: store.__setitem__((ns, n), v))
    monkeypatch.setattr(auth.secrets_store, "delete_secret",
                        lambda ns, n: store.pop((ns, n), None) is not None)
    monkeypatch.setattr(auth.config, "HUB_ADMIN_PIN", "")
    return store


def test_no_pin_by_default(mem_pin):
    assert auth.admin_pin_is_set() is False
    assert auth.verify_admin_pin("1234") is False
    assert auth.get_admin_pin() is None


def test_set_and_verify(mem_pin):
    auth.set_admin_pin("2468")
    assert auth.admin_pin_is_set() is True
    assert auth.verify_admin_pin("2468") is True
    assert auth.verify_admin_pin("0000") is False
    assert auth.verify_admin_pin("") is False


def test_pin_is_trimmed(mem_pin):
    auth.set_admin_pin("  1357 ")
    assert auth.verify_admin_pin("1357") is True
    assert auth.verify_admin_pin(" 1357 ") is True   # entry is trimmed too


def test_clear(mem_pin):
    auth.set_admin_pin("2468")
    auth.clear_admin_pin()
    assert auth.admin_pin_is_set() is False
    assert auth.verify_admin_pin("2468") is False


def test_env_fallback(mem_pin, monkeypatch):
    monkeypatch.setattr(auth.config, "HUB_ADMIN_PIN", "9999")
    assert auth.admin_pin_is_set() is True
    assert auth.verify_admin_pin("9999") is True


def test_stored_overrides_env(mem_pin, monkeypatch):
    monkeypatch.setattr(auth.config, "HUB_ADMIN_PIN", "9999")
    auth.set_admin_pin("1234")
    assert auth.get_admin_pin() == "1234"
    assert auth.verify_admin_pin("1234") is True
    assert auth.verify_admin_pin("9999") is False   # env no longer wins


def test_infinite_attempts_no_lockout(mem_pin):
    """No attempt counter/lockout: many wrong tries never block a correct one."""
    auth.set_admin_pin("2468")
    for _ in range(50):
        assert auth.verify_admin_pin("0000") is False
    assert auth.verify_admin_pin("2468") is True


# --- first-run setup code ---------------------------------------------------
def test_setup_code_not_required_when_unset(monkeypatch):
    monkeypatch.setattr(auth.config, "HUB_SETUP_CODE", "")
    assert auth.setup_code_required() is False
    # No code configured -> any entry (incl. blank) passes: first-device-wins.
    assert auth.verify_setup_code("") is True
    assert auth.verify_setup_code("whatever") is True


def test_setup_code_enforced_when_set(monkeypatch):
    monkeypatch.setattr(auth.config, "HUB_SETUP_CODE", "482913")
    assert auth.setup_code_required() is True
    assert auth.verify_setup_code("482913") is True
    assert auth.verify_setup_code(" 482913 ") is True   # trimmed
    assert auth.verify_setup_code("000000") is False
    assert auth.verify_setup_code("") is False


def test_secret_store_error_is_swallowed(mem_pin, monkeypatch):
    """A secret-store failure must not crash elevation checks (falls back)."""
    def boom(ns, n):
        raise RuntimeError("key file unreadable")
    monkeypatch.setattr(auth.secrets_store, "get_secret", boom)
    monkeypatch.setattr(auth.config, "HUB_ADMIN_PIN", "5555")
    # get_admin_pin swallows the store error and uses the env fallback
    assert auth.get_admin_pin() == "5555"
    assert auth.verify_admin_pin("5555") is True
