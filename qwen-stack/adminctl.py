#!/usr/bin/env python3
"""Standalone admin CLI for the qwen-stack gateway.

This tool operates DIRECTLY on the gateway's SQLite database. It deliberately
does NOT import the FastAPI app; it only replicates the trivial, stable parts
of the shared contract:

    full key   = "qwsk-" + secrets.token_hex(20)   (40 lowercase hex chars)
    key_hash   = sha256 hex of the FULL key
    key_prefix = full_key[:13]                      (e.g. "qwsk-1a2b3c4d")

Keys are stored ONLY as their sha256 hash; the plaintext key is shown exactly
once, at creation time, and never again.

Subcommands:
    create  --name NAME [--rpm N] [--daily-limit N]
    list
    revoke  --id N
    usage   [--key-id N]

The database location is read from DB_PATH (env or .env), defaulting to
/home/kanishka/kk_works/LLMs/qwen-stack/data/gateway.db. The schema is created
if missing, matching the gateway contract exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# python-dotenv is an optional convenience here. If it is not installed we
# simply skip loading a .env file rather than failing the whole CLI.
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


DEFAULT_DB_PATH = "/home/kanishka/kk_works/LLMs/qwen-stack/data/gateway.db"

# Schema MUST match the gateway contract byte-for-byte in terms of columns,
# types, constraints and defaults.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    rpm_limit INTEGER NOT NULL DEFAULT 60,
    daily_token_limit INTEGER NOT NULL DEFAULT 0
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
"""


def get_db_path() -> str:
    """Return the configured database path (env/.env, else default)."""
    return os.environ.get("DB_PATH", DEFAULT_DB_PATH)


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def generate_key() -> str:
    """Generate a fresh plaintext API key per the contract.

    Format: literal prefix "qwsk-" followed by 40 lowercase hex chars.
    """
    return "qwsk-" + secrets.token_hex(20)


def hash_key(full_key: str) -> str:
    """Return the sha256 hex digest of the full plaintext key."""
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def key_prefix(full_key: str) -> str:
    """Return the 13-char display prefix (e.g. 'qwsk-1a2b3c4d')."""
    return full_key[:13]


