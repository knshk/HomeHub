"""OpenAI-compatible routes.

Endpoints:
  * POST /v1/chat/completions  — chat completions (JSON or SSE streaming).
  * POST /v1/completions       — legacy text completions (JSON or streaming).
  * GET  /v1/models            — list client-facing aliases derived from
                                 upstream ``/api/tags``.
  * GET  /healthz              — report upstream reachability.

Flow for completion endpoints: authenticate -> rate-limit (handled in the auth
dependency) -> map the model alias -> forward to the Ollama OpenAI-compatible
upstream via httpx, streaming the SSE verbatim when ``stream == true`` -> log
usage.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from . import db, model_manager, providers
from .auth import openai_error, require_api_key
from .config import ALIAS_MAP, resolve_model, settings

router = APIRouter()

# Generous timeout: connect quickly, allow long generations to stream.
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


async def _read_json_body(request: Request) -> Dict[str, Any]:
    """Parse the request body as JSON or raise a 400 OpenAI-style error."""
    raw = await request.body()
    if not raw:
        raise openai_error(
            status.HTTP_400_BAD_REQUEST,
            "Request body must be a JSON object.",
            "invalid_request_error",
            "invalid_body",
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise openai_error(
            status.HTTP_400_BAD_REQUEST,
            "Request body is not valid JSON.",
            "invalid_request_error",
            "invalid_json",
        )
    if not isinstance(payload, dict):
        raise openai_error(
            status.HTTP_400_BAD_REQUEST,
            "Request body must be a JSON object.",
            "invalid_request_error",
            "invalid_body",
        )
    return payload


def _extract_usage(obj: Dict[str, Any]) -> Tuple[int, int]:
    """Return (prompt_tokens, completion_tokens) from a usage-bearing object."""
    usage = obj.get("usage") or {}
    if not isinstance(usage, dict):
        return 0, 0
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


async def _forward_completion(
    request: Request,
    key_row: Dict[str, Any],
    upstream_path: str,
) -> Any:
    """Shared handler for chat/legacy completion proxying."""
    payload = await _read_json_body(request)

    client_model = payload.get("model", "")
    if not isinstance(client_model, str) or not client_model:
        raise openai_error(
            status.HTTP_400_BAD_REQUEST,
            "Field 'model' is required.",
            "invalid_request_error",
            "missing_model",
        )

    # Serve-gate: managed models that are suspended/stopped are not served.
    # This applies to cloud models too (the admin kill-switch).
    blocked = model_manager.serve_check(client_model)
    if blocked:
        code_status, message, code = blocked
        raise openai_error(code_status, message, "service_unavailable", code)

    # Cloud routing seam: models registered against a cloud provider bypass the
    # Ollama upstream entirely and dispatch through providers.py.
    model_row = db.get_model_by_alias_or_tag(client_model)
    if model_row is not None and (model_row.get("provider") or "local") != "local":
        if upstream_path != "/v1/chat/completions":
            raise openai_error(
                status.HTTP_400_BAD_REQUEST,
                "Cloud models are only available via /v1/chat/completions.",
                "invalid_request_error",
                "cloud_chat_only",
            )
        return await _cloud_completion(payload, key_row, model_row, client_model)

    payload["model"] = resolve_model(client_model)
    is_stream = bool(payload.get("stream", False))
    upstream_url = f"{settings.ollama_base_url}{upstream_path}"
    key_id = int(key_row["id"])

    if is_stream:
        return await _stream_upstream(upstream_url, payload, key_id, client_model)
    return await _json_upstream(upstream_url, payload, key_id, client_model)


async def _json_upstream(
    upstream_url: str,
    payload: Dict[str, Any],
    key_id: int,
    client_model: str,
) -> JSONResponse:
    """Forward a non-streaming completion and return the JSON response."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(upstream_url, json=payload)
    except httpx.TimeoutException:
        db.log_usage(key_id, client_model, 0, 0, status.HTTP_504_GATEWAY_TIMEOUT)
        raise openai_error(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "Upstream timed out.",
            "upstream_error",
            "upstream_timeout",
        )
    except httpx.HTTPError:
        db.log_usage(key_id, client_model, 0, 0, status.HTTP_502_BAD_GATEWAY)
        raise openai_error(
            status.HTTP_502_BAD_GATEWAY,
            "Failed to reach upstream model server.",
            "upstream_error",
            "upstream_unavailable",
        )

    try:
        body = resp.json()
    except ValueError:
        # Do not leak upstream error text to clients.
        body = {"error": {"message": "Upstream error.", "type": "upstream_error", "code": None}}

    prompt_tokens, completion_tokens = 0, 0
    if isinstance(body, dict):
        prompt_tokens, completion_tokens = _extract_usage(body)
        # Present the client-facing model name back to the caller.
        if body.get("model"):
            body["model"] = client_model
        # Ensure usage carries total_tokens when upstream omits it.
        usage = body.get("usage")
        if isinstance(usage, dict) and usage.get("total_tokens") is None and (
            "prompt_tokens" in usage or "completion_tokens" in usage
        ):
            usage["total_tokens"] = (
                int(usage.get("prompt_tokens") or 0)
                + int(usage.get("completion_tokens") or 0)
            )

    db.log_usage(key_id, client_model, prompt_tokens, completion_tokens, resp.status_code)
    return JSONResponse(status_code=resp.status_code, content=body)


