"""Admin routes: encrypted secret store (Family & Access -> Secrets).

Values are WRITE-ONLY over HTTP: PUT stores them, but no route ever returns
plaintext — only masked hints. Retrieval is server-side via secrets_store.

Only admins (role='admin', status='approved') may use these.
"""
from fastapi import APIRouter, Body, Depends

from . import auth, secrets_store
from .errors import HubError

router = APIRouter(prefix="/api/admin/secrets", tags=["admin-secrets"])

_admin = auth.require_admin()


def _clean(part: str, label: str) -> str:
    part = (part or "").strip()
    if not part or len(part) > 128:
        raise HubError(400, f"{label} must be 1-128 chars", "bad_request")
    return part


@router.get("")
def list_namespaces(device=Depends(_admin)):
    return {"namespaces": secrets_store.list_namespaces()}


@router.get("/{namespace}")
def list_secrets(namespace: str, device=Depends(_admin)):
    namespace = _clean(namespace, "namespace")
    return {"namespace": namespace, "secrets": secrets_store.list_secrets(namespace)}


@router.put("/{namespace}/{name}")
def put_secret(namespace: str, name: str, payload: dict = Body(default={}),
               device=Depends(_admin)):
    namespace = _clean(namespace, "namespace")
    name = _clean(name, "name")
    value = (payload or {}).get("value")
    if not isinstance(value, str) or not value:
        raise HubError(400, "value (non-empty string) required", "bad_request")
    secrets_store.set_secret(namespace, name, value)
    # Write-only: echo identity only, never the value.
    return {"ok": True, "namespace": namespace, "name": name}


@router.delete("/{namespace}/{name}")
def delete_secret(namespace: str, name: str, device=Depends(_admin)):
    namespace = _clean(namespace, "namespace")
    name = _clean(name, "name")
    if not secrets_store.delete_secret(namespace, name):
        raise HubError(404, "Secret not found", "not_found")
    return {"ok": True, "namespace": namespace, "name": name}
