"""SQLite persistence layer.

Provides a connection helper, schema initialisation/migration, and small helper
functions for API keys and usage logging. Uses only the stdlib ``sqlite3``
module. All SQL is parameterised — no string interpolation of user input.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import settings

# Exact schema as defined by the shared contract.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    rpm_limit INTEGER NOT NULL DEFAULT 60,
    daily_token_limit INTEGER NOT NULL DEFAULT 0,
    cloud_allowed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id INTEGER,
    ts TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    status INTEGER
);

CREATE INDEX IF NOT EXISTS idx_usage_key_id ON usage_log(key_id);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_log(model);

-- Operator-managed model registry + lifecycle state. The gateway is the
-- control plane: `state` is one of stopped|running|suspended and is enforced
-- in the proxy path (suspended/stopped models are not served).
CREATE TABLE IF NOT EXISTS managed_models (
    alias TEXT PRIMARY KEY,
    ollama_tag TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'chat',
    state TEXT NOT NULL DEFAULT 'stopped',
    provider TEXT NOT NULL DEFAULT 'local',
    upstream_model TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- BYO-key cloud AI providers. The API key is stored Fernet-encrypted (never
-- plaintext); ``key_hint`` is a short masked display string. ``enabled`` is the
-- admin opt-in switch and ``monthly_token_budget`` (0 = unlimited) caps the
-- calendar-month token spend across all models of that provider.
CREATE TABLE IF NOT EXISTS providers (
    name TEXT PRIMARY KEY,
    api_key_encrypted BLOB,
    key_hint TEXT,
    enabled INTEGER NOT NULL DEFAULT 0,
    monthly_token_budget INTEGER NOT NULL DEFAULT 0,
    base_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Ollama tags the operator explicitly removed. Auto-reconcile skips these so a
-- 'Remove' is not silently undone the next time the dashboard scans Ollama.
CREATE TABLE IF NOT EXISTS model_dismissed (
    ollama_tag TEXT PRIMARY KEY,
    dismissed_at TEXT NOT NULL
);
"""

# Legal lifecycle states.
MODEL_STATES = ("stopped", "running", "suspended")
# Roles a managed model can play (informational; drives dashboard grouping).
MODEL_ROLES = ("chat", "vision", "embed")
# Cloud providers the gateway knows how to dispatch to ('local' means Ollama).
PROVIDER_NAMES = ("anthropic", "openai")


def utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    """Open a new SQLite connection with sensible defaults.

    Callers own the connection lifecycle and should close it (or use it as a
    context manager). ``row_factory`` is set so rows behave like dicts.
    """
    db_path = settings.db_path
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    """Create tables/indexes if they do not yet exist (idempotent)."""
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        # Migration: add managed_models.role to pre-existing databases.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(managed_models)")}
        if "role" not in cols:
            conn.execute("ALTER TABLE managed_models ADD COLUMN role TEXT NOT NULL DEFAULT 'chat'")
        # Migration: cloud-provider columns on pre-existing databases.
        if "provider" not in cols:
            conn.execute("ALTER TABLE managed_models ADD COLUMN provider TEXT NOT NULL DEFAULT 'local'")
        if "upstream_model" not in cols:
            conn.execute("ALTER TABLE managed_models ADD COLUMN upstream_model TEXT DEFAULT NULL")
        key_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_keys)")}
        if "cloud_allowed" not in key_cols:
            conn.execute("ALTER TABLE api_keys ADD COLUMN cloud_allowed INTEGER NOT NULL DEFAULT 0")
        # Seed the known providers, disabled, so the admin UI always has rows to
        # act on. Idempotent: existing rows (keys, budgets) are never touched.
        now = utcnow_iso()
        for name in PROVIDER_NAMES:
            conn.execute(
                """
                INSERT OR IGNORE INTO providers
                    (name, api_key_encrypted, key_hint, enabled,
                     monthly_token_budget, base_url, created_at, updated_at)
                VALUES (?, NULL, NULL, 0, 0, NULL, ?, ?)
                """,
                (name, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """Convert a ``sqlite3.Row`` to a plain dict (or ``None``)."""
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------- #
# API key helpers
# --------------------------------------------------------------------------- #
def create_api_key(
    name: str,
    key_prefix: str,
    key_hash: str,
    rpm_limit: int,
    daily_token_limit: int = 0,
) -> Dict[str, Any]:
    """Insert a new API key row and return it as a dict.

    Only the hash and a short display prefix are stored — never the plaintext.
    """
    created_at = utcnow_iso()
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO api_keys
                (name, key_prefix, key_hash, created_at, revoked, rpm_limit, daily_token_limit)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (name, key_prefix, key_hash, created_at, rpm_limit, daily_token_limit),
        )
        conn.commit()
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (new_id,)).fetchone()
        return _row_to_dict(row)  # type: ignore[return-value]
    finally:
        conn.close()


