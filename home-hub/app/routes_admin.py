"""Admin routes: device approval / role + privilege assignment / revoke, plus
the model control plane (proxied to the gateway).

Only admins (role='admin', status='approved') may use these.
"""
from fastapi import APIRouter, Body, Depends, Request

from . import auth, config, db, image_ops, integration, services
from .errors import HubError

router = APIRouter(prefix="/api/admin", tags=["admin"])

_admin = auth.require_admin()


@router.get("/devices")
def list_devices(device=Depends(_admin)):
    conn = db.connect()
    try:
        rows = db.list_devices(conn)
        out = []
        for r in rows:
            d = auth.device_to_me(r)
            d.update({
                "id": r["id"],
                "created_at": r["created_at"],
                "last_seen": r["last_seen"],
            })
            out.append(d)
        return out
    finally:
        conn.close()


@router.post("/devices/{device_id}/approve")
def approve_device(device_id: int, payload: dict = Body(default={}), device=Depends(_admin)):
    role = (payload.get("role") or "").strip()
    privileges = payload.get("privileges")

    if role not in config.VALID_ROLES:
        raise HubError(400, f"role must be one of {config.VALID_ROLES}", "bad_request")

    if privileges is None:
        # Default privileges for the chosen role.
        privileges = list(config.ROLE_DEFAULT_PRIVILEGES[role])
    else:
        if not isinstance(privileges, list):
            raise HubError(400, "privileges must be a list", "bad_request")
        invalid = [p for p in privileges if p not in config.ALL_PRIVILEGES]
        if invalid:
            raise HubError(400, f"unknown privileges: {invalid}", "bad_request")

    conn = db.connect()
    try:
        target = db.get_device(conn, device_id)
        if target is None:
            raise HubError(404, "Device not found", "not_found")
        db.update_device(
            conn, device_id,
            role=role,
            status="approved",
            privileges=list(privileges),
        )
        updated = db.get_device(conn, device_id)
        d = auth.device_to_me(updated)
        d.update({"id": updated["id"], "created_at": updated["created_at"],
                  "last_seen": updated["last_seen"]})
        return d
    finally:
        conn.close()


@router.post("/devices/{device_id}/revoke")
def revoke_device(device_id: int, device=Depends(_admin)):
    conn = db.connect()
    try:
        target = db.get_device(conn, device_id)
        if target is None:
            raise HubError(404, "Device not found", "not_found")
        db.update_device(
            conn, device_id,
            status="revoked",
            role="guest",
            privileges=[],
        )
        updated = db.get_device(conn, device_id)
        d = auth.device_to_me(updated)
        d.update({"id": updated["id"], "created_at": updated["created_at"],
                  "last_seen": updated["last_seen"]})
        return d
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Model control plane (thin admin-gated proxy to the gateway).
# The _admin dependency enforces admin-role AND CSRF (on mutating methods).
# ---------------------------------------------------------------------------
_VALID_ACTIONS = {"start", "suspend", "resume", "shutdown"}


@router.get("/models")
async def list_models(device=Depends(_admin)):
    return await integration.gateway_admin_json("GET", "/admin/models")


@router.post("/models")
async def add_model(payload: dict = Body(default={}), device=Depends(_admin)):
    return await integration.gateway_admin_json("POST", "/admin/models", json=payload)


@router.get("/models/pull/status")
async def model_pull_status(tag: str, device=Depends(_admin)):
    return await integration.gateway_admin_json(
        "GET", "/admin/models/pull/status", params={"tag": tag})


@router.post("/models/pull")
async def model_pull(payload: dict = Body(default={}), device=Depends(_admin)):
    return await integration.gateway_admin_json("POST", "/admin/models/pull", json=payload)


@router.post("/models/scan")
async def scan_models(device=Depends(_admin)):
    return await integration.gateway_admin_json("POST", "/admin/models/scan")


@router.get("/models/import/status")
async def model_import_status(name: str, device=Depends(_admin)):
    return await integration.gateway_admin_json(
        "GET", "/admin/models/import/status", params={"name": name})


@router.get("/models/{alias}/metrics")
async def model_metrics(alias: str, hours: int = 24, bucket: str = "hour",
                        device=Depends(_admin)):
    return await integration.gateway_admin_json(
        "GET", f"/admin/models/{alias}/metrics",
        params={"hours": hours, "bucket": bucket})


