"""Smart Home persistence (provider config, cached entity registry, per-entity
permissions, per-user favourites).

Self-contained like calendar_store / secrets_store: own lazy tables, own
connections, db_path-injectable so the logic is unit-testable against a tmp
sqlite DB without FastAPI or the live hub.db. All SQL is parameterized.

Design notes
------------
* The provider **secret** (a Home Assistant long-lived token) is NOT stored
  here — it lives in the encrypted secret store (secrets_store, namespace
  'smarthome'). This table only holds non-secret config (provider name, the
  LAN base URL, connection health) plus a *cache* of the entity registry so
  the Home tab still renders when the provider is briefly unreachable.
* `sh_permissions` is the seed of the scoped "per-user device permissions"
  feature: control of an entity can be granted to a role (`role:member`) or a
  specific user (`user:alex`). Admins always control everything (enforced in
  the route layer); for everyone else the skeleton default is deny.

Errors: invalid input raises ValueError (routes translate to HubError 400);
missing rows return None/False (routes translate to HubError 404).
"""
import json
import sqlite3
import time
from pathlib import Path

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sh_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT,
    base_url TEXT,
    connected INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_synced_at INTEGER,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sh_entities (
    entity_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    name TEXT,
    area TEXT,
    state TEXT,
    attributes TEXT,
    controllable INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sh_permissions (
    entity_id TEXT NOT NULL,
    scope TEXT NOT NULL,            -- 'role:<role>' | 'user:<username>'
    can_control INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entity_id, scope)
);