def get_key_by_hash(key_hash: str) -> Optional[Dict[str, Any]]:
    """Look up an API key row by its sha256 hash."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_keys() -> List[Dict[str, Any]]:
    """Return all API keys (without secrets — only prefixes/metadata)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, name, key_prefix, created_at, revoked, rpm_limit,
                   daily_token_limit, cloud_allowed
            FROM api_keys
            ORDER BY id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revoke_key(key_id: int) -> bool:
    """Mark a key as revoked. Returns True if a row was updated."""
    conn = get_connection()
    try:
        cur = conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_key_cloud_allowed(key_id: int, allowed: bool) -> bool:
    """Toggle a key's per-key cloud opt-in. Returns True if a row was updated."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE api_keys SET cloud_allowed = ? WHERE id = ?",
            (1 if allowed else 0, key_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_daily_token_usage(key_id: int, day_iso_prefix: str) -> int:
    """Return total tokens (prompt+completion) used by a key on a given day.

    ``day_iso_prefix`` is the ``YYYY-MM-DD`` prefix of the ISO timestamp.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0)
                   AS total
            FROM usage_log
            WHERE key_id = ? AND ts LIKE ?
            """,
            (key_id, f"{day_iso_prefix}%"),
        ).fetchone()
        return int(row["total"]) if row and row["total"] is not None else 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Usage logging
# --------------------------------------------------------------------------- #
def log_usage(
    key_id: Optional[int],
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    status: int,
) -> None:
    """Append a usage record. Best-effort: never raises into the request path."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO usage_log
                (key_id, ts, model, prompt_tokens, completion_tokens, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key_id, utcnow_iso(), model, int(prompt_tokens or 0),
             int(completion_tokens or 0), int(status)),
        )
        conn.commit()
    finally:
        conn.close()


