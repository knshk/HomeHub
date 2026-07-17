"""Smart Home routes (🏡 Smart Home, hybrid local-first control).

Endpoints under /api/home. Everything is inert until an admin connects a
provider: status/list endpoints return an empty "not set up" shape rather than
erroring, so the Home tab always renders.

Auth model (skeleton):
  * reads (status, entities, rooms, favourites) -> any approved device
  * connect / disconnect / sync / permissions -> admin only
  * control an entity          -> admin always; other devices need an explicit
                                  per-entity grant (smarthome_store.can_control)

The provider secret (HA token) is written to the encrypted secret store and is
never returned by any endpoint (a masked hint is the most that leaks).
"""
from fastapi import APIRouter, Body, Depends

from . import auth, secrets_store, smarthome, smarthome_store
from .errors import HubError

router = APIRouter(prefix="/api/home", tags=["smarthome"])

_admin = auth.require_admin()


def _bad_request(e: ValueError) -> HubError:
    return HubError(400, str(e), "bad_request")


# ---------------------------------------------------------------------------
# Status & config
# ---------------------------------------------------------------------------
@router.get("/status")
def status(device=Depends(auth.require_authenticated)):
    """Lightweight status for the Home tab: is the feature on, is a provider
    configured/connected, how many entities are cached."""
    cfg = smarthome_store.get_config()
    entities = smarthome_store.list_entities() if cfg["configured"] else []
    return {
        "enabled": smarthome.enabled(),
        "configured": cfg["configured"],
        "connected": cfg["connected"],
        "provider": cfg["provider"],
        "entity_count": len(entities),
        "last_synced_at": cfg["last_synced_at"],
        "last_error": cfg["last_error"],
        "is_admin": device["role"] == "admin",
    }


@router.get("/config")
def get_config(device=Depends(_admin)):
    """Admin view of the provider config. Token is never returned; only a hint
    from the encrypted store."""
    cfg = smarthome_store.get_config()
    hint = None
    for s in secrets_store.list_secrets(smarthome.SECRET_NAMESPACE):
        if s["name"] == smarthome.SECRET_NAME:
            hint = s["hint"]
            break
    return {**cfg, "providers": list(smarthome.PROVIDERS), "token_hint": hint}


@router.post("/connect")
async def connect(payload: dict = Body(default={}), device=Depends(_admin)):
    """Configure + test a provider. Stores the token encrypted, saves config,
    then probes the provider and (on success) syncs the entity cache."""
    provider = (payload.get("provider") or "home_assistant").strip()
    base_url = payload.get("base_url")
    token = payload.get("token")
    if provider not in smarthome.PROVIDERS:
        raise HubError(400, f"unknown provider '{provider}'", "bad_request")
    if not token:
        raise HubError(400, "token is required", "bad_request")
    try:
        base_url = smarthome.require_lan_url(base_url)
    except ValueError as e:
        raise _bad_request(e)

    # Persist config + secret first so a failed probe is retryable.
    secrets_store.set_secret(smarthome.SECRET_NAMESPACE, smarthome.SECRET_NAME, token)
    smarthome_store.set_config(provider, base_url)

    prov = smarthome.get_provider()
    if prov is None:  # should not happen right after set_config, but stay safe
        raise HubError(500, "provider could not be constructed", "internal_error")
    try:
        await prov.test_connection()
        entities = await prov.fetch_states()
        smarthome_store.replace_entities(entities)
        smarthome_store.mark_connection(True, None, synced=True)
    except smarthome.SmartHomeError as e:
        smarthome_store.mark_connection(False, str(e))
        raise HubError(502, f"saved, but could not reach the provider: {e}",
                       "provider_unreachable")
    return status(device)


@router.post("/disconnect")
def disconnect(device=Depends(_admin)):
    smarthome_store.clear_config()
    secrets_store.delete_secret(smarthome.SECRET_NAMESPACE, smarthome.SECRET_NAME)
    return {"ok": True}


