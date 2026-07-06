"""Notes routes. Users access only their OWN notes."""
import time

from fastapi import APIRouter, Body, Depends

from . import auth, db
from .errors import HubError

router = APIRouter(prefix="/api", tags=["notes"])

_notes = auth.require_privilege("notes")

VALID_COLORS = {"default", "red", "orange", "yellow", "green", "blue", "pink", "purple", "gray"}


def _now() -> int:
    return int(time.time())


def _own_note(conn, note_id: int, username: str, role: str):
    row = db.query_one(conn, "SELECT * FROM notes WHERE id=?", (note_id,))
    if row is None:
        raise HubError(404, "Note not found", "not_found")
    if role != "admin" and row["owner_username"] != username:
        raise HubError(404, "Note not found", "not_found")
    return row


@router.get("/notes")
def list_notes(device=Depends(_notes)):
    conn = db.connect()
    try:
        rows = db.query_all(
            conn,
            "SELECT * FROM notes WHERE owner_username=? "
            "ORDER BY pinned DESC, updated_at DESC",
            (device["username"],),
        )
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/notes")
def create_note(payload: dict = Body(default={}), device=Depends(_notes)):
    title = (payload.get("title") or "").strip()
    body = payload.get("body") or ""
    color = (payload.get("color") or "default").strip()
    pinned = 1 if payload.get("pinned") else 0
    if color not in VALID_COLORS:
        color = "default"
    if not title and not body:
        raise HubError(400, "title or body required", "bad_request")
    now = _now()
    conn = db.connect()
    try:
        cur = db.execute(
            conn,
            "INSERT INTO notes (owner_username, title, body, color, pinned, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (device["username"], title[:300], body, color, pinned, now, now),
        )
        conn.commit()
        row = db.query_one(conn, "SELECT * FROM notes WHERE id=?", (cur.lastrowid,))
        return dict(row)
    finally:
        conn.close()


@router.put("/notes/{note_id}")
def update_note(note_id: int, payload: dict = Body(default={}), device=Depends(_notes)):
    conn = db.connect()
    try:
        _own_note(conn, note_id, device["username"], device["role"])
        sets, params = [], []
        if "title" in payload:
            sets.append("title=?"); params.append((payload.get("title") or "").strip()[:300])
        if "body" in payload:
            sets.append("body=?"); params.append(payload.get("body") or "")
        if "color" in payload:
            color = (payload.get("color") or "default").strip()
            if color not in VALID_COLORS:
                color = "default"
            sets.append("color=?"); params.append(color)
        if "pinned" in payload:
            sets.append("pinned=?"); params.append(1 if payload.get("pinned") else 0)
        if not sets:
            raise HubError(400, "no fields to update", "bad_request")
        sets.append("updated_at=?"); params.append(_now())
        params.append(note_id)
        db.execute(conn, f"UPDATE notes SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
        row = db.query_one(conn, "SELECT * FROM notes WHERE id=?", (note_id,))
        return dict(row)
    finally:
        conn.close()


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, device=Depends(_notes)):
    conn = db.connect()
    try:
        _own_note(conn, note_id, device["username"], device["role"])
        db.execute(conn, "DELETE FROM notes WHERE id=?", (note_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
