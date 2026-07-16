"""Cloud AI providers (BYO-key hybrid).

Local models stay the default; this module adds the optional cloud side:

  * **Key custody** — provider API keys are Fernet-encrypted at rest using a
    key file stored next to the SQLite database (``data/provider.key``, mode
    0600). Only a masked hint is ever returned outward.
  * **Budgets** — per-provider calendar-month token budgets read from
    ``usage_log`` (cloud usage is logged with the client alias as the model).
  * **Pure translation** between the OpenAI chat-completions shape our clients
    speak and the native Anthropic Messages API (no OpenAI-compat shim on the
    Anthropic side). Translation functions do no I/O so they are unit-testable.
  * **HTTP dispatch** via httpx with upstream error mapping (401 -> 502
    "provider auth failed", 429 passed through with retry-after, timeouts ->
    504).

Like ``model_manager``, this module is framework-agnostic (no FastAPI imports):
errors are raised as ``ProviderError`` and mapped to HTTP responses by routes.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx
from cryptography.fernet import Fernet, InvalidToken

from . import db
from .config import settings

# Same generous profile as the proxy path: connect fast, allow long generations.
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

# Anthropic Messages API version header (fixed, per the provider's docs).
ANTHROPIC_VERSION = "2023-06-01"

# Default endpoints; providers.base_url (admin-settable) overrides the host.
_PROVIDER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "anthropic": {"base_url": "https://api.anthropic.com", "path": "/v1/messages"},
    "openai": {"base_url": "https://api.openai.com", "path": "/v1/chat/completions"},
}

# OpenAI finish_reason <- Anthropic stop_reason.
_STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "refusal": "content_filter",
    "stop_sequence": "stop",
}

# Default Anthropic max_tokens when the OpenAI request omits it (the Messages
# API requires the field; OpenAI clients frequently leave it out).
DEFAULT_MAX_TOKENS = 4096


class ProviderError(Exception):
    """Cloud dispatch error carrying an HTTP status, message and machine code.

    ``retry_after`` (seconds, as received from upstream) is set on 429s so the
    route can forward the header to the client.
    """

    def __init__(self, status_code: int, message: str, code: str,
                 retry_after: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.retry_after = retry_after


# --------------------------------------------------------------------------- #
# Key custody (Fernet at rest; hint-only outward)
# --------------------------------------------------------------------------- #
def key_file_path() -> str:
    """Path of the Fernet key file: alongside the SQLite database."""
    parent = os.path.dirname(settings.db_path) or "."
    return os.path.join(parent, "provider.key")


def _load_or_create_fernet() -> Fernet:
    """Load the Fernet key, creating it with mode 0600 on first use."""
    path = key_file_path()
    if not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        key = Fernet.generate_key()
        # O_EXCL so a concurrent first-writer wins cleanly; created mode 0600.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
    with open(path, "rb") as fh:
        return Fernet(fh.read().strip())


def mask_hint(api_key: str) -> str:
    """Return a short display hint that never reveals the whole key."""
    if len(api_key) <= 8:
        return "…" + api_key[-2:]
    return f"{api_key[:4]}…{api_key[-4:]}"


def set_key(name: str, api_key: str) -> str:
    """Encrypt and store a provider API key; returns the masked hint."""
    hint = mask_hint(api_key)
    token = _load_or_create_fernet().encrypt(api_key.encode("utf-8"))
    if not db.set_provider_key(name, token, hint):
        raise ProviderError(404, f"Provider '{name}' is not registered.",
                            "provider_not_found")
    return hint


def get_key(name: str) -> Optional[str]:
    """Decrypt and return a provider's API key (internal use only)."""
    row = db.get_provider(name)
    if not row or not row.get("api_key_encrypted"):
        return None
    try:
        return _load_or_create_fernet().decrypt(
            bytes(row["api_key_encrypted"])).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def enable(name: str, enabled: bool) -> bool:
    """Flip the admin opt-in switch for a provider."""
    return db.set_provider_enabled(name, enabled)


def set_budget(name: str, monthly_token_budget: int) -> bool:
    """Set the calendar-month token budget (0 = unlimited)."""
    return db.set_provider_budget(name, monthly_token_budget)


def list_providers() -> List[Dict[str, Any]]:
    """Sanitised provider view for the admin API: hint only, never key bytes."""
    out: List[Dict[str, Any]] = []
    for p in db.list_providers():
        out.append({
            "name": p["name"],
            "enabled": bool(p["enabled"]),
            "has_key": bool(p.get("api_key_encrypted")),
            "key_hint": p.get("key_hint"),
            "monthly_token_budget": int(p.get("monthly_token_budget") or 0),
            "base_url": p.get("base_url"),
        })
    return out