@router.post("/models/{alias}/{action}")
async def model_action(alias: str, action: str, device=Depends(_admin)):
    if action not in _VALID_ACTIONS:
        raise HubError(400, f"Unknown action '{action}'", "bad_request")
    return await integration.gateway_admin_json(
        "POST", f"/admin/models/{alias}/{action}")


@router.delete("/models/{alias}")
async def delete_model(alias: str, device=Depends(_admin)):
    return await integration.gateway_admin_json("DELETE", f"/admin/models/{alias}")


@router.get("/resources")
async def resources(device=Depends(_admin)):
    return await integration.gateway_admin_json("GET", "/admin/resources")


@router.get("/ollama/installed")
async def ollama_installed(device=Depends(_admin)):
    return await integration.gateway_admin_json("GET", "/admin/ollama/installed")


# ---------------------------------------------------------------------------
# Cloud providers (BYO-key Anthropic/OpenAI) — thin admin-gated proxy to the
# gateway. Provider API keys are WRITE-ONLY: the gateway stores them encrypted
# and only ever returns a masked hint, never the key itself.
# ---------------------------------------------------------------------------
@router.get("/providers")
async def list_providers(device=Depends(_admin)):
    return await integration.gateway_admin_json("GET", "/admin/providers")


@router.put("/providers/{name}/key")
async def set_provider_key(name: str, payload: dict = Body(default={}), device=Depends(_admin)):
    return await integration.gateway_admin_json(
        "PUT", f"/admin/providers/{name}/key", json=payload)


@router.post("/providers/{name}/enable")
async def enable_provider(name: str, payload: dict = Body(default={}), device=Depends(_admin)):
    return await integration.gateway_admin_json(
        "POST", f"/admin/providers/{name}/enable", json=payload)


@router.put("/providers/{name}/budget")
async def set_provider_budget(name: str, payload: dict = Body(default={}), device=Depends(_admin)):
    return await integration.gateway_admin_json(
        "PUT", f"/admin/providers/{name}/budget", json=payload)


@router.post("/providers/{name}/models")
async def add_cloud_model(name: str, payload: dict = Body(default={}), device=Depends(_admin)):
    return await integration.gateway_admin_json(
        "POST", f"/admin/providers/{name}/models", json=payload)


@router.get("/gateway-keys")
async def gateway_keys(device=Depends(_admin)):
    """All gateway API keys (no secret material) — the UI reads cloud_allowed."""
    return await integration.gateway_admin_json("GET", "/admin/keys")


@router.post("/gateway-keys/{key_id}/cloud")
async def set_gateway_key_cloud(key_id: str, payload: dict = Body(default={}),
                                device=Depends(_admin)):
    return await integration.gateway_admin_json(
        "POST", f"/admin/keys/{key_id}/cloud", json=payload)


# ---------------------------------------------------------------------------
# Voice models (STT/TTS) — proxied to the voice service's control plane.
# ---------------------------------------------------------------------------
@router.get("/voice-models")
async def voice_models(device=Depends(_admin)):
    return await integration.voice_admin_json("GET", "/admin/models")


@router.get("/voice-models/{name}/metrics")
async def voice_model_metrics(name: str, hours: int = 24, device=Depends(_admin)):
    return await integration.voice_admin_json(
        "GET", f"/admin/models/{name}/metrics", params={"hours": hours})


@router.post("/voice-models/{name}/{action}")
async def voice_model_action(name: str, action: str, device=Depends(_admin)):
    if action not in _VALID_ACTIONS:
        raise HubError(400, f"Unknown action '{action}'", "bad_request")
    return await integration.voice_admin_json("POST", f"/admin/models/{name}/{action}")


@router.get("/voice-resources")
async def voice_resources(device=Depends(_admin)):
    return await integration.voice_admin_json("GET", "/admin/resources")


# ---------------------------------------------------------------------------
# Service control (start/stop the local model services) + image models.
# The hub stays up while it flips these; LLM<->Image are RAM-exclusive.
# ---------------------------------------------------------------------------
@router.get("/services")
def services_status(device=Depends(_admin)):
    return services.status()


