"""Authentication, key management, and rate limiting.

Responsibilities:
  * Generate new API keys in the format ``qwsk-<40 hex>``.
  * Hash keys with sha256 (only the hash + a short display prefix are stored).
  * Verify a presented key against ``api_keys`` using constant-time comparison.
  * Enforce a per-key, in-memory, sliding-window requests-per-minute limit.
  * Provide a FastAPI dependency that returns the authenticated key row or
    raises an OpenAI-style 401/429 error.

Auth is fail-closed: any missing/invalid/revoked credential results in 401.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Optional

from fastapi import Header, HTTPException, Request, status

from . import db
from .config import settings

KEY_PREFIX_LITERAL = "qwsk-"
# Display prefix length: "qwsk-" (5) + 8 hex chars = 13 chars total.
DISPLAY_PREFIX_LEN = 13


# --------------------------------------------------------------------------- #
# Key generation / hashing
# --------------------------------------------------------------------------- #
def generate_api_key() -> str:
    """Generate a new plaintext API key: ``qwsk-`` + 40 lowercase hex chars."""
    return KEY_PREFIX_LITERAL + secrets.token_hex(20)


def hash_key(plaintext: str) -> str:
    """Return the sha256 hex digest of the full plaintext key."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def display_prefix(plaintext: str) -> str:
    """Return the first 13 chars of the key for non-secret display."""
    return plaintext[:DISPLAY_PREFIX_LEN]


def create_key_record(name: str, rpm_limit: Optional[int] = None,
                      daily_token_limit: int = 0) -> Dict[str, Any]:
    """Create and persist a new key.

    Returns a dict containing the persisted row PLUS the one-time plaintext
    under the key ``"plaintext"``. The plaintext is never stored.
    """
    plaintext = generate_api_key()
    rpm = settings.default_rpm if rpm_limit is None else int(rpm_limit)
    row = db.create_api_key(
        name=name,
        key_prefix=display_prefix(plaintext),
        key_hash=hash_key(plaintext),
        rpm_limit=rpm,
        daily_token_limit=int(daily_token_limit),
    )
    record = dict(row)
    record["plaintext"] = plaintext
    return record


# --------------------------------------------------------------------------- #
# Sliding-window RPM limiter (in-memory, per key_id)
# --------------------------------------------------------------------------- #
class SlidingWindowRateLimiter:
    """Thread-safe per-key sliding-window limiter over a 60s window."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self._window = window_seconds
        self._hits: Dict[int, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key_id: int, limit: int) -> bool:
        """Record a hit and return True if within ``limit`` for the window."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._hits[key_id]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= max(0, int(limit)):
                return False
            bucket.append(now)
            return True


rate_limiter = SlidingWindowRateLimiter()


# --------------------------------------------------------------------------- #
# OpenAI-style error helper
# --------------------------------------------------------------------------- #
def openai_error(status_code: int, message: str, err_type: str,
                 code: Optional[str] = None) -> HTTPException:
    """Build an HTTPException whose detail is an OpenAI-style error body."""
    return HTTPException(
        status_code=status_code,
        detail={"error": {"message": message, "type": err_type, "code": code}},
    )


# --------------------------------------------------------------------------- #
# Credential extraction
# --------------------------------------------------------------------------- #
def _extract_token(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    """Pull the bearer/x-api-key token from request headers, if any."""
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            candidate = parts[1].strip()
            if candidate:
                return candidate
    if x_api_key:
        candidate = x_api_key.strip()
        if candidate:
            return candidate
    return None


# A fixed-length sha256-shaped placeholder used so that the constant-time
# comparison always runs against a same-shaped digest, even when no row is
# found. This keeps the verify path's timing independent of key existence.
_DUMMY_HASH = "0" * 64


def _verify_key(plaintext: str) -> Dict[str, Any]:
    """Resolve and validate a plaintext key -> row, else raise 401.

    Uses constant-time comparison on the stored hash and checks revocation.
    The hash is always computed and a constant-time compare is always run
    (against a same-shaped placeholder when no row exists) so that callers
    cannot distinguish "unknown key" from "known/revoked key" via timing.
    """
    # Always compute the hash, even on malformed input.
    presented_hash = hash_key(plaintext or "")
    row = db.get_key_by_hash(presented_hash)

    # Always run a constant-time compare against a same-shaped digest. When the
    # row is missing we compare against a fixed placeholder so the work done is
    # independent of whether the key exists.
    stored_hash = str(row["key_hash"]) if row is not None else _DUMMY_HASH
    hash_matches = hmac.compare_digest(stored_hash, presented_hash)

    # Evaluate revocation without an existence-dependent shortcut.
    revoked = bool(row is not None and int(row["revoked"]) != 0)

    if row is None or not hash_matches:
        raise openai_error(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid API key.",
            "invalid_request_error",
            "invalid_api_key",
        )
    if revoked:
        raise openai_error(
            status.HTTP_401_UNAUTHORIZED,
            "This API key has been revoked.",
            "invalid_request_error",
            "revoked_api_key",
        )
    return dict(row)


# --------------------------------------------------------------------------- #
# FastAPI dependency
# --------------------------------------------------------------------------- #
async def require_api_key(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
) -> Dict[str, Any]:
    """FastAPI dependency: authenticate the caller and apply rate limiting.

    Returns the authenticated ``api_keys`` row as a dict. Raises 401 for
    missing/invalid/revoked keys and 429 when the per-key RPM limit is hit.
    """
    token = _extract_token(authorization, x_api_key)
    if not token:
        raise openai_error(
            status.HTTP_401_UNAUTHORIZED,
            "Missing API key. Provide 'Authorization: Bearer <key>' or 'x-api-key'.",
            "invalid_request_error",
            "missing_api_key",
        )

    key_row = _verify_key(token)

    limit = int(key_row.get("rpm_limit") or settings.default_rpm)
    if not rate_limiter.allow(int(key_row["id"]), limit):
        raise openai_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit exceeded ({limit} requests per minute).",
            "rate_limit_error",
            "rate_limit_exceeded",
        )

    # Make the key row available to handlers without re-querying.
    request.state.api_key = key_row
    return key_row