# --------------------------------------------------------------------------- #
# Budgets (calendar-month window over usage_log)
# --------------------------------------------------------------------------- #
def month_usage(provider: str, now: Optional[datetime] = None) -> int:
    """Tokens used this calendar month by all models of ``provider``.

    ``now`` is injectable so tests can freeze the window boundary.
    """
    now = now or datetime.now(timezone.utc)
    return db.provider_month_usage(provider, now.strftime("%Y-%m"))


def budget_exceeded(provider_row: Dict[str, Any], month_tokens: int) -> bool:
    """True when a non-zero budget is met or exceeded (0 = unlimited)."""
    budget = int(provider_row.get("monthly_token_budget") or 0)
    return budget > 0 and month_tokens >= budget


def gate_cloud_request(
    cloud_allowed: bool,
    provider_row: Optional[Dict[str, Any]],
    month_tokens: int,
) -> Tuple[bool, int, str]:
    """Pure gating decision for a cloud request -> (ok, status, message).

    Checks, in order: the per-key opt-in, provider enabled + key present, and
    the monthly budget. No I/O — callers supply the row and the usage figure.
    """
    if not cloud_allowed:
        return (False, 403, "This key is not allowed to use cloud models")
    if (provider_row is None or not int(provider_row.get("enabled") or 0)
            or not provider_row.get("api_key_encrypted")):
        return (False, 503, "Cloud provider is not enabled or has no API key configured.")
    if budget_exceeded(provider_row, month_tokens):
        return (False, 429, "monthly cloud budget exhausted")
    return (True, 200, "ok")