@router.post("/sync")
async def sync(device=Depends(_admin)):
    """Re-poll the provider and refresh the entity cache."""
    prov = smarthome.get_provider()
    if prov is None:
        raise HubError(409, "no smart-home provider is connected", "not_connected")
    try:
        entities = await prov.fetch_states()
        count = smarthome_store.replace_entities(entities)
        smarthome_store.mark_connection(True, None, synced=True)
    except smarthome.SmartHomeError as e:
        smarthome_store.mark_connection(False, str(e))
        raise HubError(502, str(e), "provider_unreachable")
    return {"ok": True, "entity_count": count}


# ---------------------------------------------------------------------------
# Entities & rooms (read)
# ---------------------------------------------------------------------------
@router.get("/entities")
def list_entities(domain: str | None = None,
                  device=Depends(auth.require_authenticated)):
    is_admin = device["role"] == "admin"
    out = []
    for e in smarthome_store.list_entities(domain=domain):
        controllable = e["controllable"] and (
            is_admin or smarthome_store.can_control(
                e["entity_id"], device["role"], device["username"]))
        out.append({**e, "can_control": bool(controllable)})
    return out


@router.get("/rooms")
def list_rooms(device=Depends(auth.require_authenticated)):
    return smarthome_store.list_rooms()


# ---------------------------------------------------------------------------
# Control (write) — admin always; others need a per-entity grant
# ---------------------------------------------------------------------------
@router.post("/entities/{entity_id}/action")
async def entity_action(entity_id: str, payload: dict = Body(default={}),
                        device=Depends(auth.require_authenticated)):
    entity = smarthome_store.get_entity(entity_id)
    if entity is None:
        raise HubError(404, "unknown entity", "not_found")
    if not entity["controllable"]:
        raise HubError(400, "entity is read-only", "bad_request")

    is_admin = device["role"] == "admin"
    if not is_admin and not smarthome_store.can_control(
            entity_id, device["role"], device["username"]):
        raise HubError(403, "you are not allowed to control this device", "forbidden")

    prov = smarthome.get_provider()
    if prov is None:
        raise HubError(409, "no smart-home provider is connected", "not_connected")

    action = payload.get("action")
    params = payload.get("params") or {}
    try:
        result = await prov.call_action(entity_id, action, params)
    except ValueError as e:            # unsupported action for this domain
        raise _bad_request(e)
    except smarthome.SmartHomeError as e:
        raise HubError(502, str(e), "provider_error")
    return result


# ---------------------------------------------------------------------------
# Per-entity permissions (admin) — seed of per-user device permissions
# ---------------------------------------------------------------------------
@router.get("/entities/{entity_id}/permissions")
def get_permissions(entity_id: str, device=Depends(_admin)):
    return smarthome_store.list_permissions(entity_id)


@router.put("/entities/{entity_id}/permissions")
def set_permission(entity_id: str, payload: dict = Body(default={}),
                   device=Depends(_admin)):
    try:
        smarthome_store.set_permission(
            entity_id, payload.get("scope"), bool(payload.get("can_control")))
    except ValueError as e:
        raise _bad_request(e)
    return smarthome_store.list_permissions(entity_id)


# ---------------------------------------------------------------------------
# Favourites (per user, pinned on the Home tab)
# ---------------------------------------------------------------------------
@router.get("/favorites")
def list_favorites(device=Depends(auth.require_authenticated)):
    return smarthome_store.list_favorites(device["username"])


@router.post("/favorites/{entity_id}")
def add_favorite(entity_id: str, device=Depends(auth.require_authenticated)):
    smarthome_store.add_favorite(device["username"], entity_id)
    return {"ok": True}


@router.delete("/favorites/{entity_id}")
def remove_favorite(entity_id: str, device=Depends(auth.require_authenticated)):
    smarthome_store.remove_favorite(device["username"], entity_id)
    return {"ok": True}