async def _stream_upstream(
    upstream_url: str,
    payload: Dict[str, Any],
    key_id: int,
    client_model: str,
) -> StreamingResponse:
    """Forward a streaming completion, passing SSE bytes through verbatim.

    Usage is parsed from the terminal chunk (when present) for logging.
    """

    async def event_generator():
        prompt_tokens, completion_tokens = 0, 0
        final_status = status.HTTP_200_OK
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                async with client.stream("POST", upstream_url, json=payload) as resp:
                    final_status = resp.status_code
                    if resp.status_code >= 400:
                        # Drain the body to free the connection, but do not
                        # expose upstream error text to clients.
                        await resp.aread()
                        err = {
                            "error": {
                                "message": "Upstream error.",
                                "type": "upstream_error",
                                "code": None,
                            }
                        }
                        yield f"data: {json.dumps(err)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    async for line in resp.aiter_lines():
                        # Inspect data lines for usage and rewrite the model tag
                        # to the client-facing alias, without buffering the body.
                        out_line = line
                        if line.startswith("data: "):
                            data_part = line[len("data: "):].strip()
                            if data_part and data_part != "[DONE]":
                                try:
                                    chunk = json.loads(data_part)
                                except (ValueError, TypeError):
                                    chunk = None
                                if isinstance(chunk, dict):
                                    if chunk.get("usage"):
                                        p, c = _extract_usage(chunk)
                                        prompt_tokens = p or prompt_tokens
                                        completion_tokens = c or completion_tokens
                                    # Present the client-facing model name back
                                    # to the caller in each streamed chunk.
                                    if "model" in chunk:
                                        chunk["model"] = client_model
                                    out_line = "data: " + json.dumps(chunk)
                        # SSE lines are newline-delimited; re-add the blank line
                        # separator that aiter_lines strips.
                        if out_line == "":
                            yield b"\n"
                        else:
                            yield (out_line + "\n").encode("utf-8")
        except httpx.TimeoutException:
            final_status = status.HTTP_504_GATEWAY_TIMEOUT
            err = {"error": {"message": "Upstream timed out.",
                             "type": "upstream_error", "code": "upstream_timeout"}}
            yield f"data: {json.dumps(err)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
        except httpx.HTTPError:
            final_status = status.HTTP_502_BAD_GATEWAY
            err = {"error": {"message": "Failed to reach upstream model server.",
                             "type": "upstream_error", "code": "upstream_unavailable"}}
            yield f"data: {json.dumps(err)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
        finally:
            db.log_usage(key_id, client_model, prompt_tokens, completion_tokens, final_status)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- #