def list_usage(limit: int = 200) -> List[Dict[str, Any]]:
    """Return recent usage rows joined with key name, newest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT u.id, u.key_id, k.name AS key_name, u.ts, u.model,
                   u.prompt_tokens, u.completion_tokens, u.status
            FROM usage_log u
            LEFT JOIN api_keys k ON k.id = u.key_id
            ORDER BY u.id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Managed-model registry (control plane)
# --------------------------------------------------------------------------- #
_MODEL_COLS = ("alias, ollama_tag, display_name, role, state, provider, "
               "upstream_model, created_at, updated_at")


def list_models() -> List[Dict[str, Any]]:
    """Return all managed models ordered by role then display name."""
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT {_MODEL_COLS} FROM managed_models "
            "ORDER BY role, display_name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_model(alias: str) -> Optional[Dict[str, Any]]:
    """Return one managed model by alias, or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT {_MODEL_COLS} FROM managed_models WHERE alias = ?",
            (alias,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_model_by_alias_or_tag(name: str) -> Optional[Dict[str, Any]]:
    """Resolve a managed model by its client alias OR its upstream ollama tag.

    Used by the proxy gate: a caller may address either the alias or the raw tag.
    """
    if not name:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT {_MODEL_COLS} FROM managed_models "
            "WHERE alias = ? OR ollama_tag = ? LIMIT 1",
            (name, name),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def upsert_model(alias: str, ollama_tag: str, display_name: str,
                 role: str = "chat", state: str = "stopped",
                 provider: str = "local",
                 upstream_model: Optional[str] = None) -> Dict[str, Any]:
    """Insert or update a managed model. Returns the stored row.

    ``provider`` is 'local' for Ollama-served models, else a cloud provider
    name; ``upstream_model`` is the provider-side model id for cloud models.
    """
    now = utcnow_iso()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO managed_models
                (alias, ollama_tag, display_name, role, state, provider,
                 upstream_model, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(alias) DO UPDATE SET
                ollama_tag = excluded.ollama_tag,
                display_name = excluded.display_name,
                role = excluded.role,
                provider = excluded.provider,
                upstream_model = excluded.upstream_model,
                updated_at = excluded.updated_at
            """,
            (alias, ollama_tag, display_name, role, state, provider,
             upstream_model, now, now),
        )
        conn.commit()
        row = conn.execute(
            f"SELECT {_MODEL_COLS} FROM managed_models WHERE alias = ?",
            (alias,),
        ).fetchone()
        return _row_to_dict(row)  # type: ignore[return-value]
    finally:
        conn.close()


def set_model_state(alias: str, state: str) -> bool:
    """Update a model's lifecycle state. Returns True if a row was updated."""
    if state not in MODEL_STATES:
        raise ValueError(f"invalid model state: {state}")
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE managed_models SET state = ?, updated_at = ? WHERE alias = ?",
            (state, utcnow_iso(), alias),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_model(alias: str) -> bool:
    """Remove a managed model from the registry. Returns True if deleted."""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM managed_models WHERE alias = ?", (alias,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Dismissed tags (auto-reconcile opt-out)
# --------------------------------------------------------------------------- #
def dismiss_tag(ollama_tag: str) -> None:
    """Mark an ollama tag as dismissed so auto-reconcile won't re-add it."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO model_dismissed (ollama_tag, dismissed_at) VALUES (?, ?)",
            (ollama_tag, utcnow_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def undismiss_tag(ollama_tag: str) -> None:
    """Clear a dismissal (called when a tag is explicitly (re)added)."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_dismissed WHERE ollama_tag = ?", (ollama_tag,))
        conn.commit()
    finally:
        conn.close()


def dismissed_tags() -> set:
    """Return the set of dismissed ollama tags."""
    conn = get_connection()
    try:
        return {r["ollama_tag"] for r in conn.execute("SELECT ollama_tag FROM model_dismissed")}
    finally:
        conn.close()


def seed_models(entries: List[Dict[str, str]]) -> None:
    """Seed the registry from (alias, ollama_tag, display_name) entries if empty.

    Seeded entries start ``running`` — they mirror models the gateway was already
    serving before this control plane existed, so seeding must not break traffic.
    Idempotent: only inserts aliases that are not already present.
    """
    conn = get_connection()
    try:
        existing = {r["alias"] for r in conn.execute(
            "SELECT alias FROM managed_models").fetchall()}
        now = utcnow_iso()
        for e in entries:
            alias = e["alias"]
            if alias in existing:
                continue
            conn.execute(
                """
                INSERT INTO managed_models
                    (alias, ollama_tag, display_name, role, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?)
                """,
                (alias, e["ollama_tag"], e.get("display_name") or alias,
                 e.get("role") or "chat", now, now),
            )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Cloud providers (BYO-key registry)
# --------------------------------------------------------------------------- #
def get_provider(name: str) -> Optional[Dict[str, Any]]:
    """Return one provider row (including the encrypted key blob), or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM providers WHERE name = ?", (name,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_providers() -> List[Dict[str, Any]]:
    """Return all provider rows ordered by name.

    Rows include ``api_key_encrypted``; callers that expose data outward must
    strip it (see ``providers.list_providers`` for the sanitised view).
    """
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM providers ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_provider_key(name: str, api_key_encrypted: bytes, key_hint: str) -> bool:
    """Store the encrypted API key + display hint. True if a row was updated."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE providers SET api_key_encrypted = ?, key_hint = ?, "
            "updated_at = ? WHERE name = ?",
            (api_key_encrypted, key_hint, utcnow_iso(), name),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_provider_enabled(name: str, enabled: bool) -> bool:
    """Flip the provider opt-in switch. Returns True if a row was updated."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE providers SET enabled = ?, updated_at = ? WHERE name = ?",
            (1 if enabled else 0, utcnow_iso(), name),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_provider_budget(name: str, monthly_token_budget: int) -> bool:
    """Set the calendar-month token budget (0 = unlimited)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE providers SET monthly_token_budget = ?, updated_at = ? "
            "WHERE name = ?",
            (max(0, int(monthly_token_budget)), utcnow_iso(), name),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def provider_month_usage(provider: str, month_prefix: str) -> int:
    """Total tokens logged this calendar month against a provider's models.

    Cloud requests are logged with the client-facing name — usually the alias,
    but callers may also address a model by its provider-side id (stored in
    ``ollama_tag``), so both names must count or budget spend leaks past the
    cap. ``month_prefix`` is the ``YYYY-MM`` prefix of the ISO timestamp.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(u.prompt_tokens), 0)
                   + COALESCE(SUM(u.completion_tokens), 0) AS total
            FROM usage_log u
            WHERE u.ts LIKE ?
              AND u.model IN (
                    SELECT alias FROM managed_models WHERE provider = ?
                    UNION
                    SELECT ollama_tag FROM managed_models WHERE provider = ?
              )
            """,
            (f"{month_prefix}%", provider, provider),
        ).fetchone()
        return int(row["total"]) if row and row["total"] is not None else 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Per-model usage metrics (for the admin dashboard)
