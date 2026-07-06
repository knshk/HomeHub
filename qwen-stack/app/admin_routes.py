"""Admin API and UI.

Mounted at ``/admin`` and protected by ``ADMIN_TOKEN`` presented either as
``Authorization: Bearer <token>`` or ``x-admin-token: <token>``.

JSON API:
  * POST /admin/keys            — create a key; returns the plaintext ONCE.
  * GET  /admin/keys            — list keys (no secrets).
  * POST /admin/keys/{id}/revoke— revoke a key.
  * GET  /admin/usage           — recent usage rows.
Plus GET /admin/ serving the vanilla-JS single-page admin console.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import auth, db, model_manager
from .config import settings

# Client-facing aliases become URL path segments, so keep them path-safe.
_ALIAS_RE = re.compile(r"^[A-Za-z0-9._-]+$")

router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "admin.html")


def _admin_error(status_code: int, message: str) -> HTTPException:
    """OpenAI-style error for admin endpoints."""
    return HTTPException(
        status_code=status_code,
        detail={"error": {"message": message, "type": "admin_error", "code": None}},
    )


async def require_admin(
    authorization: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
) -> bool:
    """Dependency: validate the admin token (fail-closed)."""
    if not settings.admin_token:
        raise _admin_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Admin API is disabled: ADMIN_TOKEN is not configured.",
        )
    presented: Optional[str] = None
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            presented = parts[1].strip()
    if not presented and x_admin_token:
        presented = x_admin_token.strip()

    import hmac

    if not presented or not hmac.compare_digest(presented, settings.admin_token):
        raise _admin_error(status.HTTP_401_UNAUTHORIZED, "Invalid admin token.")
    return True


class CreateKeyRequest(BaseModel):
    """Body for creating a new API key."""

    name: str = Field(..., min_length=1, max_length=200)
    rpm_limit: Optional[int] = Field(default=None, ge=0)
    daily_token_limit: int = Field(default=0, ge=0)


@router.get("/", include_in_schema=False)
async def admin_index() -> FileResponse:
    """Serve the admin single-page console (token prompted client-side)."""
    return FileResponse(_TEMPLATE_PATH, media_type="text/html")


@router.post("/keys")
async def create_key(body: CreateKeyRequest,
                     _: bool = Depends(require_admin)) -> JSONResponse:
    """Create a key and return the plaintext exactly once."""
    record = auth.create_key_record(
        name=body.name,
        rpm_limit=body.rpm_limit,
        daily_token_limit=body.daily_token_limit,
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "id": record["id"],
            "name": record["name"],
            "key_prefix": record["key_prefix"],
            "created_at": record["created_at"],
            "rpm_limit": record["rpm_limit"],
            "daily_token_limit": record["daily_token_limit"],
            "api_key": record["plaintext"],
            "warning": "Store this key now. It will not be shown again.",
        },
    )


@router.get("/keys")
async def get_keys(_: bool = Depends(require_admin)) -> JSONResponse:
    """List all keys without any secret material."""
    return JSONResponse(content={"keys": db.list_keys()})


@router.post("/keys/{key_id}/revoke")
async def revoke(key_id: int, _: bool = Depends(require_admin)) -> JSONResponse:
    """Revoke a key by id."""
    if not db.revoke_key(key_id):
        raise _admin_error(status.HTTP_404_NOT_FOUND, f"Key {key_id} not found.")
    return JSONResponse(content={"ok": True, "id": key_id, "revoked": True})


@router.get("/usage")
async def usage(limit: int = 200, _: bool = Depends(require_admin)) -> JSONResponse:
    """Return recent usage rows (newest first)."""
    limit = max(1, min(int(limit), 1000))
    return JSONResponse(content={"usage": db.list_usage(limit=limit)})


# --------------------------------------------------------------------------- #
# Model control plane
# --------------------------------------------------------------------------- #
class AddModelRequest(BaseModel):
    """Register a managed model. ``ollama_tag`` must be installed unless
    ``pull`` is set, in which case a background download is started."""

    alias: str = Field(..., min_length=1, max_length=100)
    ollama_tag: str = Field(..., min_length=1, max_length=200)
    display_name: Optional[str] = Field(default=None, max_length=200)
    role: Optional[str] = Field(default=None)  # chat|vision|embed; guessed if omitted
    pull: bool = Field(default=False)


def _model_error_to_http(exc: model_manager.ModelError) -> HTTPException:
    return _admin_error(exc.status_code, exc.message)


@router.get("/models")
async def list_models(_: bool = Depends(require_admin)) -> JSONResponse:
    """Managed models enriched with live loaded-status and 24h usage.

    Auto-reconciles first, so any model pulled into Ollama (CLI or elsewhere)
    that isn't dismissed shows up here without a manual 'Add'.
    """
    await model_manager.reconcile_registry()
    return JSONResponse(content={"models": await model_manager.list_models_enriched()})


@router.post("/models/scan")
async def scan_models(_: bool = Depends(require_admin)) -> JSONResponse:
    """Reconcile Ollama-installed models + import any new *.gguf from MODELS_DIR."""
    registered = await model_manager.reconcile_registry()
    imports = await model_manager.scan_models_dir()
    return JSONResponse(content={
        "registered": [{"alias": r["alias"], "ollama_tag": r["ollama_tag"], "role": r["role"]}
                       for r in registered],
        "imports": imports,
    })


@router.get("/models/import/status")
async def import_status(name: str, _: bool = Depends(require_admin)) -> JSONResponse:
    """Poll a background GGUF import."""
    return JSONResponse(content=model_manager.import_status(name))


@router.post("/models")
async def add_model(body: AddModelRequest,
                    _: bool = Depends(require_admin)) -> JSONResponse:
    """Register a new managed model (starts stopped)."""
    alias = body.alias.strip()
    tag = body.ollama_tag.strip()
    if not _ALIAS_RE.match(alias):
        raise _admin_error(status.HTTP_400_BAD_REQUEST,
                           "Alias may contain only letters, digits, '.', '_' and '-'.")
    if db.get_model(alias) is not None:
        raise _admin_error(status.HTTP_409_CONFLICT, f"Model '{alias}' already exists.")

    installed = {m["tag"] for m in await model_manager.ollama_installed()}
    pulling = False
    if tag not in installed:
        if body.pull:
            model_manager.start_pull(tag)
            pulling = True
        else:
            raise _admin_error(
                status.HTTP_400_BAD_REQUEST,
                f"Model tag '{tag}' is not installed. Pull it first or pass pull=true.",
            )

    db.undismiss_tag(tag)  # an explicit add overrides a prior removal
    role = body.role if body.role in ("chat", "vision", "embed") else model_manager.guess_role(tag)
    row = db.upsert_model(alias, tag, (body.display_name or alias).strip(),
                          role=role, state="stopped")
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={**row, "pulling": pulling},
    )


@router.post("/models/{alias}/{action}")
async def model_action(alias: str, action: str,
                       _: bool = Depends(require_admin)) -> JSONResponse:
    """Apply a lifecycle action: start | suspend | resume | shutdown."""
    if action not in ("start", "suspend", "resume", "shutdown"):
        raise _admin_error(status.HTTP_400_BAD_REQUEST, f"Unknown action '{action}'.")
    try:
        updated = await model_manager.apply_action(alias, action)
    except model_manager.ModelError as exc:
        raise _model_error_to_http(exc)
    return JSONResponse(content=updated)


@router.delete("/models/{alias}")
async def remove_model(alias: str, _: bool = Depends(require_admin)) -> JSONResponse:
    """Remove a model from the registry (and evict it from memory if resident)."""
    m = db.get_model(alias)
    if m is None:
        raise _admin_error(status.HTTP_404_NOT_FOUND, f"Model '{alias}' not found.")
    await model_manager.ollama_unload(m["ollama_tag"])
    db.delete_model(alias)
    db.dismiss_tag(m["ollama_tag"])  # keep auto-reconcile from re-adding it
    return JSONResponse(content={"ok": True, "alias": alias})


@router.get("/models/{alias}/metrics")
async def model_metrics(alias: str, hours: int = 24, bucket: str = "hour",
                        _: bool = Depends(require_admin)) -> JSONResponse:
    """Time-bucketed request/token histogram for one model."""
    m = db.get_model(alias)
    if m is None:
        raise _admin_error(status.HTTP_404_NOT_FOUND, f"Model '{alias}' not found.")
    hours = max(1, min(int(hours), 24 * 30))
    bucket = "day" if bucket == "day" else "hour"
    models = [m["alias"], m["ollama_tag"]]

    now = datetime.now(timezone.utc)
    if bucket == "day":
        blen, fmt = 10, "%Y-%m-%d"
        anchor = now.replace(hour=0, minute=0, second=0, microsecond=0)
        count = max(1, hours // 24)
        keys = [(anchor - timedelta(days=i)).strftime(fmt) for i in range(count)][::-1]
        since = (anchor - timedelta(days=count - 1)).isoformat()
    else:
        blen, fmt = 13, "%Y-%m-%dT%H"
        anchor = now.replace(minute=0, second=0, microsecond=0)
        count = hours
        keys = [(anchor - timedelta(hours=i)).strftime(fmt) for i in range(count)][::-1]
        since = (anchor - timedelta(hours=count - 1)).isoformat()

    grouped = db.model_usage_series(models, since, blen)
    zero = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
    series = [{"ts": k, **grouped.get(k, zero)} for k in keys]
    totals = db.model_usage_totals(models, since)
    return JSONResponse(content={
        "alias": alias, "bucket": bucket, "hours": hours,
        "series": series, "totals": totals,
    })


@router.get("/resources")
async def resources(_: bool = Depends(require_admin)) -> JSONResponse:
    """Live resource snapshot: Ollama process, system memory, resident models."""
    return JSONResponse(content=await model_manager.resources())


@router.get("/ollama/installed")
async def ollama_installed(_: bool = Depends(require_admin)) -> JSONResponse:
    """Installed Ollama tags, flagged with whether each is already registered."""
    registered = {m["ollama_tag"] for m in db.list_models()}
    items = await model_manager.ollama_installed()
    for it in items:
        it["registered"] = it["tag"] in registered
    return JSONResponse(content={"installed": items})


@router.post("/models/pull")
async def pull_model(body: Dict[str, Any],
                     _: bool = Depends(require_admin)) -> JSONResponse:
    """Start a background pull of an Ollama tag."""
    tag = str((body or {}).get("tag") or "").strip()
    if not tag:
        raise _admin_error(status.HTTP_400_BAD_REQUEST, "Field 'tag' is required.")
    return JSONResponse(content=model_manager.start_pull(tag))


@router.get("/models/pull/status")
async def pull_model_status(tag: str,
                            _: bool = Depends(require_admin)) -> JSONResponse:
    """Poll the status of a background pull."""
    return JSONResponse(content=model_manager.pull_status(tag))
