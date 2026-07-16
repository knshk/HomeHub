"""Anthropic Messages API compatibility shim.

Provides a best-effort POST /v1/messages endpoint that translates the Anthropic
Messages request shape into the OpenAI chat-completions shape understood by the
Ollama upstream, then translates the response back into Anthropic shape.

NOT TRANSLATED / UNSUPPORTED (best-effort shim only):
  * Tool use (``tools`` / ``tool_choice`` / ``tool_result`` content blocks).
  * Vision / image content blocks (only text content is forwarded).
  * Extended thinking, citations, and other Anthropic-specific block types.
Such inputs are coerced to their text portion (or ignored); structured
tool/vision semantics are dropped.

Authentication mirrors the OpenAI routes (Authorization: Bearer or x-api-key)
via the shared ``require_api_key`` dependency.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from . import db, model_manager
from .auth import openai_error, require_api_key
from .config import resolve_model, settings

router = APIRouter()

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


def _content_to_text(content: Any) -> str:
    """Flatten Anthropic message content (string or block list) into text.

    Only ``text`` blocks are kept. Tool/vision/other blocks are dropped — see
    the module docstring.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _to_openai_payload(anthropic_req: Dict[str, Any]) -> Dict[str, Any]:
    """Translate an Anthropic Messages request into an OpenAI chat payload."""
    messages: List[Dict[str, str]] = []

    system = anthropic_req.get("system")
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        sys_text = _content_to_text(system)
        if sys_text:
            messages.append({"role": "system", "content": sys_text})

    for msg in anthropic_req.get("messages", []) or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        # Anthropic roles are user/assistant; OpenAI accepts the same.
        text = _content_to_text(msg.get("content", ""))
        messages.append({"role": role, "content": text})

    payload: Dict[str, Any] = {
        "model": resolve_model(anthropic_req.get("model", "")),
        "messages": messages,
        "stream": bool(anthropic_req.get("stream", False)),
    }
    if anthropic_req.get("max_tokens") is not None:
        payload["max_tokens"] = anthropic_req["max_tokens"]
    if anthropic_req.get("temperature") is not None:
        payload["temperature"] = anthropic_req["temperature"]
    if anthropic_req.get("top_p") is not None:
        payload["top_p"] = anthropic_req["top_p"]
    if anthropic_req.get("stop_sequences") is not None:
        payload["stop"] = anthropic_req["stop_sequences"]
    return payload


def _map_stop_reason(openai_finish: Optional[str]) -> str:
    """Map an OpenAI finish_reason to an Anthropic stop_reason."""
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "content_filter": "stop_sequence",
        "tool_calls": "tool_use",
    }
    return mapping.get(openai_finish or "", "end_turn")


async def _read_json_body(request: Request) -> Dict[str, Any]:
    """Parse the request body as JSON or raise a 400 OpenAI-style error."""
    raw = await request.body()
    if not raw:
        raise openai_error(status.HTTP_400_BAD_REQUEST,
                           "Request body must be a JSON object.",
                           "invalid_request_error", "invalid_body")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise openai_error(status.HTTP_400_BAD_REQUEST,
                           "Request body is not valid JSON.",
                           "invalid_request_error", "invalid_json")
    if not isinstance(payload, dict):
        raise openai_error(status.HTTP_400_BAD_REQUEST,
                           "Request body must be a JSON object.",
                           "invalid_request_error", "invalid_body")
    return payload


@router.post("/v1/messages")
async def messages(request: Request,
                   key_row: Dict[str, Any] = Depends(require_api_key)) -> Any:
    """Anthropic Messages endpoint (best-effort translation to/from OpenAI)."""
    anthropic_req = await _read_json_body(request)

    client_model = anthropic_req.get("model", "")
    if not isinstance(client_model, str) or not client_model:
        raise openai_error(status.HTTP_400_BAD_REQUEST,
                           "Field 'model' is required.",
                           "invalid_request_error", "missing_model")

    # Serve-gate: managed models that are suspended/stopped are not served.
    blocked = model_manager.serve_check(client_model)
    if blocked:
        code_status, message, code = blocked
        raise openai_error(code_status, message, "service_unavailable", code)

    # Cloud-provider aliases cannot be served by this shim: it forwards to the
    # Ollama upstream, which does not know them. Reject explicitly instead of
    # silently misrouting (same stance as /v1/completions in openai_routes).
    model_row = db.get_model_by_alias_or_tag(client_model)
    if model_row is not None and (model_row.get("provider") or "local") != "local":
        raise openai_error(status.HTTP_400_BAD_REQUEST,
                           "Cloud models are only available via /v1/chat/completions.",
                           "invalid_request_error", "cloud_chat_only")

    is_stream = bool(anthropic_req.get("stream", False))
    payload = _to_openai_payload(anthropic_req)
    upstream_url = f"{settings.ollama_base_url}/v1/chat/completions"
    key_id = int(key_row["id"])

    if is_stream:
        return _anthropic_stream(upstream_url, payload, key_id, client_model)
    return await _anthropic_json(upstream_url, payload, key_id, client_model)


