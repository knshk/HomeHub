"""Session / identity routes."""
from fastapi import APIRouter, Body, Request, Response

from . import auth, db
from .errors import HubError

router = APIRouter(prefix="/api", tags=["session"])


@router.get("/me")
def me(request: Request):
    """Return identity payload. If no/unknown device, fail closed with 401."""
    token = auth.read_device_token(request)
    if not token:
        raise HubError(401, "No device cookie; register first", "no_device")
    conn = db.connect()
    try:
        device = db.get_device_by_hash(conn, auth.sha256_hex(token))
        if device is None:
            raise HubError(401, "Unknown device", "unknown_device")
        db.touch_device(conn, device["id"])
        return auth.device_to_me(device)
    finally:
        conn.close()


@router.post("/session/register")
def register(response: Response, request: Request, payload: dict = Body(default={})):
    """Register or re-attach a device. TOFU: new token -> pending guest.

    Idempotent: if the cookie already maps to a known device, we just update the
    username (if provided) and return the current payload.
    """
    auth.enforce_csrf(request)
    username = (payload.get("username") or "").strip()
    if not username:
        raise HubError(400, "username is required", "bad_request")
    if len(username) > 64:
        raise HubError(400, "username too long", "bad_request")

    token = auth.read_device_token(request)
    conn = db.connect()
    try:
        device = None
        if token:
            device = db.get_device_by_hash(conn, auth.sha256_hex(token))
        if device is not None:
            # Existing device: allow updating the chosen display username.
            db.update_device(conn, device["id"], username=username)
            db.touch_device(conn, device["id"])
            device = db.get_device(conn, device["id"])
            auth.set_device_cookie(response, token)  # refresh max-age
            return auth.device_to_me(device)

        # New device.
        new_token = auth.new_device_token()
        device_id = auth.register_new_device(conn, new_token, username)
        auth.set_device_cookie(response, new_token)
        device = db.get_device(conn, device_id)
        return auth.device_to_me(device)
    finally:
        conn.close()


@router.post("/session/claim")
def claim(response: Response, request: Request, payload: dict = Body(default={})):
    """Promote the current device to admin by presenting a valid admin token."""
    auth.enforce_csrf(request)
    username = (payload.get("username") or "").strip()
    admin_token = (payload.get("admin_token") or "").strip()
    if not username:
        raise HubError(400, "username is required", "bad_request")
    if not auth.verify_admin_token(admin_token):
        raise HubError(403, "Invalid admin token", "bad_admin_token")

    token = auth.read_device_token(request)
    conn = db.connect()
    try:
        device = None
        if token:
            device = db.get_device_by_hash(conn, auth.sha256_hex(token))
        if device is None:
            # Bootstrap a brand-new admin device.
            token = auth.new_device_token()
            device_id = auth.register_new_device(conn, token, username)
            auth.set_device_cookie(response, token)
        else:
            device_id = device["id"]
            auth.set_device_cookie(response, token)  # refresh
        auth.make_admin(conn, device_id, username)
        device = db.get_device(conn, device_id)
        return auth.device_to_me(device)
    finally:
        conn.close()


@router.post("/session/setup")
def setup(response: Response, request: Request, payload: dict = Body(default={})):
    """First-run: create the FIRST admin. Works ONLY while the hub has no admin
    yet (naturally single-use — once an admin exists it 409s). Uses the setup
    code the installer printed, so a fresh install needs no token / .env edit.
    """
    auth.enforce_csrf(request)
    username = (payload.get("username") or "").strip()
    code = (payload.get("code") or "").strip()
    if not username:
        raise HubError(400, "username is required", "bad_request")

    conn = db.connect()
    try:
        if db.count_admins(conn) > 0:
            raise HubError(409, "This hub is already set up. Ask an admin for "
                           "the PIN, or use the admin token.", "setup_complete")
        if not auth.verify_setup_code(code):
            raise HubError(403, "Incorrect setup code", "bad_setup_code")

        token = auth.read_device_token(request)
        device = db.get_device_by_hash(conn, auth.sha256_hex(token)) if token else None
        if device is None:
            token = auth.new_device_token()
            device_id = auth.register_new_device(conn, token, username)
            auth.set_device_cookie(response, token)
        else:
            device_id = device["id"]
            auth.set_device_cookie(response, token)  # refresh
        auth.make_admin(conn, device_id, username)
        device = db.get_device(conn, device_id)
        return auth.device_to_me(device)
    finally:
        conn.close()


@router.post("/session/elevate")
def elevate(response: Response, request: Request, payload: dict = Body(default={})):
    """Promote the CURRENT device to admin by entering the admin PIN.

    Works from any device / PWA. If this browser has no device yet, a `username`
    bootstraps one first. By design there is NO attempt limit — infinite tries,
    nothing gets locked out (family usability; the hub is LAN-only).
    """
    auth.enforce_csrf(request)
    pin = (payload.get("pin") or "").strip()
    username = (payload.get("username") or "").strip()

    if not auth.admin_pin_is_set():
        raise HubError(409, "No admin PIN is set yet. An admin can set one in "
                       "Settings, or use the admin token.", "no_admin_pin")
    if not auth.verify_admin_pin(pin):
        raise HubError(403, "Incorrect PIN", "bad_pin")  # no lockout, try again

    token = auth.read_device_token(request)
    conn = db.connect()
    try:
        device = db.get_device_by_hash(conn, auth.sha256_hex(token)) if token else None
        if device is None:
            if not username:
                raise HubError(400, "username is required on a new device",
                               "bad_request")
            token = auth.new_device_token()
            device_id = auth.register_new_device(conn, token, username)
            auth.set_device_cookie(response, token)
        else:
            device_id = device["id"]
            username = username or device["username"]
            auth.set_device_cookie(response, token)  # refresh
        auth.make_admin(conn, device_id, username)
        device = db.get_device(conn, device_id)
        return auth.device_to_me(device)
    finally:
        conn.close()


@router.post("/session/logout")
def logout(response: Response, request: Request):
    """Clear the device cookie on this browser. Device record is retained."""
    auth.enforce_csrf(request)
    auth.clear_device_cookie(response)
    return {"ok": True}