# Cloud dispatch (BYO-key providers; see providers.py)
# --------------------------------------------------------------------------- #
def _provider_http_error(exc: providers.ProviderError) -> HTTPException:
    """Map a ProviderError onto an OpenAI-style HTTPException.

    Forwards retry-after on 429s so well-behaved clients can back off.
    """
    headers = {"retry-after": exc.retry_after} if exc.retry_after else None
    return HTTPException(
        status_code=exc.status_code,
        detail={"error": {"message": exc.message, "type": "upstream_error",
                          "code": exc.code}},
        headers=headers,
    )


async def _cloud_completion(
    payload: Dict[str, Any],
    key_row: Dict[str, Any],
    model_row: Dict[str, Any],
    client_model: str,
) -> Any:
    """Gate + dispatch a chat completion to a cloud provider.

    Gating order: per-key opt-in (403) -> provider enabled + key present (503)
    -> monthly budget (429). Usage is logged exactly like local traffic, with
    the client alias as the model name.
    """
    provider_name = model_row["provider"]
    provider_row = db.get_provider(provider_name)
    month_tokens = providers.month_usage(provider_name)
    ok, code_status, message = providers.gate_cloud_request(
        bool(key_row.get("cloud_allowed")), provider_row, month_tokens)
    if not ok:
        if code_status == status.HTTP_403_FORBIDDEN:
            raise openai_error(code_status, message, "cloud_not_allowed",
                               "cloud_not_allowed")
        if code_status == status.HTTP_429_TOO_MANY_REQUESTS:
            raise openai_error(code_status, message, "rate_limit_error",
                               "cloud_budget_exhausted")
        raise openai_error(code_status, message, "service_unavailable",
                           "provider_not_available")

    upstream_model = model_row.get("upstream_model") or model_row["ollama_tag"]
    if provider_name == "anthropic":
        _, body = providers.openai_to_anthropic({**payload, "model": upstream_model})
    else:
        # OpenAI upstream speaks our client protocol — passthrough, retagged.
        body = {**payload, "model": upstream_model}

    is_stream = bool(payload.get("stream", False))
    key_id = int(key_row["id"])
    if is_stream:
        return _cloud_stream(provider_row, provider_name, body, key_id, client_model)
    return await _cloud_json(provider_row, provider_name, body, key_id, client_model)


async def _cloud_json(
    provider_row: Dict[str, Any],
    provider_name: str,
    body: Dict[str, Any],
    key_id: int,
    client_model: str,
) -> JSONResponse:
    """Non-streaming cloud dispatch: forward, translate, log, respond."""
    try:
        resp_json = await providers.call_provider(provider_row, body, stream=False)
    except providers.ProviderError as exc:
        db.log_usage(key_id, client_model, 0, 0, exc.status_code)
        raise _provider_http_error(exc)

    if provider_name == "anthropic":
        out = providers.anthropic_to_openai_response(resp_json, client_model)
    else:
        out = resp_json if isinstance(resp_json, dict) else {}
        if out.get("model"):
            out["model"] = client_model

    prompt_tokens, completion_tokens = _extract_usage(out)
    db.log_usage(key_id, client_model, prompt_tokens, completion_tokens,
                 status.HTTP_200_OK)
    return JSONResponse(content=out)