CREATE TABLE IF NOT EXISTS sh_favorites (
    username TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (username, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_sh_entities_area ON sh_entities(area);
CREATE INDEX IF NOT EXISTS idx_sh_entities_domain ON sh_entities(domain);
"""


def _now() -> int:
    return int(time.time())


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection like db.connect(), but table-lazy and path-injectable."""
    conn = sqlite3.connect(str(db_path or config.DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.executescript(_SCHEMA)
    return conn


# ----------------------------------------------------------------------------
# Provider config (single row, id=1) — never carries the secret token
# ----------------------------------------------------------------------------
def get_config(db_path: str | Path | None = None) -> dict:
    """Current provider config. Always returns a dict (defaults when unset).

    `configured` is True once an admin has saved a provider + base_url.
    """
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM sh_config WHERE id = 1").fetchone()
    finally:
        conn.close()
    if row is None:
        return {
            "configured": False, "provider": None, "base_url": None,
            "connected": False, "last_error": None, "last_synced_at": None,
        }
    return {
        "configured": bool(row["provider"] and row["base_url"]),
        "provider": row["provider"],
        "base_url": row["base_url"],
        "connected": bool(row["connected"]),
        "last_error": row["last_error"],
        "last_synced_at": row["last_synced_at"],
    }


def set_config(provider: str, base_url: str,
               db_path: str | Path | None = None) -> dict:
    """Upsert provider + base_url. Resets connection health (a re-connect must
    prove reachability again). Does not touch the entity cache."""
    provider = (provider or "").strip()
    base_url = (base_url or "").strip().rstrip("/")
    if not provider:
        raise ValueError("provider is required")
    if not base_url:
        raise ValueError("base_url is required")
    now = _now()
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO sh_config (id, provider, base_url, connected, updated_at) "
            "VALUES (1, ?, ?, 0, ?) "
            "ON CONFLICT(id) DO UPDATE SET provider=excluded.provider, "
            "base_url=excluded.base_url, connected=0, last_error=NULL, "
            "updated_at=excluded.updated_at",
            (provider, base_url, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_config(db_path)


def mark_connection(ok: bool, error: str | None = None, synced: bool = False,
                    db_path: str | Path | None = None) -> None:
    """Record the outcome of the last connection/sync attempt."""
    now = _now()
    conn = _connect(db_path)
    try:
        if synced:
            conn.execute(
                "UPDATE sh_config SET connected=?, last_error=?, "
                "last_synced_at=?, updated_at=? WHERE id=1",
                (1 if ok else 0, error, now, now),
            )
        else:
            conn.execute(
                "UPDATE sh_config SET connected=?, last_error=?, updated_at=? "
                "WHERE id=1",
                (1 if ok else 0, error, now),
            )
        conn.commit()
    finally:
        conn.close()


def clear_config(db_path: str | Path | None = None) -> None:
    """Forget the provider entirely (used on disconnect). Drops the entity
    cache and permissions too; favourites are kept (harmless, re-resolve on
    next connect)."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM sh_config")
        conn.execute("DELETE FROM sh_entities")
        conn.execute("DELETE FROM sh_permissions")
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Entity cache — replaced wholesale on each sync
# ----------------------------------------------------------------------------
def replace_entities(entities: list[dict],
                     db_path: str | Path | None = None) -> int:
    """Replace the cached registry with a fresh sync. `entities` are normalized
    dicts from smarthome.normalize_ha_state(). Returns the count stored."""
    now = _now()
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM sh_entities")
        for e in entities:
            conn.execute(
                "INSERT OR REPLACE INTO sh_entities "
                "(entity_id, domain, name, area, state, attributes, "
                " controllable, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    e["entity_id"], e["domain"], e.get("name"), e.get("area"),
                    e.get("state"), json.dumps(e.get("attributes") or {}),
                    1 if e.get("controllable") else 0, now,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return len(entities)


def _row_to_entity(row: sqlite3.Row) -> dict:
    return {
        "entity_id": row["entity_id"],
        "domain": row["domain"],
        "name": row["name"],
        "area": row["area"],
        "state": row["state"],
        "attributes": json.loads(row["attributes"] or "{}"),
        "controllable": bool(row["controllable"]),
    }


def list_entities(domain: str | None = None,
                  db_path: str | Path | None = None) -> list[dict]:
    """Cached entities, optionally filtered by domain, ordered by area then name."""
    conn = _connect(db_path)
    try:
        if domain:
            rows = conn.execute(
                "SELECT * FROM sh_entities WHERE domain=? "
                "ORDER BY area IS NULL, area, name",
                (domain,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sh_entities ORDER BY area IS NULL, area, name"
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_entity(r) for r in rows]


def get_entity(entity_id: str,
               db_path: str | Path | None = None) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM sh_entities WHERE entity_id=?", (entity_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_entity(row) if row else None


def list_rooms(db_path: str | Path | None = None) -> list[dict]:
    """Entities grouped by area/room. Unassigned entities collect under a
    synthetic 'Unassigned' room so nothing is hidden."""
    grouped: dict[str, list[dict]] = {}
    for e in list_entities(db_path=db_path):
        room = e["area"] or "Unassigned"
        grouped.setdefault(room, []).append(e)
    return [{"area": area, "entities": ents} for area, ents in grouped.items()]


# ----------------------------------------------------------------------------
# Per-entity permissions (seed of "per-user device permissions")
# ----------------------------------------------------------------------------
def set_permission(entity_id: str, scope: str, can_control: bool,
                   db_path: str | Path | None = None) -> None:
    scope = (scope or "").strip()
    if not (scope.startswith("role:") or scope.startswith("user:")):
        raise ValueError("scope must be 'role:<role>' or 'user:<username>'")
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO sh_permissions (entity_id, scope, can_control) "
            "VALUES (?,?,?) ON CONFLICT(entity_id, scope) DO UPDATE SET "
            "can_control=excluded.can_control",
            (entity_id, scope, 1 if can_control else 0),
        )
        conn.commit()
    finally:
        conn.close()


def can_control(entity_id: str, role: str, username: str,
                db_path: str | Path | None = None) -> bool:
    """Skeleton control-permission check for NON-admin devices.

    Admins bypass this in the route layer. For others: an explicit
    `user:<username>` grant wins, else a `role:<role>` grant, else deny.
    """
    conn = _connect(db_path)
    try:
        u = conn.execute(
            "SELECT can_control FROM sh_permissions WHERE entity_id=? AND scope=?",
            (entity_id, f"user:{username}"),
        ).fetchone()
        if u is not None:
            return bool(u["can_control"])
        r = conn.execute(
            "SELECT can_control FROM sh_permissions WHERE entity_id=? AND scope=?",
            (entity_id, f"role:{role}"),
        ).fetchone()
        if r is not None:
            return bool(r["can_control"])
    finally:
        conn.close()
    return False


def list_permissions(entity_id: str,
                     db_path: str | Path | None = None) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT scope, can_control FROM sh_permissions WHERE entity_id=? "
            "ORDER BY scope",
            (entity_id,),
        ).fetchall()
    finally:
        conn.close()
    return [{"scope": r["scope"], "can_control": bool(r["can_control"])} for r in rows]


# ----------------------------------------------------------------------------
# Per-user favourites (pinned entities for the Home tab)
# ----------------------------------------------------------------------------
def add_favorite(username: str, entity_id: str,
                 db_path: str | Path | None = None) -> None:
    conn = _connect(db_path)
    try:
        pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM sh_favorites "
            "WHERE username=?",
            (username,),
        ).fetchone()["p"]
        conn.execute(
            "INSERT OR IGNORE INTO sh_favorites (username, entity_id, position) "
            "VALUES (?,?,?)",
            (username, entity_id, pos),
        )
        conn.commit()
    finally:
        conn.close()


def remove_favorite(username: str, entity_id: str,
                    db_path: str | Path | None = None) -> bool:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM sh_favorites WHERE username=? AND entity_id=?",
            (username, entity_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_favorites(username: str,
                   db_path: str | Path | None = None) -> list[str]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT entity_id FROM sh_favorites WHERE username=? ORDER BY position",
            (username,),
        ).fetchall()
    finally:
        conn.close()
    return [r["entity_id"] for r in rows]
