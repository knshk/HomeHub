"""Upstream integration: the gateway (chat + key mint/revoke) and Ollama
(embeddings + vision captions). The hub is a CLIENT only.

All functions raise HubError on upstream failure (fail closed).
"""
import base64
import json
from typing import AsyncGenerator

import httpx

from . import config
from .errors import HubError

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)


def _gateway_headers() -> dict:
    return {
        "Authorization": f"Bearer {config.HUB_GATEWAY_KEY}",
        "Content-Type": "application/json",
    }


def _admin_headers() -> dict:
    return {
        "Authorization": f"Bearer {config.HUB_ADMIN_TOKEN}",
        "Content-Type": "application/json",
    }


# ----------------------------------------------------------------------------
# Gateway: chat completions (non-streaming + streaming SSE passthrough)
# ----------------------------------------------------------------------------
async def chat_completion(messages: list[dict]) -> str:
    """Non-streaming chat completion. Returns the assistant text content."""
    url = f"{config.GATEWAY_URL}/v1/chat/completions"
    payload = {"model": config.CHAT_MODEL, "messages": messages, "stream": False}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, headers=_gateway_headers(), json=payload)
    except httpx.HTTPError as e:
        raise HubError(502, f"Gateway unreachable: {e}", "gateway_error")
    if r.status_code >= 400:
        raise HubError(502, f"Gateway error {r.status_code}: {r.text[:300]}", "gateway_error")
    try:
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise HubError(502, f"Bad gateway response: {e}", "gateway_error")


async def chat_completion_stream(messages: list[dict]) -> AsyncGenerator[str, None]:
    """Stream SSE chunks from the gateway. Yields raw SSE lines (text) to relay
    straight through to the browser. Also collects nothing here — the caller
    reconstructs assembled content separately if needed.

    Yields strings already terminated with the SSE framing (\n\n) so they can be
    written directly to a text/event-stream response.
    """
    url = f"{config.GATEWAY_URL}/v1/chat/completions"
    payload = {"model": config.CHAT_MODEL, "messages": messages, "stream": True}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream("POST", url, headers=_gateway_headers(), json=payload) as r:
                if r.status_code >= 400:
                    body = await r.aread()
                    raise HubError(502, f"Gateway error {r.status_code}: {body[:300]!r}", "gateway_error")
                async for line in r.aiter_lines():
                    if line is None:
                        continue
                    # Pass SSE "data:" lines straight through; keep blank-line framing.
                    if line.strip() == "":
                        continue
                    yield f"{line}\n\n"
    except httpx.HTTPError as e:
        raise HubError(502, f"Gateway unreachable: {e}", "gateway_error")


def extract_delta(sse_line: str) -> str:
    """Given a raw 'data: {...}' SSE line, extract incremental content text.
    Returns '' for [DONE] or unparsable lines. Used to persist assembled output.
    """
    line = sse_line.strip()
    if not line.startswith("data:"):
        return ""
    payload = line[len("data:"):].strip()
    if payload == "[DONE]" or not payload:
        return ""
    try:
        obj = json.loads(payload)
        return obj.get("choices", [{}])[0].get("delta", {}).get("content", "") or ""
    except Exception:
        return ""


# ----------------------------------------------------------------------------
# Gateway admin: mint / revoke per-user API keys
# ----------------------------------------------------------------------------
async def mint_key(name: str) -> dict:
    """POST /admin/keys -> returns gateway response. We expect plaintext key once.

    Returns a normalized dict: {"key": <plaintext>, "id": <key id>, "prefix": <prefix>}.
    """
    url = f"{config.GATEWAY_URL}/admin/keys"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, headers=_admin_headers(), json={"name": name})
    except httpx.HTTPError as e:
        raise HubError(502, f"Gateway unreachable: {e}", "gateway_error")
    if r.status_code >= 400:
        raise HubError(502, f"Gateway key mint failed {r.status_code}: {r.text[:300]}", "gateway_error")
    try:
        data = r.json()
    except Exception as e:
        raise HubError(502, f"Bad gateway key response: {e}", "gateway_error")

    # Be liberal about the gateway's field names.
    plaintext = data.get("key") or data.get("api_key") or data.get("plaintext") or data.get("token")
    key_id = data.get("id") or data.get("key_id") or data.get("kid")
    prefix = data.get("prefix") or data.get("key_prefix")
    if not plaintext:
        raise HubError(502, "Gateway did not return a plaintext key", "gateway_error")
    if not prefix:
        prefix = plaintext[:8]
    if key_id is None:
        key_id = str(prefix)
    return {"key": plaintext, "id": str(key_id), "prefix": str(prefix)}


async def revoke_key(gateway_key_id: str) -> None:
    url = f"{config.GATEWAY_URL}/admin/keys/{gateway_key_id}/revoke"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, headers=_admin_headers())
    except httpx.HTTPError as e:
        raise HubError(502, f"Gateway unreachable: {e}", "gateway_error")
    if r.status_code >= 400 and r.status_code != 404:
        raise HubError(502, f"Gateway key revoke failed {r.status_code}: {r.text[:300]}", "gateway_error")


