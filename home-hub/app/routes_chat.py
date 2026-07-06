"""Chat / conversations routes. Users access only their OWN conversations."""
import time

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse

from . import auth, db, integration
from .errors import HubError

router = APIRouter(prefix="/api", tags=["chat"])

_chat = auth.require_privilege("chat")


def _now() -> int:
    return int(time.time())


def _own_conversation(conn, conv_id: int, username: str, role: str):
    row = db.query_one(conn, "SELECT * FROM conversations WHERE id=?", (conv_id,))
    if row is None:
        raise HubError(404, "Conversation not found", "not_found")
    if role != "admin" and row["owner_username"] != username:
        raise HubError(404, "Conversation not found", "not_found")
    return row


@router.get("/conversations")
def list_conversations(device=Depends(_chat)):
    conn = db.connect()
    try:
        rows = db.query_all(
            conn,
            "SELECT id, owner_username, title, created_at, updated_at "
            "FROM conversations WHERE owner_username=? ORDER BY updated_at DESC",
            (device["username"],),
        )
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/conversations")
def create_conversation(payload: dict = Body(default={}), device=Depends(_chat)):
    title = (payload.get("title") or "New chat").strip() or "New chat"
    now = _now()
    conn = db.connect()
    try:
        cur = db.execute(
            conn,
            "INSERT INTO conversations (owner_username, title, created_at, updated_at) "
            "VALUES (?,?,?,?)",
            (device["username"], title[:200], now, now),
        )
        conn.commit()
        row = db.query_one(conn, "SELECT * FROM conversations WHERE id=?", (cur.lastrowid,))
        return dict(row)
    finally:
        conn.close()


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: int, device=Depends(_chat)):
    conn = db.connect()
    try:
        conv = _own_conversation(conn, conv_id, device["username"], device["role"])
        msgs = db.query_all(
            conn,
            "SELECT id, role, content, created_at FROM messages "
            "WHERE conversation_id=? ORDER BY id ASC",
            (conv_id,),
        )
        return {**dict(conv), "messages": [dict(m) for m in msgs]}
    finally:
        conn.close()


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: int, device=Depends(_chat)):
    conn = db.connect()
    try:
        _own_conversation(conn, conv_id, device["username"], device["role"])
        db.execute(conn, "DELETE FROM conversations WHERE id=?", (conv_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def _conversation_messages(conn, conv_id: int) -> list[dict]:
    rows = db.query_all(
        conn,
        "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id ASC",
        (conv_id,),
    )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


@router.post("/conversations/{conv_id}/messages")
async def post_message(conv_id: int, request: Request, payload: dict = Body(default={}),
                       device=Depends(_chat)):
    content = (payload.get("content") or "").strip()
    stream = bool(payload.get("stream", False))
    if not content:
        raise HubError(400, "content is required", "bad_request")

    username = device["username"]
    role = device["role"]
    now = _now()

    conn = db.connect()
    try:
        _own_conversation(conn, conv_id, username, role)
        # Persist the user message.
        db.execute(
            conn,
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
            (conv_id, "user", content, now),
        )
        db.execute(conn, "UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id))
        # Auto-title from first user message if still default.
        conv = db.query_one(conn, "SELECT title FROM conversations WHERE id=?", (conv_id,))
        if conv and conv["title"] in ("New chat", ""):
            db.execute(conn, "UPDATE conversations SET title=? WHERE id=?",
                       (content[:60], conv_id))
        conn.commit()
        history = _conversation_messages(conn, conv_id)
    finally:
        conn.close()

    if stream:
        async def gen():
            assembled = []
            try:
                async for sse in integration.chat_completion_stream(history):
                    assembled.append(integration.extract_delta(sse))
                    yield sse
            except HubError as e:
                yield f'data: {{"error":{{"message":{e.message!r},"code":"{e.code}"}}}}\n\n'
            finally:
                full = "".join(assembled).strip()
                if full:
                    c2 = db.connect()
                    try:
                        db.execute(
                            c2,
                            "INSERT INTO messages (conversation_id, role, content, created_at) "
                            "VALUES (?,?,?,?)",
                            (conv_id, "assistant", full, _now()),
                        )
                        db.execute(c2, "UPDATE conversations SET updated_at=? WHERE id=?",
                                   (_now(), conv_id))
                        c2.commit()
                    finally:
                        c2.close()
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming.
    answer = await integration.chat_completion(history)
    conn = db.connect()
    try:
        db.execute(
            conn,
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
            (conv_id, "assistant", answer, _now()),
        )
        db.execute(conn, "UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conv_id))
        conn.commit()
    finally:
        conn.close()
    return {"role": "assistant", "content": answer}