async def _anthropic_json(upstream_url: str, payload: Dict[str, Any],
                          key_id: int, client_model: str) -> JSONResponse:
    """Non-streaming: forward and translate OpenAI -> Anthropic response."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(upstream_url, json=payload)
    except httpx.TimeoutException:
        db.log_usage(key_id, client_model, 0, 0, status.HTTP_504_GATEWAY_TIMEOUT)
        raise openai_error(status.HTTP_504_GATEWAY_TIMEOUT, "Upstream timed out.",
                           "upstream_error", "upstream_timeout")
    except httpx.HTTPError:
        db.log_usage(key_id, client_model, 0, 0, status.HTTP_502_BAD_GATEWAY)
        raise openai_error(status.HTTP_502_BAD_GATEWAY,
                           "Failed to reach upstream model server.",
                           "upstream_error", "upstream_unavailable")

    if resp.status_code >= 400:
        try:
            body = resp.json()
        except ValueError:
            # Do not leak upstream error text to clients.
            body = {"error": {"message": "Upstream error.", "type": "upstream_error",
                              "code": None}}
        db.log_usage(key_id, client_model, 0, 0, resp.status_code)
        return JSONResponse(status_code=resp.status_code, content=body)

    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    finish = choice.get("finish_reason")
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)

    db.log_usage(key_id, client_model, prompt_tokens, completion_tokens,
                 resp.status_code)

    anthropic_resp = {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": client_model,
        "stop_reason": _map_stop_reason(finish),
        "stop_sequence": None,
        "usage": {"input_tokens": prompt_tokens, "output_tokens": completion_tokens},
    }
    return JSONResponse(content=anthropic_resp)


def _sse(event: str, data: Dict[str, Any]) -> bytes:
    """Encode an Anthropic SSE event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


def _anthropic_stream(upstream_url: str, payload: Dict[str, Any],
                      key_id: int, client_model: str) -> StreamingResponse:
    """Streaming: translate OpenAI SSE deltas into Anthropic SSE events."""
    payload = dict(payload)
    payload["stream"] = True
    # Ask upstream for usage in the final chunk when supported.
    payload.setdefault("stream_options", {"include_usage": True})

    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    async def gen():
        prompt_tokens, completion_tokens = 0, 0
        finish_reason: Optional[str] = None
        final_status = status.HTTP_200_OK
        started = False

        # message_start
        yield _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": client_model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })
        # content_block_start (single text block, index 0)
        yield _sse("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })
        started = True

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                async with client.stream("POST", upstream_url, json=payload) as resp:
                    final_status = resp.status_code
                    if resp.status_code >= 400:
                        # Drain the body to free the connection, but do not
                        # expose upstream error text to clients.
                        await resp.aread()
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta",
                                      "text": "[upstream error]"},
                        })
                    else:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_part = line[len("data: "):].strip()
                            if not data_part or data_part == "[DONE]":
                                continue
                            try:
                                chunk = json.loads(data_part)
                            except ValueError:
                                continue
                            if chunk.get("usage"):
                                u = chunk["usage"]
                                prompt_tokens = int(u.get("prompt_tokens") or 0) or prompt_tokens
                                completion_tokens = int(u.get("completion_tokens") or 0) or completion_tokens
                            choice = (chunk.get("choices") or [{}])[0]
                            delta = choice.get("delta") or {}
                            piece = delta.get("content")
                            if piece:
                                yield _sse("content_block_delta", {
                                    "type": "content_block_delta",
                                    "index": 0,
                                    "delta": {"type": "text_delta", "text": piece},
                                })
                            if choice.get("finish_reason"):
                                finish_reason = choice["finish_reason"]
        except httpx.TimeoutException:
            final_status = status.HTTP_504_GATEWAY_TIMEOUT
            yield _sse("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": "[upstream timed out]"},
            })
        except httpx.HTTPError:
            final_status = status.HTTP_502_BAD_GATEWAY
            yield _sse("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": "[upstream unavailable]"},
            })
        finally:
            if started:
                # content_block_stop
                yield _sse("content_block_stop",
                           {"type": "content_block_stop", "index": 0})
                # message_delta carries only delta/stop_reason/stop_sequence per
                # the Anthropic Messages spec (usage lives in message_start).
                yield _sse("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": _map_stop_reason(finish_reason),
                              "stop_sequence": None},
                })
                # message_stop
                yield _sse("message_stop", {"type": "message_stop"})
            db.log_usage(key_id, client_model, prompt_tokens, completion_tokens,
                         final_status)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