def connect(db_path: str) -> sqlite3.Connection:
    """Open the SQLite database, creating parent dirs and schema as needed."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the gateway schema if it does not already exist."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


# --------------------------------------------------------------------------- #
# Subcommand handlers
# --------------------------------------------------------------------------- #
def cmd_create(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Create a new API key, printing the plaintext exactly once."""
    rpm = args.rpm if args.rpm is not None else int(os.environ.get("DEFAULT_RPM", "60"))
    daily = args.daily_limit if args.daily_limit is not None else 0

    full_key = generate_key()
    prefix = key_prefix(full_key)
    khash = hash_key(full_key)
    created = now_iso()

    try:
        cur = conn.execute(
            "INSERT INTO api_keys "
            "(name, key_prefix, key_hash, created_at, revoked, rpm_limit, daily_token_limit) "
            "VALUES (?, ?, ?, ?, 0, ?, ?)",
            (args.name, prefix, khash, created, rpm, daily),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        # Astronomically unlikely hash collision, or some constraint failure.
        print(f"error: failed to create key: {exc}", file=sys.stderr)
        return 1

    key_id = cur.lastrowid

    print("=" * 70)
    print("API KEY CREATED")
    print("=" * 70)
    print(f"  id      : {key_id}")
    print(f"  name    : {args.name}")
    print(f"  prefix  : {prefix}")
    print(f"  rpm     : {rpm}")
    print(f"  daily   : {daily} (0 = unlimited)")
    print(f"  created : {created}")
    print("-" * 70)
    print("  Plaintext key (copy it NOW):")
    print()
    print(f"      {full_key}")
    print()
    print("  !! WARNING: this key is shown ONCE and is NOT stored in plaintext.")
    print("  !! It cannot be recovered. If you lose it, revoke and create a new one.")
    print("=" * 70)
    return 0


def cmd_list(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Print a table of all keys: id/name/prefix/created/revoked/rpm."""
    rows = conn.execute(
        "SELECT id, name, key_prefix, created_at, revoked, rpm_limit "
        "FROM api_keys ORDER BY id"
    ).fetchall()

    if not rows:
        print("(no keys)")
        return 0

    header = f"{'ID':>4}  {'NAME':<20}  {'PREFIX':<14}  {'CREATED':<26}  {'REVOKED':<7}  {'RPM':>5}"
    print(header)
    print("-" * len(header))
    for r in rows:
        revoked = "yes" if r["revoked"] else "no"
        name = (r["name"] or "")[:20]
        print(
            f"{r['id']:>4}  {name:<20}  {r['key_prefix']:<14}  "
            f"{(r['created_at'] or ''):<26}  {revoked:<7}  {r['rpm_limit']:>5}"
        )
    return 0


def cmd_revoke(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Mark a key as revoked by id."""
    row = conn.execute(
        "SELECT id, name, key_prefix, revoked FROM api_keys WHERE id = ?",
        (args.id,),
    ).fetchone()
    if row is None:
        print(f"error: no key with id {args.id}", file=sys.stderr)
        return 1
    if row["revoked"]:
        print(f"key {args.id} ({row['key_prefix']}) is already revoked")
        return 0

    conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (args.id,))
    conn.commit()
    print(f"revoked key {args.id} ({row['name']}, {row['key_prefix']})")
    return 0


def cmd_usage(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Print recent usage rows and token totals, optionally filtered by key."""
    if args.key_id is not None:
        rows = conn.execute(
            "SELECT id, key_id, ts, model, prompt_tokens, completion_tokens, status "
            "FROM usage_log WHERE key_id = ? ORDER BY id DESC LIMIT 50",
            (args.key_id,),
        ).fetchall()
        totals = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens), 0) AS p, "
            "COALESCE(SUM(completion_tokens), 0) AS c, COUNT(*) AS n "
            "FROM usage_log WHERE key_id = ?",
            (args.key_id,),
        ).fetchone()
        scope = f"key_id={args.key_id}"
    else:
        rows = conn.execute(
            "SELECT id, key_id, ts, model, prompt_tokens, completion_tokens, status "
            "FROM usage_log ORDER BY id DESC LIMIT 50"
        ).fetchall()
        totals = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens), 0) AS p, "
            "COALESCE(SUM(completion_tokens), 0) AS c, COUNT(*) AS n "
            "FROM usage_log"
        ).fetchone()
        scope = "all keys"

    print(f"Recent usage ({scope}, newest first, up to 50 rows):")
    if not rows:
        print("  (no usage logged)")
    else:
        header = (
            f"{'ID':>6}  {'KEY':>5}  {'TS':<26}  {'MODEL':<28}  "
            f"{'PROMPT':>7}  {'COMPL':>7}  {'STATUS':>6}"
        )
        print(header)
        print("-" * len(header))
        for r in rows:
            print(
                f"{r['id']:>6}  {(r['key_id'] if r['key_id'] is not None else ''):>5}  "
                f"{(r['ts'] or ''):<26}  {(r['model'] or '')[:28]:<28}  "
                f"{(r['prompt_tokens'] or 0):>7}  {(r['completion_tokens'] or 0):>7}  "
                f"{(r['status'] if r['status'] is not None else ''):>6}"
            )

    print("-" * 40)
    p = totals["p"]
    c = totals["c"]
    print(f"Totals ({scope}): requests={totals['n']}  prompt_tokens={p}  "
          f"completion_tokens={c}  total_tokens={p + c}")
    return 0


# --------------------------------------------------------------------------- #
# Argument parsing / entrypoint
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="adminctl.py",
        description="Admin CLI for the qwen-stack gateway (operates directly on SQLite).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="create a new API key")
    p_create.add_argument("--name", required=True, help="human-readable name for the key")
    p_create.add_argument("--rpm", type=int, default=None,
                          help="requests-per-minute limit (default: DEFAULT_RPM env or 60)")
    p_create.add_argument("--daily-limit", type=int, default=None, dest="daily_limit",
                          help="daily token limit (default 0 = unlimited)")
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="list all keys")
    p_list.set_defaults(func=cmd_list)

    p_revoke = sub.add_parser("revoke", help="revoke a key by id")
    p_revoke.add_argument("--id", type=int, required=True, help="id of the key to revoke")
    p_revoke.set_defaults(func=cmd_revoke)

    p_usage = sub.add_parser("usage", help="show recent usage and token totals")
    p_usage.add_argument("--key-id", type=int, default=None, dest="key_id",
                         help="filter usage to a single key id")
    p_usage.set_defaults(func=cmd_usage)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    db_path = get_db_path()
    conn = connect(db_path)
    try:
        return args.func(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