# ----------------------------------------------------------------------------
# Gateway admin: model control plane proxy
# ----------------------------------------------------------------------------
# The gateway owns model lifecycle + metrics; the hub is a thin authenticated
# proxy so the family-facing admin UI can drive it without exposing the gateway
# ADMIN_TOKEN to the browser. The hub enforces admin-role + CSRF on its side.
async def gateway_admin_json(method: str, path: str, *,
                             json: dict | None = None,
                             params: dict | None = None):
    """Call a gateway ``/admin`` endpoint with the admin token.

    Returns the parsed JSON body. On an upstream >=400 it re-raises a HubError
    that preserves the gateway's status code, message and machine code so the
    browser sees a faithful error (e.g. 409 illegal transition).
    """
    url = f"{config.GATEWAY_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.request(method, url, headers=_admin_headers(),
                                     json=json, params=params)
    except httpx.HTTPError as e:
        raise HubError(502, f"Gateway unreachable: {e}", "gateway_error")

    try:
        data = r.json()
    except Exception:
        data = None

    if r.status_code >= 400:
        msg, code = "Gateway error", "gateway_error"
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            msg = data["error"].get("message") or msg
            code = data["error"].get("code") or code
        # Clamp to a sane client-facing status; unknown upstreams collapse to 502.
        sc = r.status_code if 400 <= r.status_code < 600 else 502
        raise HubError(sc, msg, code)
    return data


# ----------------------------------------------------------------------------
# Voice service admin proxy (STT/TTS control plane lives in the voice service)
# ----------------------------------------------------------------------------
async def voice_admin_json(method: str, path: str, *,
                           json: dict | None = None,
                           params: dict | None = None):
    """Call a voice-service ``/admin`` endpoint and return its JSON body.

    The voice service is localhost-only and unauthenticated (same trust model as
    its /transcribe /speak endpoints); the Hub enforces admin-role + CSRF before
    proxying here. Upstream >=400 is re-raised as a HubError preserving the code.
    """
    url = f"{config.VOICE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.request(method, url, json=json, params=params)
    except httpx.HTTPError as e:
        raise HubError(502, f"Voice service unreachable: {e}", "voice_error")
    try:
        data = r.json()
    except Exception:
        data = None
    if r.status_code >= 400:
        msg, code = "Voice service error", "voice_error"
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            msg = data["error"].get("message") or msg
            code = data["error"].get("code") or code
        sc = r.status_code if 400 <= r.status_code < 600 else 502
        raise HubError(sc, msg, code)
    return data


# ----------------------------------------------------------------------------
# Ollama: embeddings
# ----------------------------------------------------------------------------
async def embeddings(text: str) -> list[float]:
    """Embeddings via the gateway (so the embed model is gated + metered).

    Routes through GATEWAY_URL/api/embeddings rather than Ollama directly; the
    gateway proxies to Ollama and returns the same {embedding:[float,...]} shape.
    A suspended/stopped embed model surfaces as an upstream error here.
    """
    url = f"{config.GATEWAY_URL}/api/embeddings"
    payload = {"model": config.EMBED_MODEL, "prompt": text}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, headers=_gateway_headers(), json=payload)
    except httpx.HTTPError as e:
        raise HubError(502, f"Embeddings service unreachable: {e}", "ollama_error")
    if r.status_code >= 400:
        raise HubError(502, f"Embeddings error {r.status_code}: {r.text[:300]}", "ollama_error")
    try:
        data = r.json()
        emb = data.get("embedding")
        if not isinstance(emb, list) or not emb:
            raise ValueError("empty embedding")
        return [float(x) for x in emb]
    except Exception as e:
        raise HubError(502, f"Bad embeddings response: {e}", "ollama_error")


# ----------------------------------------------------------------------------
# Ollama: vision caption
# ----------------------------------------------------------------------------
# Phrasing matters for small VQA models: moondream returns empty for terse
# colon-list instructions but reliably answers a full descriptive sentence.
_CAPTION_PROMPT = "Describe this photo, including objects, scene, colors, and any visible text."


async def caption(image_bytes: bytes) -> str:
    """Vision caption via the gateway (so the vision model is gated + metered).

    Routes through GATEWAY_URL/api/generate; the gateway proxies to Ollama and
    returns the same {response} shape. A suspended/stopped vision model surfaces
    as an upstream error here.
    """
    url = f"{config.GATEWAY_URL}/api/generate"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": config.VISION_MODEL,
        "prompt": _CAPTION_PROMPT,
        "images": [b64],
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, headers=_gateway_headers(), json=payload)
    except httpx.HTTPError as e:
        raise HubError(502, f"Vision service unreachable: {e}", "ollama_error")
    if r.status_code >= 400:
        raise HubError(502, f"Vision error {r.status_code}: {r.text[:300]}", "ollama_error")
    try:
        data = r.json()
        return (data.get("response") or "").strip()
    except Exception as e:
        raise HubError(502, f"Bad vision response: {e}", "ollama_error")
