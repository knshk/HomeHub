"""Application configuration.

Loads environment variables (optionally from a local ``.env`` file via
python-dotenv) and exposes a single, typed ``settings`` object plus the model
alias map used to translate client-facing model names into upstream Ollama
model tags.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict

from dotenv import load_dotenv

# Load variables from a .env file if present. Real environment variables take
# precedence over .env values (override=False).
load_dotenv(override=False)


def _get_str(name: str, default: str) -> str:
    """Return a string env var, falling back to ``default`` when unset/empty."""
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    """Return an integer env var, falling back to ``default`` on parse error."""
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# Default client-alias -> upstream-tag map. Extensible: add entries here or
# rely on pass-through for unknown names.
DEFAULT_ALIAS_MAP: Dict[str, str] = {
    "qwen2.5-7b": "qwen2.5:7b-instruct-q4_K_M",
}


@dataclass(frozen=True)
class Settings:
    """Typed, immutable view of runtime configuration."""

    gateway_host: str = field(default_factory=lambda: _get_str("GATEWAY_HOST", "0.0.0.0"))
    gateway_port: int = field(default_factory=lambda: _get_int("GATEWAY_PORT", 8080))
    ollama_base_url: str = field(
        default_factory=lambda: _get_str("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    )
    db_path: str = field(
        default_factory=lambda: _get_str(
            "DB_PATH", "/home/kanishka/kk_works/LLMs/qwen-stack/data/gateway.db"
        )
    )
    admin_token: str = field(default_factory=lambda: _get_str("ADMIN_TOKEN", ""))
    default_rpm: int = field(default_factory=lambda: _get_int("DEFAULT_RPM", 60))

    # Directory watched for drop-in *.gguf files (imported via `ollama create`).
    models_dir: str = field(
        default_factory=lambda: _get_str("MODELS_DIR", "/home/kanishka/kk_works/LLMs/models")
    )
    # Path to the ollama CLI (used to import GGUF files).
    ollama_bin: str = field(
        default_factory=lambda: _get_str("OLLAMA_BIN", os.path.expanduser("~/.local/bin/ollama"))
    )

    # Alias map is shared/mutable at module scope (see ALIAS_MAP) but referenced
    # here for convenience.
    alias_map: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ALIAS_MAP))


# Single shared instance imported across the app.
settings = Settings()

# Module-level alias map (the authoritative one used by routes). Kept separate
# so it can be extended at runtime if desired.
ALIAS_MAP: Dict[str, str] = settings.alias_map


def resolve_model(client_model: str) -> str:
    """Map a client-facing model name to its upstream Ollama tag.

    Unknown names pass through unchanged.
    """
    if not client_model:
        return client_model
    return ALIAS_MAP.get(client_model, client_model)