# --------------------------------------------------------------------------- #
# Pure translation: OpenAI chat <-> Anthropic Messages
# --------------------------------------------------------------------------- #
def _openai_content_to_text(content: Any) -> str:
    """Flatten OpenAI message content (string or part list) into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "".join(parts)
    return ""


def openai_to_anthropic(payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Translate an OpenAI chat payload into an Anthropic Messages request.

    Returns ``(url_path, body)``. System messages are extracted into the
    top-level ``system`` string; user/assistant turns keep string content;
    ``max_tokens`` defaults to 4096 (required by the Messages API); the stream
    flag is carried over. Tool/vision semantics are dropped (best-effort, same
    stance as the Anthropic shim in ``anthropic_routes``).
    """
    system_parts: List[str] = []
    messages: List[Dict[str, str]] = []
    for msg in payload.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        text = _openai_content_to_text(msg.get("content"))
        if role == "system":
            if text:
                system_parts.append(text)
        elif role in ("user", "assistant"):
            messages.append({"role": role, "content": text})
        # other roles (tool/function) are dropped — see docstring.

    body: Dict[str, Any] = {
        "model": payload.get("model", ""),
        "messages": messages,
        "max_tokens": int(payload.get("max_tokens") or DEFAULT_MAX_TOKENS),
        "stream": bool(payload.get("stream", False)),
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    stop = payload.get("stop")
    if stop is not None:
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    return "/v1/messages", body


def _map_finish(stop_reason: Optional[str]) -> str:
    """Map an Anthropic stop_reason to an OpenAI finish_reason."""
    return _STOP_REASON_MAP.get(stop_reason or "", "stop")


def anthropic_to_openai_response(resp_json: Dict[str, Any],
                                 alias: str) -> Dict[str, Any]:
    """Translate an Anthropic Messages response into an OpenAI chat completion.

    Text blocks are joined into ``choices[0].message.content``; usage maps
    input/output tokens to prompt/completion; stop reasons map end_turn->stop,
    max_tokens->length, refusal->content_filter.
    """
    text = "".join(
        str(b.get("text", ""))
        for b in (resp_json.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "text"
    )
    usage = resp_json.get("usage") or {}
    prompt_tokens = int(usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or 0)
    return {
        "id": f"chatcmpl-{resp_json.get('id') or uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": alias,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": _map_finish(resp_json.get("stop_reason")),
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _openai_chunk(alias: str, delta: Dict[str, Any],
                  finish_reason: Optional[str]) -> Dict[str, Any]:
    """Build one OpenAI chat.completion.chunk envelope."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": alias,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def anthropic_sse_to_openai_chunks(
    event_type: str,
    data_json: Dict[str, Any],
    alias: str,
) -> Optional[List[Dict[str, Any]]]:
    """Translate one Anthropic SSE event into OpenAI streaming chunks.

    Returns a (possibly empty) list of chunk dicts, or ``None`` for
    ``message_stop`` — the sentinel meaning "emit ``data: [DONE]``".
    """
    if event_type == "message_stop":
        return None
    chunks: List[Dict[str, Any]] = []
    if event_type == "content_block_delta":
        delta = data_json.get("delta") or {}
        if delta.get("type") == "text_delta" and delta.get("text"):
            chunks.append(_openai_chunk(alias, {"content": delta["text"]}, None))
    elif event_type == "message_delta":
        d = data_json.get("delta") or {}
        usage = data_json.get("usage") or {}
        prompt_tokens = int(usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or 0)
        chunk = _openai_chunk(alias, {}, _map_finish(d.get("stop_reason")))
        chunk["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        chunks.append(chunk)
    # message_start / content_block_start / content_block_stop / ping -> [].
    return chunks


# --------------------------------------------------------------------------- #
# HTTP dispatch
# --------------------------------------------------------------------------- #
def _request_target(provider_row: Dict[str, Any],
                    api_key: str) -> Tuple[str, Dict[str, str]]:
    """Resolve (url, headers) for a provider, honouring base_url overrides."""
    name = provider_row["name"]
    defaults = _PROVIDER_DEFAULTS.get(name)
    if defaults is None:
        raise ProviderError(503, f"Unknown cloud provider '{name}'.",
                            "provider_unknown")
    base = (provider_row.get("base_url") or defaults["base_url"]).rstrip("/")
    url = f"{base}{defaults['path']}"
    if name == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    else:
        headers = {"Authorization": f"Bearer {api_key}"}
    return url, headers


def _error_for_status(resp: httpx.Response) -> ProviderError:
    """Map an upstream error status onto a client-facing ProviderError.

    Upstream error text is never forwarded to clients (same stance as the
    Ollama proxy path).
    """
    if resp.status_code == 401:
        return ProviderError(502, "provider auth failed", "provider_auth_failed")
    if resp.status_code == 429:
        return ProviderError(429, "Cloud provider rate limit exceeded.",
                             "provider_rate_limited",
                             retry_after=resp.headers.get("retry-after"))
    return ProviderError(502, f"Cloud provider error (HTTP {resp.status_code}).",
                         "provider_error")


async def _stream_lines(url: str, headers: Dict[str, str],
                        body: Dict[str, Any]) -> AsyncIterator[str]:
    """Yield raw SSE lines from the provider, mapping transport errors."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream("POST", url, headers=headers,
                                     json=body) as resp:
                if resp.status_code >= 400:
                    # Drain to free the connection; never leak upstream text.
                    await resp.aread()
                    raise _error_for_status(resp)
                async for line in resp.aiter_lines():
                    yield line
    except httpx.TimeoutException:
        raise ProviderError(504, "Cloud provider timed out.", "provider_timeout")
    except httpx.HTTPError:
        raise ProviderError(502, "Failed to reach cloud provider.",
                            "provider_unavailable")


async def call_provider(provider_row: Dict[str, Any], body: Dict[str, Any],
                        stream: bool = False) -> Any:
    """Dispatch a translated request to the provider.

    Non-streaming: returns the parsed JSON response body. Streaming: returns an
    async iterator of raw SSE lines (errors surface as ``ProviderError`` during
    iteration). ``body`` must already be in the provider's native shape.
    """
    api_key = get_key(provider_row["name"])
    if not api_key:
        raise ProviderError(503, "Cloud provider has no API key configured.",
                            "provider_not_configured")
    url, headers = _request_target(provider_row, api_key)

    if stream:
        return _stream_lines(url, headers, {**body, "stream": True})

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        raise ProviderError(504, "Cloud provider timed out.", "provider_timeout")
    except httpx.HTTPError:
        raise ProviderError(502, "Failed to reach cloud provider.",
                            "provider_unavailable")
    if resp.status_code >= 400:
        raise _error_for_status(resp)
    try:
        return resp.json()
    except ValueError:
        raise ProviderError(502, "Cloud provider returned invalid JSON.",
                            "provider_error")


def parse_sse_line(line: str, current_event: Optional[str]
                   ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Incremental SSE parser for Anthropic streams.

    Feed each raw line with the last seen event name; returns the (possibly
    updated) event name and the parsed data payload when the line carries one.
    """
    if line.startswith("event: "):
        return line[len("event: "):].strip(), None
    if line.startswith("data: "):
        data_part = line[len("data: "):].strip()
        if data_part:
            try:
                parsed = json.loads(data_part)
            except ValueError:
                return current_event, None
            if isinstance(parsed, dict):
                return current_event, parsed
    return current_event, None
