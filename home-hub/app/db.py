"""SQLite layer: connection helper, schema init, parameterized CRUD helpers.

Uses stdlib sqlite3 only (NO ORM). All SQL is parameterized.
"""
import json
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

from . import config

_LOCK = threading.Lock()


def _now() -> int:
    return int(time.time())


def connect() -> sqlite3.Connection:
    """Open a connection with sensible pragmas. Caller closes."""
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_token_hash TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'guest',
    status TEXT NOT NULL DEFAULT 'pending',
    privileges_json TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    last_seen INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_username TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'New chat',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_username TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT 'default',
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS checklists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_username TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id INTEGER NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (checklist_id) REFERENCES checklists(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_username TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'file',
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    mime TEXT NOT NULL DEFAULT 'application/octet-stream',
    size INTEGER NOT NULL DEFAULT 0,
    shared INTEGER NOT NULL DEFAULT 0,
    caption TEXT NOT NULL DEFAULT '',
    indexed INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS file_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_username TEXT NOT NULL,
    gateway_key_id TEXT NOT NULL,
    key_prefix TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_conv_owner ON conversations(owner_username);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_notes_owner ON notes(owner_username);
CREATE INDEX IF NOT EXISTS idx_checklists_owner ON checklists(owner_username);
CREATE INDEX IF NOT EXISTS idx_items_list ON checklist_items(checklist_id);
CREATE INDEX IF NOT EXISTS idx_files_owner ON files(owner_username);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON file_chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_keys_owner ON user_keys(owner_username);
"""


def init_db() -> None:
    """Create the schema if missing. Idempotent (migrate via IF NOT EXISTS)."""
    config.ensure_dirs()
    with _LOCK:
        conn = connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()


# ----------------------------------------------------------------------------
# Low-level helpers
# ----------------------------------------------------------------------------
def query_one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    cur = conn.execute(sql, tuple(params))
    return cur.fetchone()


def query_all(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    cur = conn.execute(sql, tuple(params))
    return cur.fetchall()


def execute(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    return conn.execute(sql, tuple(params))


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


# ----------------------------------------------------------------------------
# Device helpers
# ----------------------------------------------------------------------------
def get_device_by_hash(conn, token_hash: str) -> Optional[sqlite3.Row]:
    return query_one(conn, "SELECT * FROM devices WHERE device_token_hash=?", (token_hash,))


def get_device(conn, device_id: int) -> Optional[sqlite3.Row]:
    return query_one(conn, "SELECT * FROM devices WHERE id=?", (device_id,))


def create_device(conn, token_hash: str, username: str, role: str, status: str,
                  privileges: list[str]) -> int:
    now = _now()
    cur = execute(
        conn,
        "INSERT INTO devices (device_token_hash, username, role, status, privileges_json, created_at, last_seen) "
        "VALUES (?,?,?,?,?,?,?)",
        (token_hash, username, role, status, json.dumps(privileges), now, now),
    )
    conn.commit()
    return cur.lastrowid


def touch_device(conn, device_id: int) -> None:
    execute(conn, "UPDATE devices SET last_seen=? WHERE id=?", (_now(), device_id))
    conn.commit()


def update_device(conn, device_id: int, *, username: str | None = None, role: str | None = None,
                  status: str | None = None, privileges: list[str] | None = None) -> None:
    sets, params = [], []
    if username is not None:
        sets.append("username=?"); params.append(username)
    if role is not None:
        sets.append("role=?"); params.append(role)
    if status is not None:
        sets.append("status=?"); params.append(status)
    if privileges is not None:
        sets.append("privileges_json=?"); params.append(json.dumps(privileges))
    if not sets:
        return
    params.append(device_id)
    execute(conn, f"UPDATE devices SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()


def list_devices(conn) -> list[sqlite3.Row]:
    return query_all(conn, "SELECT * FROM devices ORDER BY created_at DESC")


def count_devices(conn) -> int:
    r = query_one(conn, "SELECT COUNT(*) AS c FROM devices")
    return r["c"] if r else 0


def count_admins(conn) -> int:
    """Number of approved admin devices. Used to decide setup_required."""
    r = query_one(
        conn,
        "SELECT COUNT(*) AS c FROM devices WHERE role='admin' AND status='approved'",
    )
    return r["c"] if r else 0
