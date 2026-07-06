"""Files + photos routes: upload, list, content, delete, search.

Authz model:
- kind 'file'  -> requires files_read / files_write
- kind 'photo' -> requires photos_read / photos_write
- A file has an owner + 'shared' flag. Readers need the matching *_read priv.
- Only owner or admin can delete.
- Storage path is server-controlled: data/uploads/<owner>/<uuid>_<safe>.
  NEVER serve by raw client path; basename only; path-safety enforced.
"""
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse

from . import auth, config, db, indexer
from .errors import HubError

router = APIRouter(prefix="/api", tags=["files"])

# Dependencies for each capability.
_files_read = auth.require_privilege("files_read")
_files_write = auth.require_privilege("files_write")
_photos_read = auth.require_privilege("photos_read")
_photos_write = auth.require_privilege("photos_write")


def _now() -> int:
    return int(time.time())


_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def safe_filename(name: str) -> str:
    """Basename only; strip dangerous chars. Never trust client paths."""
    base = os.path.basename(name or "").strip()
    base = base.replace("\x00", "")
    base = _SAFE_RE.sub("_", base)
    base = base.lstrip(".") or "file"
    return base[:200]


def _read_priv_for(kind: str) -> str:
    return "photos_read" if kind == "photo" else "files_read"


def _write_priv_for(kind: str) -> str:
    return "photos_write" if kind == "photo" else "files_write"


def _has_priv(device, priv: str) -> bool:
    return priv in auth.privileges_of(device) or device["role"] == "admin"


def _can_read_file(device, frow) -> bool:
    """Owner OR (shared AND has *_read for that kind). Admin always."""
    if device["role"] == "admin":
        return True
    if frow["owner_username"] == device["username"]:
        return _has_priv(device, _read_priv_for(frow["kind"]))
    if frow["shared"]:
        return _has_priv(device, _read_priv_for(frow["kind"]))
    return False


def _resolve_in_uploads(stored_path: str) -> Path:
    """Ensure stored_path is inside UPLOAD_DIR; reject traversal."""
    base = config.UPLOAD_DIR.resolve()
    p = Path(stored_path).resolve()
    if base not in p.parents and p != base:
        raise HubError(403, "Invalid stored path", "forbidden")
    return p


# ----------------------------------------------------------------------------
# Upload
# ----------------------------------------------------------------------------
@router.post("/files")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    shared: str = Form(default="0"),
    device=Depends(auth.get_current_device),
):
    # CSRF + approval are enforced here manually because we need to know the kind
    # (file vs photo) before choosing the privilege.
    auth.enforce_csrf(request)
    if device["status"] != "approved":
        raise HubError(403, "Device pending admin approval", "not_approved")

    raw_name = file.filename or "upload"
    mime = file.content_type or "application/octet-stream"
    kind = "photo" if indexer.is_photo(raw_name, mime) else "file"

    write_priv = _write_priv_for(kind)
    if not _has_priv(device, write_priv):
        raise HubError(403, f"Privilege '{write_priv}' required", "forbidden")

    is_shared = 1 if str(shared).strip().lower() in ("1", "true", "yes", "on") else 0

    # Read the upload with a size cap.
    data = await file.read()
    if len(data) == 0:
        raise HubError(400, "Empty file", "bad_request")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HubError(400, "File too large", "too_large")

    owner = device["username"]
    owner_dir = config.UPLOAD_DIR / safe_filename(owner)
    owner_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_filename(raw_name)
    stored_name = f"{uuid.uuid4().hex}_{safe}"
    stored_path = owner_dir / stored_name
    with open(stored_path, "wb") as f:
        f.write(data)

    conn = db.connect()
    try:
        cur = db.execute(
            conn,
            "INSERT INTO files (owner_username, kind, filename, stored_path, mime, size, shared, "
            "caption, indexed, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (owner, kind, safe, str(stored_path), mime, len(data), is_shared, "", 0, _now()),
        )
        conn.commit()
        file_id = cur.lastrowid
        row = db.query_one(conn, "SELECT * FROM files WHERE id=?", (file_id,))
        result = dict(row)
    finally:
        conn.close()

    # Index synchronously (best-effort; never fails the upload).
    try:
        await indexer.index_file(file_id)
    except Exception:
        pass

    # Refresh caption/indexed after indexing.
    conn = db.connect()
    try:
        row = db.query_one(conn, "SELECT * FROM files WHERE id=?", (file_id,))
        result = dict(row)
    finally:
        conn.close()
    result.pop("stored_path", None)  # never leak server paths
    return result