@router.post("/services/{name}/{action}")
def service_action(name: str, action: str, device=Depends(_admin)):
    if action not in ("start", "stop"):
        raise HubError(400, "action must be start|stop", "bad_request")
    if name not in ("ai", "images"):
        raise HubError(400, "unknown service", "bad_request")
    try:
        (services.start if action == "start" else services.stop)(name)
    except Exception as e:
        raise HubError(500, f"service {action} failed: {str(e)[:120]}", "service_error")
    return {"ok": True, "service": name, "action": action}


@router.get("/image-models")
def list_image_models(device=Depends(_admin)):
    return services.image_models()


@router.post("/image-models/download")
def download_image_model(payload: dict = Body(default={}), device=Depends(_admin)):
    mid = str((payload or {}).get("id") or "").strip()
    if not mid:
        raise HubError(400, "id required", "bad_request")
    services.download_image_model(mid)
    return {"ok": True, "id": mid, "downloading": True}


@router.get("/models-catalog")
async def models_catalog(device=Depends(_admin)):
    """The AI models (chat/vision/embed + STT/TTS), live when the stack is up and
    last-known (cached, marked stopped by the caller) when it's down — so the
    Models page always lists them with a Start button."""
    st = services.status()
    gw_up = bool(st["ai"]["gateway"] and st["ai"]["ollama"])
    voice_up = bool(st["ai"]["voice"])
    cached = services.load_cached_ai_models()

    if gw_up:
        try:
            llm = (await integration.gateway_admin_json("GET", "/admin/models")).get("models", [])
        except Exception:
            llm, gw_up = cached.get("llm", []), False
    else:
        llm = cached.get("llm", [])

    if voice_up:
        try:
            voice = (await integration.voice_admin_json("GET", "/admin/models")).get("models", [])
        except Exception:
            voice, voice_up = cached.get("voice", []), False
    else:
        voice = cached.get("voice", [])

    # Persist whatever is currently live so it can be shown while stopped.
    services.cache_ai_models(llm if gw_up else cached.get("llm", []),
                             voice if voice_up else cached.get("voice", []))
    return {"gw_up": gw_up, "voice_up": voice_up, "llm": llm, "voice": voice}


@router.get("/resources-overview")
def resources_overview(device=Depends(_admin)):
    """Per-service + aggregate resources (disk always; RAM/CPU when running)."""
    return services.resources_overview()


@router.get("/generated-images")
def generated_images(device=Depends(_admin)):
    """List images FastSD wrote to its results/ dir (newest first) so the hub can
    show them directly — robust against the embedded gradio gallery not updating."""
    import glob
    import json as _json
    import os as _os
    d = config.FASTSD_RESULTS_DIR
    out = []
    if _os.path.isdir(d):
        pngs = sorted(glob.glob(_os.path.join(d, "*.png")),
                      key=_os.path.getmtime, reverse=True)[:60]
        for p in pngs:
            base = _os.path.basename(p)
            stem = _os.path.splitext(base)[0]          # uuid-1
            prompt = ""
            jf = _os.path.join(d, stem.rsplit("-", 1)[0] + ".json")  # uuid.json
            if _os.path.isfile(jf):
                try:
                    j = _json.load(open(jf, encoding="utf-8"))
                    s = j.get("lcm_diffusion_setting", j)
                    prompt = s.get("prompt", "") if isinstance(s, dict) else ""
                except Exception:
                    pass
            out.append({"file": base, "url": f"/generated-files/{base}",
                        "prompt": prompt, "mtime": int(_os.path.getmtime(p))})
    return {"images": out, "dir": d}


@router.post("/images/process")
def images_process(payload: dict = Body(default={}), device=Depends(_admin)):
    """Run a processing op on one or more generated images (drag-drop segments).
    Results land back in results/ (the ribbon) or the Studio."""
    op = str((payload or {}).get("op") or "")
    files = (payload or {}).get("files") or []
    prompt = str((payload or {}).get("prompt") or "")
    scale = int((payload or {}).get("scale") or 2)
    if not isinstance(files, list) or not files:
        raise HubError(400, "No images given", "bad_request")
    try:
        return image_ops.process(op, files, prompt, scale)
    except ValueError:
        raise HubError(400, "Unknown operation", "bad_request")


@router.get("/images/inflight")
def images_inflight(device=Depends(_admin)):
    return image_ops.inflight()


@router.post("/images/free-memory")
def images_free_memory(device=Depends(_admin)):
    """Restart the Image Studio to drop any resident model (frees RAM)."""
    services.free_image_memory()
    return {"ok": True, "restarting": True}
