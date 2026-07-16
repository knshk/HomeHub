"""Encrypted secret store: retrievable secrets for cloud/HA tokens etc.

Unlike issued device/gateway keys (which are sha256-HASHED and never
recoverable), these secrets must be read back by the hub itself, so they are
encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256, cryptography lib).

Key custody: a master key is auto-generated at DATA_DIR/secret.key with 0600
perms and never leaves the box. Threat model: this protects the DB at rest
and in backups (exfiltrated hub.db is useless without secret.key); it does
NOT protect against an attacker with root/hub-user access, who can read the
key file directly.

Self-contained: own lazy table, own connections. Does not touch db.SCHEMA.
All SQL is parameterized.
"""
import base64
import os
import sqlite3
import threading
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import config

_LOCK = threading.Lock()

KEY_FILENAME = "secret.key"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    namespace TEXT NOT NULL,
    name TEXT NOT NULL,
    value_encrypted BLOB NOT NULL,
    hint TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (namespace, name)
);
"""


class SecretStoreError(Exception):
    """Raised when the master key file is unusable (corrupt/wrong key)."""


def _now() -> int:
    return int(time.time())


# ----------------------------------------------------------------------------
# Master key handling
# ----------------------------------------------------------------------------
def _default_key_path() -> Path:
    return Path(config.DATA_DIR) / KEY_FILENAME


def _load_or_create_key(key_path: str | Path | None = None) -> bytes:
    """Return the Fernet master key, generating it on first use (0600)."""
    path = Path(key_path) if key_path is not None else _default_key_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        # O_EXCL: never clobber a key racing another writer; 0600 from birth.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return key
    # Verify + fix perms on every read (backups/copies can loosen them).
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        os.chmod(path, 0o600)
    key = path.read_bytes().strip()
    try:
        # Fernet keys are urlsafe-base64 of 32 bytes; validate before use so a
        # corrupt file fails loudly here instead of deep inside cryptography.
        if len(base64.urlsafe_b64decode(key)) != 32:
            raise ValueError("wrong length")
        Fernet(key)
    except Exception as e:
        raise SecretStoreError(
            f"Corrupt master key file {path}: not a valid Fernet key ({e}). "
            "Restore it from backup; deleting it orphans all stored secrets."
        ) from e
    return key


def _fernet(key_path: str | Path | None = None) -> Fernet:
    return Fernet(_load_or_create_key(key_path))


# ----------------------------------------------------------------------------
# DB helpers (own connections; table created lazily)
# ----------------------------------------------------------------------------
def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection like db.connect(), but table-lazy and path-injectable."""
    conn = sqlite3.connect(str(db_path or config.DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.executescript(_SCHEMA)
    return conn


def _make_hint(value: str) -> str:
    """Masked preview like 'sk-a…7f2'. Never enough to reconstruct the value."""
    if len(value) <= 8:
        return "…" * 3
    return f"{value[:4]}…{value[-3:]}"


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------
def set_secret(namespace: str, name: str, value: str,
               db_path: str | Path | None = None,
               key_path: str | Path | None = None) -> None:
    """Insert or overwrite a secret (upsert keyed on namespace+name)."""
    token = _fernet(key_path).encrypt(value.encode("utf-8"))
    now = _now()
    with _LOCK:
        conn = _connect(db_path)
        try:
            conn.execute(
                "INSERT INTO secrets (namespace, name, value_encrypted, hint, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(namespace, name) DO UPDATE SET "
                "value_encrypted=excluded.value_encrypted, hint=excluded.hint, "
                "updated_at=excluded.updated_at",
                (namespace, name, token, _make_hint(value), now, now),
            )
            conn.commit()
        finally:
            conn.close()


def get_secret(namespace: str, name: str,
               db_path: str | Path | None = None,
               key_path: str | Path | None = None) -> str | None:
    """Decrypt and return the secret, or None if absent."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT value_encrypted FROM secrets WHERE namespace=? AND name=?",
            (namespace, name),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        return _fernet(key_path).decrypt(bytes(row["value_encrypted"])).decode("utf-8")
    except InvalidToken as e:
        raise SecretStoreError(
            f"Cannot decrypt secret {namespace}/{name}: master key does not "
            "match (key file replaced after this secret was stored?)"
        ) from e


def delete_secret(namespace: str, name: str,
                  db_path: str | Path | None = None) -> bool:
    """Delete a secret. Returns True if one existed."""
    with _LOCK:
        conn = _connect(db_path)
        try:
            cur = conn.execute(
                "DELETE FROM secrets WHERE namespace=? AND name=?",
                (namespace, name),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def has_secret(namespace: str, name: str,
               db_path: str | Path | None = None) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM secrets WHERE namespace=? AND name=?",
            (namespace, name),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def list_secrets(namespace: str,
                 db_path: str | Path | None = None) -> list[dict]:
    """List secrets in a namespace: name + masked hint only, NEVER plaintext."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name, hint, updated_at FROM secrets WHERE namespace=? ORDER BY name",
            (namespace,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_namespaces(db_path: str | Path | None = None) -> list[dict]:
    """Summary of all namespaces: {namespace, count, updated_at(newest)}."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT namespace, COUNT(*) AS count, MAX(updated_at) AS updated_at "
            "FROM secrets GROUP BY namespace ORDER BY namespace",
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