def _cloud_stream(
    provider_row: Dict[str, Any],
    provider_name: str,
    body: Dict[str, Any],
    key_id: int,
    client_model: str,
) -> StreamingResponse:
    """Streaming cloud dispatch, emitting OpenAI-style SSE to the client.

    Anthropic upstream SSE is translated event-by-event; OpenAI upstream SSE is
    passed through with the model retagged. Usage is parsed for logging.
    """
    if provider_name == "openai":
        # Ask the upstream for usage in the terminal chunk.
        body = dict(body)
        body.setdefault("stream_options", {"include_usage": True})

    async def gen():
        prompt_tokens, completion_tokens = 0, 0
        final_status = status.HTTP_200_OK
        try:
            lines = await providers.call_provider(provider_row, body, stream=True)
            event_type: Optional[str] = None
            async for line in lines:
                if provider_name == "anthropic":
                    event_type, data_json = providers.parse_sse_line(line, event_type)
                    if data_json is None:
                        continue
                    et = event_type or str(data_json.get("type") or "")
                    if et == "message_start":
                        u = (data_json.get("message") or {}).get("usage") or {}
                        prompt_tokens = int(u.get("input_tokens") or 0) or prompt_tokens
                    elif et == "message_delta":
                        u = data_json.get("usage") or {}
                        completion_tokens = (int(u.get("output_tokens") or 0)
                                             or completion_tokens)
                    chunks = providers.anthropic_sse_to_openai_chunks(
                        et, data_json, client_model)
                    if chunks is None:
                        yield b"data: [DONE]\n\n"
                        break
                    for ch in chunks:
                        yield f"data: {json.dumps(ch)}\n\n".encode("utf-8")
                else:
                    # OpenAI passthrough: retag model, harvest usage, forward.
                    out_line = line
                    if line.startswith("data: "):
                        data_part = line[len("data: "):].strip()
                        if data_part and data_part != "[DONE]":
                            try:
                                chunk = json.loads(data_part)
                            except (ValueError, TypeError):
                                chunk = None
                            if isinstance(chunk, dict):
                                if chunk.get("usage"):
                                    p, c = _extract_usage(chunk)
                                    prompt_tokens = p or prompt_tokens
                                    completion_tokens = c or completion_tokens
                                if "model" in chunk:
                                    chunk["model"] = client_model
                                out_line = "data: " + json.dumps(chunk)
                    if out_line == "":
                        yield b"\n"
                    else:
                        yield (out_line + "\n").encode("utf-8")
        except providers.ProviderError as exc:
            final_status = exc.status_code
            err = {"error": {"message": exc.message, "type": "upstream_error",
                             "code": exc.code}}
            yield f"data: {json.dumps(err)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
        finally:
            db.log_usage(key_id, client_model, prompt_tokens, completion_tokens,
                         final_status)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request,
                           key_row: Dict[str, Any] = Depends(require_api_key)) -> Any:
    """Proxy to the upstream chat completions endpoint."""
    return await _forward_completion(request, key_row, "/v1/chat/completions")


@router.post("/v1/completions")
async def completions(request: Request,
                      key_row: Dict[str, Any] = Depends(require_api_key)) -> Any:
    """Proxy to the upstream legacy completions endpoint."""
    return await _forward_completion(request, key_row, "/v1/completions")


@router.get("/v1/models")
async def list_models(key_row: Dict[str, Any] = Depends(require_api_key)) -> JSONResponse:
    """List client-facing model aliases derived from upstream ``/api/tags``.

    Always includes configured aliases; appends upstream tags not already
    covered so callers can also address pass-through models directly.
    """
    upstream_tags: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                for m in (data.get("models") or []):
                    name = m.get("name") or m.get("model")
                    if name:
                        upstream_tags.append(name)
    except httpx.HTTPError:
        upstream_tags = []

    # Reverse alias map: upstream tag -> client alias.
    reverse = {v: k for k, v in ALIAS_MAP.items()}

    ids: list[str] = list(ALIAS_MAP.keys())
    for tag in upstream_tags:
        client_name = reverse.get(tag, tag)
        if client_name not in ids:
            ids.append(client_name)

    # Managed models (incl. cloud-provider entries with no Ollama tag upstream)
    # are always listed; the provider field lets UIs badge cloud models.
    provider_by_name: Dict[str, str] = {}
    for m in db.list_models():
        prov = m.get("provider") or "local"
        provider_by_name[m["alias"]] = prov
        provider_by_name[m["ollama_tag"]] = prov
        if m["alias"] not in ids:
            ids.append(m["alias"])

    created = int(time.time())
    data = [
        {"id": mid, "object": "model", "owned_by": "qwen-stack",
         "created": created, "provider": provider_by_name.get(mid, "local")}
        for mid in ids
    ]
    return JSONResponse(content={"object": "list", "data": data})