# ----------------------------------------------------------------------------
# List
# ----------------------------------------------------------------------------
@router.get("/files")
def list_files(request: Request, kind: str | None = None,
               device=Depends(auth.get_current_device)):
    if device["status"] != "approved":
        raise HubError(403, "Device pending admin approval", "not_approved")
    if kind == "all":
        kind = None
    if kind is not None and kind not in ("file", "photo"):
        raise HubError(400, "kind must be file|photo|all", "bad_request")

    # Fail fast: require the relevant read privilege upfront, before any DB query.
    privs = auth.privileges_of(device)
    is_admin = device["role"] == "admin"
    if kind == "file" and not (is_admin or "files_read" in privs):
        raise HubError(403, "files_read required", "forbidden")
    if kind == "photo" and not (is_admin or "photos_read" in privs):
        raise HubError(403, "photos_read required", "forbidden")
    if kind is None and not (is_admin or "files_read" in privs or "photos_read" in privs):
        raise HubError(403, "files_read or photos_read required", "forbidden")

    conn = db.connect()
    try:
        username = device["username"]
        params = [username]
        sql = "SELECT * FROM files WHERE (owner_username=? OR shared=1)"
        if device["role"] == "admin":
            sql = "SELECT * FROM files WHERE 1=1"
            params = []
        if kind in ("file", "photo"):
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY created_at DESC"
        rows = db.query_all(conn, sql, params)
        out = []
        for r in rows:
            if not _can_read_file(device, r):
                continue
            d = dict(r)
            d.pop("stored_path", None)
            out.append(d)
        return out
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Content (download)
# ----------------------------------------------------------------------------
@router.get("/files/{file_id}/content")
def file_content(file_id: int, device=Depends(auth.get_current_device)):
    if device["status"] != "approved":
        raise HubError(403, "Device pending admin approval", "not_approved")
    conn = db.connect()
    try:
        row = db.query_one(conn, "SELECT * FROM files WHERE id=?", (file_id,))
        if row is None:
            raise HubError(404, "File not found", "not_found")
        # Fail fast on missing read privilege for the file kind, before any file I/O.
        if not _has_priv(device, _read_priv_for(row["kind"])):
            raise HubError(403, f"Privilege '{_read_priv_for(row['kind'])}' required", "forbidden")
        if not _can_read_file(device, row):
            raise HubError(403, "Not allowed to read this file", "forbidden")
        path = _resolve_in_uploads(row["stored_path"])
        if not path.exists():
            raise HubError(404, "File missing on disk", "not_found")
        return FileResponse(
            str(path),
            media_type=row["mime"] or "application/octet-stream",
            filename=row["filename"],
        )
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Delete (owner or admin only)
# ----------------------------------------------------------------------------
@router.delete("/files/{file_id}")
def delete_file(request: Request, file_id: int, device=Depends(auth.get_current_device)):
    auth.enforce_csrf(request)
    if device["status"] != "approved":
        raise HubError(403, "Device pending admin approval", "not_approved")
    conn = db.connect()
    try:
        row = db.query_one(conn, "SELECT * FROM files WHERE id=?", (file_id,))
        if row is None:
            raise HubError(404, "File not found", "not_found")
        if device["role"] != "admin" and row["owner_username"] != device["username"]:
            raise HubError(403, "Only owner or admin can delete", "forbidden")
        # Remove disk file (chunks cascade via FK).
        try:
            path = _resolve_in_uploads(row["stored_path"])
            if path.exists():
                path.unlink()
        except Exception:
            pass
        db.execute(conn, "DELETE FROM files WHERE id=?", (file_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Search (semantic + keyword, authz-scoped)
# ----------------------------------------------------------------------------
@router.post("/search")
async def search(request: Request, payload: dict = Body(default={}),
                 device=Depends(auth.get_current_device)):
    auth.enforce_csrf(request)
    if device["status"] != "approved":
        raise HubError(403, "Device pending admin approval", "not_approved")
    q = (payload.get("q") or "").strip()
    kind = payload.get("kind")
    if kind == "all":
        kind = None  # "all" == search files and photos
    if kind is not None and kind not in ("file", "photo"):
        raise HubError(400, "kind must be file|photo|all", "bad_request")
    if not q:
        raise HubError(400, "q is required", "bad_request")

    # Require the relevant read privilege(s). If kind given, require that one;
    # otherwise require at least one of files_read/photos_read.
    privs = auth.privileges_of(device)
    is_admin = device["role"] == "admin"
    if kind == "file" and not (is_admin or "files_read" in privs):
        raise HubError(403, "files_read required", "forbidden")
    if kind == "photo" and not (is_admin or "photos_read" in privs):
        raise HubError(403, "photos_read required", "forbidden")
    if kind is None and not (is_admin or "files_read" in privs or "photos_read" in privs):
        raise HubError(403, "files_read or photos_read required", "forbidden")

    results = await indexer.search(device["username"], device["role"], q, kind)

    # Final per-result authz double-check (defense in depth).
    if not is_admin:
        conn = db.connect()
        try:
            filtered = []
            for r in results:
                frow = db.query_one(conn, "SELECT * FROM files WHERE id=?", (r["file_id"],))
                if frow and _can_read_file(device, frow):
                    filtered.append(r)
            results = filtered
        finally:
            conn.close()
    return results
