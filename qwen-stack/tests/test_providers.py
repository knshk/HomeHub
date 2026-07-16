"""Offline unit tests for the cloud-provider layer (providers.py + db.py).

Everything runs against a temporary SQLite database (pytest ``tmp_path``); no
network, no Ollama, no FastAPI app. The ``settings`` objects imported by
``app.db`` and ``app.providers`` are monkeypatched so both the DB and the
Fernet key file land in the temp directory.
"""

from __future__ import annotations

import asyncio
import os
import stat
import types
from datetime import datetime, timezone

import pytest

from app import db, model_manager, providers


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Fresh schema in a temp dir; db + providers settings both repointed."""
    ns = types.SimpleNamespace(db_path=str(tmp_path / "gateway.db"))
    monkeypatch.setattr(db, "settings", ns)
    monkeypatch.setattr(providers, "settings", ns)
    db.init_db()
    return db


def _insert_usage(model: str, ts: str, prompt: int, completion: int) -> None:
    """Insert a usage row with an explicit timestamp (log_usage stamps 'now')."""
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO usage_log (key_id, ts, model, prompt_tokens, "
            "completion_tokens, status) VALUES (1, ?, ?, ?, ?, 200)",
            (ts, model, prompt, completion),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Migrations
# --------------------------------------------------------------------------- #
def test_fresh_schema_has_cloud_columns_and_seeded_providers(temp_db):
    conn = db.get_connection()
    try:
        model_cols = {r["name"] for r in conn.execute("PRAGMA table_info(managed_models)")}
        key_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_keys)")}
    finally:
        conn.close()
    assert {"provider", "upstream_model"} <= model_cols
    assert "cloud_allowed" in key_cols

    rows = {p["name"]: p for p in db.list_providers()}
    assert set(rows) == {"anthropic", "openai"}
    for p in rows.values():
        assert p["enabled"] == 0
        assert p["api_key_encrypted"] is None
        assert p["monthly_token_budget"] == 0


def test_migration_alters_legacy_tables(tmp_path, monkeypatch):
    """A pre-cloud database gets the new columns via guarded ALTERs."""
    ns = types.SimpleNamespace(db_path=str(tmp_path / "legacy.db"))
    monkeypatch.setattr(db, "settings", ns)
    conn = db.get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, key_prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                rpm_limit INTEGER NOT NULL DEFAULT 60,
                daily_token_limit INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE managed_models (
                alias TEXT PRIMARY KEY, ollama_tag TEXT NOT NULL,
                display_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'chat',
                state TEXT NOT NULL DEFAULT 'stopped',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    db.init_db()  # must add the columns without error
    db.init_db()  # and be idempotent

    conn = db.get_connection()
    try:
        model_cols = {r["name"] for r in conn.execute("PRAGMA table_info(managed_models)")}
        key_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_keys)")}
    finally:
        conn.close()
    assert {"provider", "upstream_model"} <= model_cols
    assert "cloud_allowed" in key_cols


def test_upsert_cloud_model_roundtrip(temp_db):
    db.upsert_model("claude-haiku", "claude-haiku-4-5", "Claude Haiku",
                    role="chat", state="running", provider="anthropic",
                    upstream_model="claude-haiku-4-5")
    row = db.get_model("claude-haiku")
    assert row["provider"] == "anthropic"
    assert row["upstream_model"] == "claude-haiku-4-5"
    assert row["state"] == "running"
    # Addressable by alias or by the provider-side model id.
    assert db.get_model_by_alias_or_tag("claude-haiku-4-5")["alias"] == "claude-haiku"


# --------------------------------------------------------------------------- #
# Fernet key custody
# --------------------------------------------------------------------------- #
def test_fernet_roundtrip_hint_and_file_perms(temp_db):
    plaintext = "sk-ant-api03-abcdef0123456789"
    hint = providers.set_key("anthropic", plaintext)
    assert providers.get_key("anthropic") == plaintext

    # Hint is masked: bounded ends only, never the whole key.
    assert hint == "sk-a…6789"
    assert plaintext not in hint

    # DB row holds ciphertext, not plaintext; the sanitised list shows the hint.
    row = db.get_provider("anthropic")
    assert plaintext.encode() not in bytes(row["api_key_encrypted"])
    listed = {p["name"]: p for p in providers.list_providers()}
    assert listed["anthropic"]["has_key"] is True
    assert listed["anthropic"]["key_hint"] == hint
    assert "api_key_encrypted" not in listed["anthropic"]

    # Key file created 0600 next to the DB.
    path = providers.key_file_path()
    assert os.path.dirname(path) == os.path.dirname(db.settings.db_path)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_mask_hint_short_keys():
    assert providers.mask_hint("abcd") == "…cd"
    assert providers.mask_hint("sk-live-1234567890") == "sk-l…7890"


def test_get_key_missing_returns_none(temp_db):
    assert providers.get_key("openai") is None


# --------------------------------------------------------------------------- #
# Translation: OpenAI -> Anthropic request
# --------------------------------------------------------------------------- #
def test_openai_to_anthropic_system_extraction_and_defaults():
    path, body = providers.openai_to_anthropic({
        "model": "claude-haiku-4-5",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "system", "content": [{"type": "text", "text": "Be kind."}]},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": [{"type": "text", "text": "part1 "},
                                         {"type": "text", "text": "part2"}]},
        ],
    })
    assert path == "/v1/messages"
    assert body["model"] == "claude-haiku-4-5"
    assert body["system"] == "Be brief.\n\nBe kind."
    assert body["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "part1 part2"},
    ]
    assert body["max_tokens"] == 4096      # default when absent
    assert body["stream"] is False


def test_openai_to_anthropic_explicit_fields():
    _, body = providers.openai_to_anthropic({
        "model": "m", "max_tokens": 128, "stream": True, "stop": "END",
        "messages": [{"role": "user", "content": "x"}],
    })
    assert body["max_tokens"] == 128
    assert body["stream"] is True
    assert body["stop_sequences"] == ["END"]
    assert "system" not in body


# --------------------------------------------------------------------------- #
# Translation: Anthropic -> OpenAI response
# --------------------------------------------------------------------------- #
def test_anthropic_to_openai_response_joins_text_and_maps_usage():
    out = providers.anthropic_to_openai_response({
        "id": "msg_123",
        "content": [
            {"type": "text", "text": "Hello "},
            {"type": "thinking", "thinking": "ignored"},
            {"type": "text", "text": "world"},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }, "my-alias")
    assert out["object"] == "chat.completion"
    assert out["model"] == "my-alias"
    choice = out["choices"][0]
    assert choice["message"] == {"role": "assistant", "content": "Hello world"}
    assert choice["finish_reason"] == "stop"
    assert out["usage"] == {"prompt_tokens": 11, "completion_tokens": 7,
                            "total_tokens": 18}


@pytest.mark.parametrize("stop_reason,expected", [
    ("end_turn", "stop"),
    ("max_tokens", "length"),
    ("refusal", "content_filter"),
    ("stop_sequence", "stop"),
    (None, "stop"),
])
def test_finish_reason_mapping(stop_reason, expected):
    out = providers.anthropic_to_openai_response(
        {"content": [], "stop_reason": stop_reason, "usage": {}}, "a")
    assert out["choices"][0]["finish_reason"] == expected


# --------------------------------------------------------------------------- #
# Translation: Anthropic SSE -> OpenAI chunks
# --------------------------------------------------------------------------- #
def test_sse_event_sequence_translates_to_chunk_stream():
    alias = "cloud-model"
    # message_start / content_block_start produce no client chunks.
    assert providers.anthropic_sse_to_openai_chunks(
        "message_start",
        {"type": "message_start",
         "message": {"usage": {"input_tokens": 9, "output_tokens": 0}}},
        alias) == []
    assert providers.anthropic_sse_to_openai_chunks(
        "content_block_start",
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}}, alias) == []

    # text deltas -> delta.content chunks.
    chunks = providers.anthropic_sse_to_openai_chunks(
        "content_block_delta",
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "Hel"}}, alias)
    assert len(chunks) == 1
    ch = chunks[0]
    assert ch["object"] == "chat.completion.chunk"
    assert ch["model"] == alias
    assert ch["choices"][0]["delta"] == {"content": "Hel"}
    assert ch["choices"][0]["finish_reason"] is None

    # non-text deltas (e.g. input_json_delta) are dropped.
    assert providers.anthropic_sse_to_openai_chunks(
        "content_block_delta",
        {"delta": {"type": "input_json_delta", "partial_json": "{"}}, alias) == []

    # message_delta -> final chunk with finish_reason + usage.
    chunks = providers.anthropic_sse_to_openai_chunks(
        "message_delta",
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"},
         "usage": {"output_tokens": 12}}, alias)
    assert len(chunks) == 1
    final = chunks[0]
    assert final["choices"][0]["finish_reason"] == "length"
    assert final["choices"][0]["delta"] == {}
    assert final["usage"]["completion_tokens"] == 12

    # message_stop -> None sentinel (emit 'data: [DONE]').
    assert providers.anthropic_sse_to_openai_chunks(
        "message_stop", {"type": "message_stop"}, alias) is None


def test_parse_sse_line_tracks_event_and_data():
    et, data = providers.parse_sse_line("event: content_block_delta", None)
    assert et == "content_block_delta" and data is None
    et, data = providers.parse_sse_line('data: {"type": "ping"}', et)
    assert et == "content_block_delta" and data == {"type": "ping"}
    et, data = providers.parse_sse_line("", et)
    assert data is None
    et, data = providers.parse_sse_line("data: not-json", et)
    assert data is None


# --------------------------------------------------------------------------- #
# Budget month-window math
# --------------------------------------------------------------------------- #
def test_month_usage_counts_only_this_month_and_this_provider(temp_db):
    db.upsert_model("claude", "claude-haiku-4-5", "Claude", state="running",
                    provider="anthropic", upstream_model="claude-haiku-4-5")
    db.upsert_model("gpt", "gpt-4.1-mini", "GPT", state="running",
                    provider="openai", upstream_model="gpt-4.1-mini")
    db.upsert_model("local-chat", "qwen2.5:7b", "Qwen", state="running")

    _insert_usage("claude", "2026-06-30T23:59:59+00:00", 100, 50)   # prev month
    _insert_usage("claude", "2026-07-01T00:00:00+00:00", 10, 5)     # in window
    _insert_usage("claude", "2026-07-15T12:00:00+00:00", 20, 4)     # in window
    _insert_usage("gpt", "2026-07-02T00:00:00+00:00", 999, 1)       # other provider
    _insert_usage("local-chat", "2026-07-03T00:00:00+00:00", 7, 7)  # local

    frozen = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
    assert providers.month_usage("anthropic", now=frozen) == 39
    assert providers.month_usage("openai", now=frozen) == 1000
    # A month later the window is empty again.
    assert providers.month_usage("anthropic",
                                 now=datetime(2026, 8, 1, tzinfo=timezone.utc)) == 0


def test_month_usage_counts_upstream_id_addressing(temp_db):
    """A caller may address a cloud model by its provider-side id (stored in
    ollama_tag); such usage rows must still count against the budget."""
    db.upsert_model("claude", "claude-haiku-4-5", "Claude", state="running",
                    provider="anthropic", upstream_model="claude-haiku-4-5")
    _insert_usage("claude", "2026-07-01T00:00:00+00:00", 10, 5)           # alias
    _insert_usage("claude-haiku-4-5", "2026-07-02T00:00:00+00:00", 30, 5)  # upstream id
    frozen = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
    assert providers.month_usage("anthropic", now=frozen) == 50


def test_budget_exceeded_semantics():
    assert providers.budget_exceeded({"monthly_token_budget": 0}, 10**9) is False
    assert providers.budget_exceeded({"monthly_token_budget": 100}, 99) is False
    assert providers.budget_exceeded({"monthly_token_budget": 100}, 100) is True
    assert providers.budget_exceeded({"monthly_token_budget": 100}, 101) is True


# --------------------------------------------------------------------------- #
# Gating decision helper (pure)
# --------------------------------------------------------------------------- #
def _provider_row(enabled=1, key=b"cipher", budget=0):
    return {"name": "anthropic", "enabled": enabled, "api_key_encrypted": key,
            "monthly_token_budget": budget}


def test_gate_blocks_key_without_cloud_optin():
    ok, code, msg = providers.gate_cloud_request(False, _provider_row(), 0)
    assert (ok, code) == (False, 403)
    assert msg == "This key is not allowed to use cloud models"


def test_gate_blocks_disabled_or_keyless_provider():
    ok, code, _ = providers.gate_cloud_request(True, _provider_row(enabled=0), 0)
    assert (ok, code) == (False, 503)
    ok, code, _ = providers.gate_cloud_request(True, _provider_row(key=None), 0)
    assert (ok, code) == (False, 503)
    ok, code, _ = providers.gate_cloud_request(True, None, 0)
    assert (ok, code) == (False, 503)


def test_gate_blocks_on_budget_and_allows_otherwise():
    ok, code, msg = providers.gate_cloud_request(True, _provider_row(budget=100), 100)
    assert (ok, code) == (False, 429)
    assert msg == "monthly cloud budget exhausted"
    ok, code, _ = providers.gate_cloud_request(True, _provider_row(budget=100), 99)
    assert ok is True
    ok, code, _ = providers.gate_cloud_request(True, _provider_row(), 10**9)
    assert ok is True  # budget 0 = unlimited


# --------------------------------------------------------------------------- #
# Cloud lifecycle actions never touch Ollama
# --------------------------------------------------------------------------- #
def test_cloud_model_actions_are_state_only(temp_db, monkeypatch):
    db.upsert_model("claude", "claude-haiku-4-5", "Claude", state="running",
                    provider="anthropic", upstream_model="claude-haiku-4-5")
    calls = []

    async def _record(tag):  # replaces both ollama side effects
        calls.append(tag)

    monkeypatch.setattr(model_manager, "ollama_load", _record)
    monkeypatch.setattr(model_manager, "ollama_unload", _record)

    async def scenario():
        row = await model_manager.apply_action("claude", "suspend")
        assert row["state"] == "suspended"
        row = await model_manager.apply_action("claude", "resume")
        assert row["state"] == "running"
        row = await model_manager.apply_action("claude", "shutdown")
        assert row["state"] == "stopped"
        row = await model_manager.apply_action("claude", "start")
        assert row["state"] == "running"

    asyncio.run(scenario())
    assert calls == []  # no Ollama warm-load/unload for cloud models

    # The admin kill-switch still blocks serving via the serve-gate.
    db.set_model_state("claude", "suspended")
    blocked = model_manager.serve_check("claude")
    assert blocked is not None and blocked[0] == 503


# --------------------------------------------------------------------------- #
# Anthropic shim: cloud aliases are rejected, never misrouted to Ollama
# --------------------------------------------------------------------------- #
def test_anthropic_shim_rejects_cloud_models(temp_db, monkeypatch):
    """/v1/messages forwards to Ollama, so a cloud alias must 400 (clear code)
    instead of silently misrouting. The rejection fires before any upstream
    HTTP, so this stays fully offline."""
    from fastapi.testclient import TestClient

    from app.auth import require_api_key
    from app.main import app

    db.upsert_model("claude", "claude-haiku-4-5", "Claude", state="running",
                    provider="anthropic", upstream_model="claude-haiku-4-5")
    app.dependency_overrides[require_api_key] = lambda: {"id": 1, "cloud_allowed": 1}
    try:
        with TestClient(app) as client:
            for name in ("claude", "claude-haiku-4-5"):  # alias AND upstream id
                r = client.post("/v1/messages", json={
                    "model": name, "max_tokens": 16,
                    "messages": [{"role": "user", "content": "hi"}],
                })
                assert r.status_code == 400
                assert r.json()["error"]["code"] == "cloud_chat_only"
    finally:
        app.dependency_overrides.pop(require_api_key, None)