@router.post("/api/embeddings")
async def ollama_embeddings(request: Request,
                            key_row: Dict[str, Any] = Depends(require_api_key)) -> Any:
    """Gated + logged embeddings proxy (Ollama-native shape).

    Lets the Home Hub route RAG embeddings through the gateway so the embed model
    is subject to the same serve-gate and usage metrics as chat.
    """
    payload = await _read_json_body(request)
    model = payload.get("model", "")
    if not isinstance(model, str) or not model:
        raise openai_error(status.HTTP_400_BAD_REQUEST, "Field 'model' is required.",
                           "invalid_request_error", "missing_model")
    blocked = model_manager.serve_check(model)
    if blocked:
        cs, msg, code = blocked
        raise openai_error(cs, msg, "service_unavailable", code)

    key_id = int(key_row["id"])
    url = f"{settings.ollama_base_url}/api/embeddings"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
    except httpx.HTTPError:
        db.log_usage(key_id, model, 0, 0, status.HTTP_502_BAD_GATEWAY)
        raise openai_error(status.HTTP_502_BAD_GATEWAY,
                           "Failed to reach upstream model server.",
                           "upstream_error", "upstream_unavailable")
    # Embeddings responses carry no token counts; log the request for counts.
    db.log_usage(key_id, model, 0, 0, resp.status_code)
    try:
        body = resp.json()
    except ValueError:
        body = {"error": {"message": "Upstream error.", "type": "upstream_error", "code": None}}
    return JSONResponse(status_code=resp.status_code, content=body)


@router.post("/api/generate")
async def ollama_generate(request: Request,
                          key_row: Dict[str, Any] = Depends(require_api_key)) -> Any:
    """Gated + logged generate proxy (Ollama-native shape), used for the vision
    caption path. Non-streaming: token counts are logged from the reply."""
    payload = await _read_json_body(request)
    model = payload.get("model", "")
    if not isinstance(model, str) or not model:
        raise openai_error(status.HTTP_400_BAD_REQUEST, "Field 'model' is required.",
                           "invalid_request_error", "missing_model")
    blocked = model_manager.serve_check(model)
    if blocked:
        cs, msg, code = blocked
        raise openai_error(cs, msg, "service_unavailable", code)

    payload["stream"] = False  # captioning is single-shot
    key_id = int(key_row["id"])
    url = f"{settings.ollama_base_url}/api/generate"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
    except httpx.TimeoutException:
        db.log_usage(key_id, model, 0, 0, status.HTTP_504_GATEWAY_TIMEOUT)
        raise openai_error(status.HTTP_504_GATEWAY_TIMEOUT, "Upstream timed out.",
                           "upstream_error", "upstream_timeout")
    except httpx.HTTPError:
        db.log_usage(key_id, model, 0, 0, status.HTTP_502_BAD_GATEWAY)
        raise openai_error(status.HTTP_502_BAD_GATEWAY,
                           "Failed to reach upstream model server.",
                           "upstream_error", "upstream_unavailable")
    try:
        body = resp.json()
    except ValueError:
        body = {"error": {"message": "Upstream error.", "type": "upstream_error", "code": None}}
    p = int(body.get("prompt_eval_count") or 0) if isinstance(body, dict) else 0
    c = int(body.get("eval_count") or 0) if isinstance(body, dict) else 0
    db.log_usage(key_id, model, p, c, resp.status_code)
    return JSONResponse(status_code=resp.status_code, content=body)


@router.get("/healthz")
async def healthz() -> JSONResponse:
    """Report gateway health and upstream reachability (no auth)."""
    upstream_ok = False
    detail: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            upstream_ok = resp.status_code == 200
            if not upstream_ok:
                detail = f"upstream returned HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        detail = f"upstream unreachable: {exc.__class__.__name__}"

    body = {
        "status": "ok" if upstream_ok else "degraded",
        "upstream": settings.ollama_base_url,
        "upstream_reachable": upstream_ok,
    }
    if detail:
        body["detail"] = detail
    code = status.HTTP_200_OK if upstream_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=body)
