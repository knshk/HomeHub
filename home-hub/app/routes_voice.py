"""Voice routes: PROXY to a SEPARATE local voice service (STT + TTS).

The hub is a CLIENT only. It does NOT run faster-whisper or kokoro-onnx itself;
those live in a standalone "voice service" the operator runs at VOICE_URL
(default http://127.0.0.1:8100). Execution there is sequential, not real-time.

Voice service contract (see VOICE_URL):
  GET  /healthz    -> {"status","stt","tts","voice"}
  POST /transcribe -> multipart "audio" (+ optional "language") -> {"text","language"}
  POST /speak      -> JSON {"text","voice"?,"speed"?} -> audio/wav bytes

Authz: all routes require the "chat" privilege + an approved device; CSRF is
enforced on the state-changing POSTs. Fail closed with HubError(502, ...) when
the voice service is unreachable, and never leak its internals/secrets.
"""
import logging

import httpx

from fastapi import APIRouter, Body, Depends, File, Request, UploadFile
from fastapi.responses import StreamingResponse

from . import auth, config
from .errors import HubError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

_chat = auth.require_privilege("chat")

# Match integration.py: generous read timeout for slow CPU transcription/synthesis.
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)


# ----------------------------------------------------------------------------
# Transcribe: multipart audio upload -> {"text","language"}
# ----------------------------------------------------------------------------
@router.post("/transcribe")
async def transcribe(
    request: Request,
    audio: UploadFile = File(...),
    device=Depends(_chat),
):
    # _chat already enforced CSRF + approval + the "chat" privilege.
    data = await audio.read()
    if len(data) == 0:
        raise HubError(400, "Empty audio", "bad_request")
    if len(data) > config.MAX_VOICE_BYTES:
        raise HubError(400, "Audio too large", "too_large")

    filename = audio.filename or "audio.webm"
    mime = audio.content_type or "application/octet-stream"
    files = {"audio": (filename, data, mime)}

    url = f"{config.VOICE_URL}/transcribe"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, files=files)
    except httpx.HTTPError as e:
        raise HubError(502, f"Voice service unreachable: {e}", "voice_error")
    if r.status_code >= 400:
        # Log upstream details server-side only; never leak them to the client.
        log.warning("Voice transcribe upstream error %s: %s", r.status_code, r.text[:300])
        raise HubError(502, "Voice service error (please try again)", "voice_error")
    try:
        body = r.json()
    except Exception as e:
        raise HubError(502, f"Bad voice response: {e}", "voice_error")
    return {
        "text": body.get("text", "") or "",
        "language": body.get("language", "") or "",
    }


# ----------------------------------------------------------------------------
# Speak: JSON {text, voice?, speed?} -> stream audio/wav back
# ----------------------------------------------------------------------------
@router.post("/speak")
async def speak(
    request: Request,
    payload: dict = Body(default={}),
    device=Depends(_chat),
):
    # _chat already enforced CSRF + approval + the "chat" privilege.
    text = (payload.get("text") or "").strip()
    if not text:
        raise HubError(400, "text is required", "bad_request")
    if len(text) > 8000:
        raise HubError(400, "Text too long (max 8000 chars)", "too_large")

    voice = (payload.get("voice") or "").strip() or config.VOICE_DEFAULT_VOICE
    if len(voice) > 64 or not voice.replace("_", "").isalnum():
        raise HubError(400, "Invalid voice", "bad_request")
    out = {"text": text, "voice": voice}
    speed = payload.get("speed")
    if speed is not None:
        try:
            out["speed"] = float(speed)
        except (TypeError, ValueError):
            raise HubError(400, "speed must be a number", "bad_request")

    url = f"{config.VOICE_URL}/speak"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=out)
    except httpx.HTTPError as e:
        raise HubError(502, f"Voice service unreachable: {e}", "voice_error")
    if r.status_code >= 400:
        # Log upstream details server-side only; never leak them to the client.
        log.warning("Voice speak upstream error %s: %s", r.status_code, r.text[:300])
        raise HubError(502, "Voice service error (please try again)", "voice_error")

    audio_bytes = r.content

    def _gen():
        yield audio_bytes

    return StreamingResponse(_gen(), media_type="audio/wav")


# ----------------------------------------------------------------------------
# Health: proxy the voice service health, never leak internals
# ----------------------------------------------------------------------------
@router.get("/health")
async def health(device=Depends(_chat)):
    url = f"{config.VOICE_URL}/healthz"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url)
    except httpx.HTTPError:
        # Fail closed but do not surface the upstream URL/error to the client.
        return {"available": False}
    if r.status_code >= 400:
        return {"available": False}
    try:
        body = r.json()
    except Exception:
        return {"available": False}
    # Surface only the safe, expected fields. Never echo arbitrary upstream data.
    return {
        "available": str(body.get("status", "")).lower() == "ok",
        "stt": body.get("stt"),
        "tts": body.get("tts"),
        "voice": body.get("voice"),
    }