# --------------------------------------------------------------------------- #
def model_usage_totals(models: List[str], since_iso: str) -> Dict[str, Any]:
    """Aggregate request count + token totals for the given model names since a
    timestamp. ``models`` is a list of names to match against usage_log.model
    (typically [alias, ollama_tag])."""
    if not models:
        return {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
    placeholders = ",".join("?" for _ in models)
    conn = get_connection()
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS requests,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens
            FROM usage_log
            WHERE model IN ({placeholders}) AND ts >= ?
            """,
            (*models, since_iso),
        ).fetchone()
        return {
            "requests": int(row["requests"] or 0),
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
        }
    finally:
        conn.close()


def model_usage_series(models: List[str], since_iso: str,
                       bucket_len: int) -> Dict[str, Dict[str, int]]:
    """Return usage grouped by a timestamp prefix of length ``bucket_len``.

    ``bucket_len`` = 13 buckets by hour (``YYYY-MM-DDTHH``); 10 by day. Keyed by
    the bucket string -> {requests, prompt_tokens, completion_tokens}. Zero-count
    buckets are filled in by the caller.
    """
    if not models:
        return {}
    placeholders = ",".join("?" for _ in models)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT substr(ts, 1, ?) AS bucket,
                   COUNT(*) AS requests,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens
            FROM usage_log
            WHERE model IN ({placeholders}) AND ts >= ?
            GROUP BY bucket
            """,
            (bucket_len, *models, since_iso),
        ).fetchall()
        return {
            r["bucket"]: {
                "requests": int(r["requests"] or 0),
                "prompt_tokens": int(r["prompt_tokens"] or 0),
                "completion_tokens": int(r["completion_tokens"] or 0),
            }
            for r in rows
        }
    finally:
        conn.close()
