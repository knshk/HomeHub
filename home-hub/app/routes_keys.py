"""Per-user API key routes. Minted via the gateway admin endpoint.

We store only the gateway key id + prefix locally, linked to the user.
The plaintext key is returned exactly ONCE on creation.
"""
import time

from fastapi import APIRouter, Body, Depends

from . import auth, config, db, integration
from .errors import HubError

router = APIRouter(prefix="/api", tags=["keys"])

_keys = auth.require_privilege("api_keys")


def _now() -> int:
    return int(time.time())


@router.get("/keys")
def list_keys(device=Depends(_keys)):
    conn = db.connect()
    try:
        rows = db.query_all(
            conn,
            "SELECT id, gateway_key_id, key_prefix, name, created_at, revoked "
            "FROM user_keys WHERE owner_username=? ORDER BY created_at DESC",
            (device["username"],),
        )
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/keys")
async def create_key(payload: dict = Body(default={}), device=Depends(_keys)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HubError(400, "name is required", "bad_request")

    # Mint via gateway admin endpoint.
    minted = await integration.mint_key(f"{device['username']}:{name}"[:100])

    conn = db.connect()
    try:
        db.execute(
            conn,
            "INSERT INTO user_keys (owner_username, gateway_key_id, key_prefix, name, created_at, revoked) "
            "VALUES (?,?,?,?,?,?)",
            (device["username"], minted["id"], minted["prefix"], name[:100], _now(), 0),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "plaintext_key": minted["key"],
        "base_url": config.GATEWAY_URL,
        "model": config.CHAT_MODEL,
    }


@router.post("/keys/{key_id}/revoke")
async def revoke_key(key_id: int, device=Depends(_keys)):
    conn = db.connect()
    try:
        row = db.query_one(conn, "SELECT * FROM user_keys WHERE id=?", (key_id,))
        if row is None:
            raise HubError(404, "Key not found", "not_found")
        if device["role"] != "admin" and row["owner_username"] != device["username"]:
            raise HubError(404, "Key not found", "not_found")
        gateway_key_id = row["gateway_key_id"]
    finally:
        conn.close()

    await integration.revoke_key(gateway_key_id)

    conn = db.connect()
    try:
        db.execute(conn, "UPDATE user_keys SET revoked=1 WHERE id=?", (key_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}
