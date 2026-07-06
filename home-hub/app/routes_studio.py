"""Studio routes — art/animation asset pipeline.

Viewing needs an approved device; mutations need the ``files_write`` privilege
(enforces admin/member role + CSRF). Files are served read-only under
/studio-files (mounted in main.py).
"""
from fastapi import APIRouter, Body, Depends, File, UploadFile

from . import auth, config, studio
from .errors import HubError

router = APIRouter(prefix="/api/studio", tags=["studio"])

_view = Depends(auth.require_authenticated)
_edit = Depends(auth.require_privilege("files_write"))

_MAX_RIV = 8 * 1024 * 1024  # 8 MB is plenty for a .riv


@router.get("/assets")
def list_assets(device=_view):
    return {"assets": studio.list_assets()}


@router.post("/import-generated")
def import_generated(device=_edit):
    added = studio.import_generated()
    return {"imported": added, "count": len(added), "dir": config.FASTSD_RESULTS_DIR}


@router.post("/upload")
async def upload_image(file: UploadFile = File(...), device=_edit):
    data = await file.read()
    if not data:
        raise HubError(400, "Empty file", "bad_request")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HubError(413, "Image too large", "too_large")
    return studio.add_upload(file.filename or "image.png", data)


@router.post("/{aid}/rive")
async def upload_rive(aid: str, file: UploadFile = File(...), device=_edit):
    data = await file.read()
    if not data:
        raise HubError(400, "Empty file", "bad_request")
    if len(data) > _MAX_RIV:
        raise HubError(413, ".riv too large", "too_large")
    if not (file.filename or "").lower().endswith(".riv"):
        raise HubError(400, "Expected a .riv file", "bad_request")
    try:
        return studio.set_rive(aid, data)
    except KeyError:
        raise HubError(404, "Asset not found", "not_found")


@router.post("/{aid}/animate")
def animate(aid: str, device=_edit):
    try:
        return studio.animate_cpu(aid)
    except KeyError:
        raise HubError(404, "Asset not found", "not_found")
    except Exception as e:  # PIL / IO issues -> surface cleanly
        raise HubError(500, f"Animation failed: {str(e)[:160]}", "animate_failed")


@router.post("/{aid}/meta")
def set_meta(aid: str, payload: dict = Body(default={}), device=_edit):
    try:
        return studio.update_meta(
            aid,
            catalogId=payload.get("catalogId"),
            name=payload.get("name"),
            games=payload.get("games"),
            notes=payload.get("notes"),
            status=payload.get("status"),
        )
    except KeyError:
        raise HubError(404, "Asset not found", "not_found")
    except ValueError:
        raise HubError(400, "Invalid status (draft|rigging|ready)", "bad_request")


@router.post("/{aid}/remove-animation")
def remove_animation(aid: str, device=_edit):
    try:
        return studio.remove_animation(aid)
    except KeyError:
        raise HubError(404, "Asset not found", "not_found")


@router.post("/{aid}/delete")
def delete(aid: str, device=_edit):
    if not studio.delete(aid):
        raise HubError(404, "Asset not found", "not_found")
    return {"ok": True, "id": aid}
