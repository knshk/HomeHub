"""Unit tests for app.secrets_store — offline, tmp_path sqlite + tmp key file.

No network, no services touched: every call passes explicit db_path/key_path
so the live hub.db and DATA_DIR/secret.key are never opened.
"""
import os
import stat

import pytest

from app import secrets_store


@pytest.fixture
def paths(tmp_path):
    """(db_path, key_path) inside tmp_path."""
    return tmp_path / "hub.db", tmp_path / "secret.key"


def test_roundtrip_encrypt_decrypt(paths):
    db, key = paths
    secrets_store.set_secret("cloud", "api_token", "sk-abc123DEF456xyz7f2",
                             db_path=db, key_path=key)
    assert secrets_store.get_secret("cloud", "api_token",
                                    db_path=db, key_path=key) == "sk-abc123DEF456xyz7f2"


def test_value_encrypted_at_rest(paths):
    db, key = paths
    secrets_store.set_secret("cloud", "tok", "super-plain-value",
                             db_path=db, key_path=key)
    conn = secrets_store._connect(db)
    try:
        row = conn.execute("SELECT value_encrypted FROM secrets").fetchone()
    finally:
        conn.close()
    assert b"super-plain-value" not in bytes(row["value_encrypted"])


def test_key_file_created_0600(paths):
    db, key = paths
    secrets_store.set_secret("ns", "n", "v", db_path=db, key_path=key)
    assert key.exists()
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600


def test_loose_key_perms_fixed_on_read(paths):
    db, key = paths
    secrets_store.set_secret("ns", "n", "v", db_path=db, key_path=key)
    os.chmod(key, 0o644)
    assert secrets_store.get_secret("ns", "n", db_path=db, key_path=key) == "v"
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600


def test_list_returns_hint_not_plaintext(paths):
    db, key = paths
    value = "sk-abc123DEF456xyz7f2"
    secrets_store.set_secret("cloud", "api_token", value,
                             db_path=db, key_path=key)
    items = secrets_store.list_secrets("cloud", db_path=db)
    assert len(items) == 1
    item = items[0]
    assert set(item) == {"name", "hint", "updated_at"}
    assert item["name"] == "api_token"
    assert item["hint"] == "sk-a…7f2"
    assert value not in str(item)


def test_short_value_hint_fully_masked(paths):
    db, key = paths
    secrets_store.set_secret("ns", "pin", "1234", db_path=db, key_path=key)
    hint = secrets_store.list_secrets("ns", db_path=db)[0]["hint"]
    assert "1234" not in hint and "1" not in hint


def test_overwrite_updates(paths):
    db, key = paths
    secrets_store.set_secret("ns", "n", "old-value-000000", db_path=db, key_path=key)
    secrets_store.set_secret("ns", "n", "new-value-111111", db_path=db, key_path=key)
    assert secrets_store.get_secret("ns", "n", db_path=db, key_path=key) == "new-value-111111"
    items = secrets_store.list_secrets("ns", db_path=db)
    assert len(items) == 1  # upsert, not a second row
    assert items[0]["hint"] == "new-…111"


def test_delete(paths):
    db, key = paths
    secrets_store.set_secret("ns", "n", "v", db_path=db, key_path=key)
    assert secrets_store.has_secret("ns", "n", db_path=db) is True
    assert secrets_store.delete_secret("ns", "n", db_path=db) is True
    assert secrets_store.has_secret("ns", "n", db_path=db) is False
    assert secrets_store.get_secret("ns", "n", db_path=db, key_path=key) is None
    assert secrets_store.delete_secret("ns", "n", db_path=db) is False


def test_missing_returns_none(paths):
    db, key = paths
    assert secrets_store.get_secret("nowhere", "nothing",
                                    db_path=db, key_path=key) is None


def test_namespaces_isolated(paths):
    db, key = paths
    secrets_store.set_secret("a", "n", "va", db_path=db, key_path=key)
    secrets_store.set_secret("b", "n", "vb", db_path=db, key_path=key)
    assert [i["name"] for i in secrets_store.list_secrets("a", db_path=db)] == ["n"]
    assert secrets_store.get_secret("a", "n", db_path=db, key_path=key) == "va"
    ns = secrets_store.list_namespaces(db_path=db)
    assert [(n["namespace"], n["count"]) for n in ns] == [("a", 1), ("b", 1)]


def test_corrupt_key_file_clear_error(paths):
    db, key = paths
    key.write_bytes(b"not-a-valid-fernet-key")
    with pytest.raises(secrets_store.SecretStoreError, match="Corrupt master key"):
        secrets_store.set_secret("ns", "n", "v", db_path=db, key_path=key)


def test_replaced_key_cannot_decrypt(paths):
    db, key = paths
    secrets_store.set_secret("ns", "n", "v", db_path=db, key_path=key)
    key.unlink()  # a fresh key is generated on next use
    with pytest.raises(secrets_store.SecretStoreError, match="Cannot decrypt"):
        secrets_store.get_secret("ns", "n", db_path=db, key_path=key)
