"""Checklists + items routes. Users access only their OWN checklists."""
import time

from fastapi import APIRouter, Body, Depends

from . import auth, db
from .errors import HubError

router = APIRouter(prefix="/api", tags=["checklists"])

_cl = auth.require_privilege("checklists")


def _now() -> int:
    return int(time.time())


def _own_checklist(conn, list_id: int, username: str, role: str):
    row = db.query_one(conn, "SELECT * FROM checklists WHERE id=?", (list_id,))
    if row is None:
        raise HubError(404, "Checklist not found", "not_found")
    if role != "admin" and row["owner_username"] != username:
        raise HubError(404, "Checklist not found", "not_found")
    return row


def _items(conn, list_id: int) -> list[dict]:
    rows = db.query_all(
        conn,
        "SELECT id, text, done, position FROM checklist_items "
        "WHERE checklist_id=? ORDER BY position ASC, id ASC",
        (list_id,),
    )
    return [dict(r) for r in rows]


@router.get("/checklists")
def list_checklists(device=Depends(_cl)):
    conn = db.connect()
    try:
        rows = db.query_all(
            conn,
            "SELECT * FROM checklists WHERE owner_username=? ORDER BY updated_at DESC",
            (device["username"],),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["items"] = _items(conn, r["id"])
            out.append(d)
        return out
    finally:
        conn.close()


@router.post("/checklists")
def create_checklist(payload: dict = Body(default={}), device=Depends(_cl)):
    title = (payload.get("title") or "").strip()
    if not title:
        raise HubError(400, "title is required", "bad_request")
    now = _now()
    conn = db.connect()
    try:
        cur = db.execute(
            conn,
            "INSERT INTO checklists (owner_username, title, created_at, updated_at) "
            "VALUES (?,?,?,?)",
            (device["username"], title[:200], now, now),
        )
        conn.commit()
        row = db.query_one(conn, "SELECT * FROM checklists WHERE id=?", (cur.lastrowid,))
        d = dict(row)
        d["items"] = []
        return d
    finally:
        conn.close()


@router.put("/checklists/{list_id}")
def rename_checklist(list_id: int, payload: dict = Body(default={}), device=Depends(_cl)):
    title = (payload.get("title") or "").strip()
    if not title:
        raise HubError(400, "title is required", "bad_request")
    conn = db.connect()
    try:
        _own_checklist(conn, list_id, device["username"], device["role"])
        db.execute(conn, "UPDATE checklists SET title=?, updated_at=? WHERE id=?",
                   (title[:200], _now(), list_id))
        conn.commit()
        row = db.query_one(conn, "SELECT * FROM checklists WHERE id=?", (list_id,))
        d = dict(row)
        d["items"] = _items(conn, list_id)
        return d
    finally:
        conn.close()


@router.delete("/checklists/{list_id}")
def delete_checklist(list_id: int, device=Depends(_cl)):
    conn = db.connect()
    try:
        _own_checklist(conn, list_id, device["username"], device["role"])
        db.execute(conn, "DELETE FROM checklists WHERE id=?", (list_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/checklists/{list_id}/items")
def add_item(list_id: int, payload: dict = Body(default={}), device=Depends(_cl)):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HubError(400, "text is required", "bad_request")
    conn = db.connect()
    try:
        _own_checklist(conn, list_id, device["username"], device["role"])
        pos_row = db.query_one(
            conn, "SELECT COALESCE(MAX(position), -1) AS p FROM checklist_items WHERE checklist_id=?",
            (list_id,))
        position = (pos_row["p"] if pos_row else -1) + 1
        cur = db.execute(
            conn,
            "INSERT INTO checklist_items (checklist_id, text, done, position) VALUES (?,?,?,?)",
            (list_id, text[:500], 0, position),
        )
        db.execute(conn, "UPDATE checklists SET updated_at=? WHERE id=?", (_now(), list_id))
        conn.commit()
        row = db.query_one(conn, "SELECT id, text, done, position FROM checklist_items WHERE id=?",
                           (cur.lastrowid,))
        return dict(row)
    finally:
        conn.close()


@router.put("/checklists/{list_id}/items/{item_id}")
def update_item(list_id: int, item_id: int, payload: dict = Body(default={}), device=Depends(_cl)):
    conn = db.connect()
    try:
        _own_checklist(conn, list_id, device["username"], device["role"])
        item = db.query_one(conn, "SELECT * FROM checklist_items WHERE id=? AND checklist_id=?",
                            (item_id, list_id))
        if item is None:
            raise HubError(404, "Item not found", "not_found")
        sets, params = [], []
        if "text" in payload:
            t = (payload.get("text") or "").strip()
            if not t:
                raise HubError(400, "text cannot be empty", "bad_request")
            sets.append("text=?"); params.append(t[:500])
        if "done" in payload:
            sets.append("done=?"); params.append(1 if payload.get("done") else 0)
        if "position" in payload:
            try:
                sets.append("position=?"); params.append(int(payload.get("position")))
            except (TypeError, ValueError):
                raise HubError(400, "position must be int", "bad_request")
        if not sets:
            raise HubError(400, "no fields to update", "bad_request")
        params.append(item_id)
        db.execute(conn, f"UPDATE checklist_items SET {', '.join(sets)} WHERE id=?", params)
        db.execute(conn, "UPDATE checklists SET updated_at=? WHERE id=?", (_now(), list_id))
        conn.commit()
        row = db.query_one(conn, "SELECT id, text, done, position FROM checklist_items WHERE id=?",
                           (item_id,))
        return dict(row)
    finally:
        conn.close()


@router.delete("/checklists/{list_id}/items/{item_id}")
def delete_item(list_id: int, item_id: int, device=Depends(_cl)):
    conn = db.connect()
    try:
        _own_checklist(conn, list_id, device["username"], device["role"])
        item = db.query_one(conn, "SELECT id FROM checklist_items WHERE id=? AND checklist_id=?",
                            (item_id, list_id))
        if item is None:
            raise HubError(404, "Item not found", "not_found")
        db.execute(conn, "DELETE FROM checklist_items WHERE id=?", (item_id,))
        db.execute(conn, "UPDATE checklists SET updated_at=? WHERE id=?", (_now(), list_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
