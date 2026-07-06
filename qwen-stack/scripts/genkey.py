#!/usr/bin/env python3
"""Tiny convenience wrapper to mint a gateway API key with defaults.

Usage:
    python3 scripts/genkey.py <name>

Creates an API key with default limits and prints the base_url, key and model
in a copy-paste-friendly block suitable for an app/client config. This reuses
adminctl's key-generation and DB logic so the schema and storage stay identical
to the gateway contract (key stored ONLY as sha256 hash; plaintext shown once).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the project root importable so we can reuse adminctl's helpers.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

import adminctl  # noqa: E402  (import after sys.path tweak)

# Default client-facing values. The base URL is where the gateway listens; the
# model is the client-facing alias from the contract's alias map.
DEFAULT_HOST = os.environ.get("GATEWAY_HOST", "0.0.0.0")
DEFAULT_PORT = os.environ.get("GATEWAY_PORT", "8080")
DEFAULT_MODEL = "qwen2.5-7b"


def public_host(host: str) -> str:
    """Map a bind-all host to a usable client host for display."""
    return "127.0.0.1" if host in ("0.0.0.0", "::") else host


def main(argv: list[str]) -> int:
    """Create a key with defaults and print a pasteable config block."""
    if len(argv) != 1:
        print("usage: python3 scripts/genkey.py <name>", file=sys.stderr)
        return 2

    name = argv[0]

    full_key = adminctl.generate_key()
    prefix = adminctl.key_prefix(full_key)
    khash = adminctl.hash_key(full_key)
    created = adminctl.now_iso()
    rpm = int(os.environ.get("DEFAULT_RPM", "60"))

    db_path = adminctl.get_db_path()
    conn = adminctl.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO api_keys "
            "(name, key_prefix, key_hash, created_at, revoked, rpm_limit, daily_token_limit) "
            "VALUES (?, ?, ?, ?, 0, ?, 0)",
            (name, prefix, khash, created, rpm),
        )
        conn.commit()
    finally:
        conn.close()

    base_url = f"http://{public_host(DEFAULT_HOST)}:{DEFAULT_PORT}/v1"

    print("=" * 64)
    print(f"Created key '{name}' (prefix {prefix}). Shown ONCE -- copy now:")
    print("=" * 64)
    print(f"base_url : {base_url}")
    print(f"api_key  : {full_key}")
    print(f"model    : {DEFAULT_MODEL}")
    print("=" * 64)
    print("# Example OpenAI-compatible client config:")
    print(f'#   OPENAI_BASE_URL="{base_url}"')
    print(f'#   OPENAI_API_KEY="{full_key}"')
    print(f'#   model="{DEFAULT_MODEL}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
