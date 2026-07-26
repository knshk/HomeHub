"""Auth: passwordless, device-bound identity.

- Device token = opaque 40-hex, set as httponly cookie "hub_device".
- We store ONLY the sha256 hash of the token in the DB.
- Trust-on-first-use: a new device self-registers status="pending", role="guest".
- Admin claim verifies admin_token against HUB_ADMIN_TOKEN or HUB_BOOTSTRAP_TOKEN.
- CSRF: state-changing methods require header X-Hub-CSRF: 1.
- Fail closed: missing/invalid device -> 401; insufficient privilege -> 403.
"""
import hashlib
import hmac
import json
import secrets

from fastapi import Depends, Request, Response

from . import config, db, secrets_store
from .errors import HubError


# ----------------------------------------------------------------------------
# Token utilities
# ----------------------------------------------------------------------------
def new_device_token() -> str:
    """40 hex chars (20 bytes)."""
    return secrets.token_hex(20)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_eq(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a, b)


# ----------------------------------------------------------------------------
# Cookie issue / read
# ----------------------------------------------------------------------------
def set_device_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=config.DEVICE_COOKIE,
        value=token,
        max_age=config.COOKIE_MAX_AGE,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        path="/",
    )


def clear_device_cookie(response: Response) -> None:
    response.delete_cookie(key=config.DEVICE_COOKIE, path="/")


def read_device_token(request: Request) -> str | None:
    return request.cookies.get(config.DEVICE_COOKIE)


# ----------------------------------------------------------------------------
# Device record helpers
# ----------------------------------------------------------------------------
def device_to_me(device) -> dict:
    """Build the /api/me payload from a device row."""
    try:
        privs = json.loads(device["privileges_json"])
        if not isinstance(privs, list):
            privs = []
    except Exception:
        privs = []
    return {
        "username": device["username"],
        "role": device["role"],
        "status": device["status"],
        "privileges": privs,
    }


def privileges_of(device) -> list[str]:
    try:
        privs = json.loads(device["privileges_json"])
        return privs if isinstance(privs, list) else []
    except Exception:
        return []


def register_new_device(conn, token: str, username: str) -> int:
    """TOFU: create a pending guest device for this token."""
    role = "guest"
    privs = list(config.ROLE_DEFAULT_PRIVILEGES["guest"])
    return db.create_device(
        conn, sha256_hex(token), username.strip(), role, "pending", privs
    )


def make_admin(conn, device_id: int, username: str) -> None:
    db.update_device(
        conn, device_id,
        username=username.strip(),
        role="admin",
        status="approved",
        privileges=list(config.ROLE_DEFAULT_PRIVILEGES["admin"]),
    )


def verify_admin_token(token: str) -> bool:
    if not token:
        return False
    if config.HUB_ADMIN_TOKEN and constant_eq(token, config.HUB_ADMIN_TOKEN):
        return True
    if config.HUB_BOOTSTRAP_TOKEN and constant_eq(token, config.HUB_BOOTSTRAP_TOKEN):
        return True
    return False


# ----------------------------------------------------------------------------
# Admin PIN — any user can elevate to admin from any device by entering it.
# The PIN is stored encrypted (secret store, namespace 'admin'); an env value
# (HUB_ADMIN_PIN) is the bootstrap fallback when none is stored. By design there
# is NO attempt limit / lockout on PIN entry (family usability over hardening,
# LAN-only). The PIN is never returned by any endpoint.
# ----------------------------------------------------------------------------
_ADMIN_PIN_NS = "admin"
_ADMIN_PIN_NAME = "pin"


def get_admin_pin() -> str | None:
    """Stored PIN (secret store) if set, else the env bootstrap PIN, else None."""
    try:
        stored = secrets_store.get_secret(_ADMIN_PIN_NS, _ADMIN_PIN_NAME)
    except Exception:
        stored = None
    return (stored or config.HUB_ADMIN_PIN) or None


def admin_pin_is_set() -> bool:
    return bool(get_admin_pin())


def set_admin_pin(pin: str) -> None:
    secrets_store.set_secret(_ADMIN_PIN_NS, _ADMIN_PIN_NAME, pin.strip())


def clear_admin_pin() -> None:
    secrets_store.delete_secret(_ADMIN_PIN_NS, _ADMIN_PIN_NAME)


def verify_admin_pin(pin: str) -> bool:
    stored = get_admin_pin()
    if not stored or not pin:
        return False
    return constant_eq(pin.strip(), stored.strip())


# ----------------------------------------------------------------------------
# CSRF enforcement
# ----------------------------------------------------------------------------
_STATE_CHANGING = {"POST", "PUT", "DELETE", "PATCH"}


def enforce_csrf(request: Request) -> None:
    if request.method.upper() in _STATE_CHANGING:
        val = request.headers.get(config.CSRF_HEADER)
        if val is None or val.strip() == "":
            raise HubError(403, "Missing CSRF header X-Hub-CSRF", "csrf_missing")


# ----------------------------------------------------------------------------
# FastAPI dependencies
# ----------------------------------------------------------------------------
def get_current_device(request: Request):
    """Resolve the current device row from the cookie. Fail closed (401)."""
    token = read_device_token(request)
    if not token:
        raise HubError(401, "No device cookie; register first", "no_device")
    conn = db.connect()
    try:
        device = db.get_device_by_hash(conn, sha256_hex(token))
        if device is None:
            raise HubError(401, "Unknown device", "unknown_device")
        db.touch_device(conn, device["id"])
        # Return a plain dict snapshot so the connection can close.
        return dict(device)
    finally:
        conn.close()


def require_csrf(request: Request):
    """Dependency form of CSRF enforcement for state-changing routes."""
    enforce_csrf(request)
    return True


def require_authenticated(device=Depends(get_current_device)):
    """Authenticated AND approved device."""
    if device["status"] != "approved":
        raise HubError(403, "Device pending admin approval", "not_approved")
    return device


def require_privilege(privilege: str):
    """Build a dependency that requires an approved device with `privilege`."""
    def _dep(request: Request, device=Depends(get_current_device)):
        enforce_csrf(request)  # applies to state-changing methods only
        if device["status"] != "approved":
            raise HubError(403, "Device pending admin approval", "not_approved")
        privs = privileges_of(device)
        if privilege not in privs:
            raise HubError(403, f"Privilege '{privilege}' required", "forbidden")
        return device
    return _dep


def require_admin():
    def _dep(request: Request, device=Depends(get_current_device)):
        enforce_csrf(request)
        if device["status"] != "approved" or device["role"] != "admin":
            raise HubError(403, "Admin role required", "forbidden")
        return device
    return _dep
