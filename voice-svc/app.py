"""Home Hub voice service — local STT (faster-whisper) + TTS (kokoro-onnx).

Runs OUTSIDE Ollama as a small localhost service the Home Hub proxies to.

Client contract (the Hub is the only client):
  GET  /healthz    -> {"status","stt","tts","voice","states"}
  POST /transcribe -> multipart "audio" (+ optional "language") -> {"text","language"}
  POST /speak      -> JSON {"text","voice"?,"speed"?} -> audio/wav

Control-plane contract (proxied by the Hub's admin "Models" tab; localhost-only,
same trust model as the client endpoints):
  GET  /admin/models                 -> {"models":[{name,role,display_name,state,loaded,requests_24h}]}
  POST /admin/models/{name}/{action} -> start|suspend|resume|shutdown -> model view
  GET  /admin/models/{name}/metrics  -> {"name","series":[{ts,requests}],"totals":{requests}}
  GET  /admin/resources              -> {"voice":{pid,rss_bytes},"loaded":[...]}

Lifecycle (mirrors the gateway): stopped -(start)-> running -(suspend)-> suspended
-(resume)-> running; running/suspended -(shutdown)-> stopped. `start` loads the
model into memory, `shutdown` frees it, `suspend` keeps it resident but gates
requests. State is persisted in SQLite so it survives a restart.

Calls are serialized with per-model locks. Licenses: faster-whisper MIT,
Kokoro-82M Apache-2.0.
"""
from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import threading
import wave
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import Body, FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response

ROOT = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(ROOT, "models")
DB_PATH = os.path.join(ROOT, "data", "voice.db")
VOICE_DEFAULT = os.environ.get("VOICE_DEFAULT", "af_sarah")
WHISPER_SIZE = os.environ.get("WHISPER_SIZE", "base")
TTS_LANG = os.environ.get("TTS_LANG", "en-us")
MAX_AUDIO_BYTES = int(os.environ.get("MAX_AUDIO_BYTES", str(25 * 1024 * 1024)))
MAX_TTS_CHARS = int(os.environ.get("MAX_TTS_CHARS", "2000"))

app = FastAPI(title="home-hub-voice", version="1.1.0")

_stt_lock = threading.Lock()
_tts_lock = threading.Lock()

from kokoro_onnx import Kokoro  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

# Models are managed (loaded/unloaded on demand); start unloaded and let the
# startup hook load whatever isn't left 'stopped'.
_whisper = None
_kokoro = None

# Static registry of the two voice models this service manages.
_REGISTRY = {
    "stt": {"role": "stt", "display_name": f"faster-whisper ({WHISPER_SIZE})"},
    "tts": {"role": "tts", "display_name": "Kokoro-82M"},
}
_STATES = ("stopped", "running", "suspended")
_TRANSITIONS = {
    "stopped": {"start": "running"},
    "running": {"suspend": "suspended", "shutdown": "stopped"},
    "suspended": {"resume": "running", "shutdown": "stopped"},
}


# --------------------------------------------------------------------------- #
# Persistence (state + usage log)
# --------------------------------------------------------------------------- #
def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    conn = _db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_state (
                name TEXT PRIMARY KEY, state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, model TEXT, status INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_vusage_ts ON usage_log(ts);
            """
        )
        for name in _REGISTRY:
            conn.execute(
                "INSERT OR IGNORE INTO model_state(name, state) VALUES(?, 'running')",
                (name,),
            )
        conn.commit()
    finally:
        conn.close()


def _get_state(name: str) -> str | None:
    conn = _db()
    try:
        row = conn.execute("SELECT state FROM model_state WHERE name=?", (name,)).fetchone()
        return row["state"] if row else None
    finally:
        conn.close()


def _set_state(name: str, state: str) -> None:
    conn = _db()
    try:
        conn.execute("UPDATE model_state SET state=? WHERE name=?", (state, name))
        conn.commit()
    finally:
        conn.close()


def _log(model: str, status: int) -> None:
    try:
        conn = _db()
        try:
            conn.execute("INSERT INTO usage_log(ts, model, status) VALUES(?,?,?)",
                         (_utcnow(), model, int(status)))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # metrics logging must never break a request


# --------------------------------------------------------------------------- #
# Model load / unload
# --------------------------------------------------------------------------- #
def _load_model(name: str) -> None:
    global _whisper, _kokoro
    if name == "stt" and _whisper is None:
        _whisper = WhisperModel(WHISPER_SIZE, device="cpu", compute_type="int8")
    elif name == "tts" and _kokoro is None:
        _kokoro = Kokoro(
            os.path.join(MODELS, "kokoro-v1.0.onnx"),
            os.path.join(MODELS, "voices-v1.0.bin"),
        )


def _unload_model(name: str) -> None:
    global _whisper, _kokoro
    if name == "stt":
        _whisper = None
    elif name == "tts":
        _kokoro = None
    import gc
    gc.collect()


def _is_loaded(name: str) -> bool:
    return (_whisper is not None) if name == "stt" else (_kokoro is not None)


def _lock_for(name: str) -> threading.Lock:
    return _stt_lock if name == "stt" else _tts_lock


@app.on_event("startup")
def _startup() -> None:
    _init_db()
    for name in _REGISTRY:
        if _get_state(name) != "stopped":
            try:
                with _lock_for(name):
                    _load_model(name)
            except Exception:
                pass  # a load failure leaves it unloaded; admin can retry start


def _gate(name: str):
    """Return a 503 JSONResponse if the model may not serve, else None."""
    state = _get_state(name)
    if state != "running":
        code = f"model_{state}" if state in ("suspended", "stopped") else "model_unavailable"
        return JSONResponse(
            {"error": {"message": f"{_REGISTRY[name]['display_name']} is {state}.", "code": code}},
            status_code=503,
        )
    if not _is_loaded(name):
        return JSONResponse(
            {"error": {"message": f"{_REGISTRY[name]['display_name']} is not loaded.",
                       "code": "model_stopped"}},
            status_code=503,
        )
    return None


# --------------------------------------------------------------------------- #
# Audio helper
# --------------------------------------------------------------------------- #
def _wav_bytes(samples, sr: int) -> bytes:
    """Float samples in [-1,1] -> 16-bit mono WAV bytes (stdlib, no libsndfile)."""
    x = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Client endpoints
# --------------------------------------------------------------------------- #
@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "stt": f"faster-whisper:{WHISPER_SIZE}",
        "tts": "kokoro-onnx",
        "voice": VOICE_DEFAULT,
        "states": {n: _get_state(n) for n in _REGISTRY},
    }


@app.post("/transcribe")
def transcribe(audio: UploadFile = File(...), language: str = Form(default="")):
    gated = _gate("stt")
    if gated is not None:
        _log("stt", 503)
        return gated
    data = audio.file.read()
    if not data:
        return JSONResponse({"error": "empty audio"}, status_code=400)
    if len(data) > MAX_AUDIO_BYTES:
        return JSONResponse({"error": "audio too large"}, status_code=413)
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(data)
        path = tf.name
    try:
        with _stt_lock:
            segments, info = _whisper.transcribe(
                path,
                beam_size=1,
                language=(language or None),
                vad_filter=True,
            )
            text = "".join(s.text for s in segments).strip()
        _log("stt", 200)
        return {"text": text, "language": getattr(info, "language", "") or ""}
    except Exception as e:  # fail closed with a generic message
        _log("stt", 500)
        return JSONResponse({"error": "transcription_failed", "detail": str(e)[:200]}, status_code=500)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@app.post("/speak")
def speak(payload: dict = Body(...)):
    gated = _gate("tts")
    if gated is not None:
        _log("tts", 503)
        return gated
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    text = text[:MAX_TTS_CHARS]
    voice = (payload.get("voice") or VOICE_DEFAULT).strip() or VOICE_DEFAULT
    try:
        speed = float(payload.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    speed = min(2.0, max(0.5, speed))
    try:
        with _tts_lock:
            samples, sr = _kokoro.create(text, voice=voice, speed=speed, lang=TTS_LANG)
        _log("tts", 200)
        return Response(content=_wav_bytes(samples, sr), media_type="audio/wav")
    except Exception as e:
        _log("tts", 500)
        return JSONResponse({"error": "tts_failed", "detail": str(e)[:200]}, status_code=500)


# --------------------------------------------------------------------------- #
# Admin (control-plane) endpoints — localhost only, proxied by the Hub
# --------------------------------------------------------------------------- #
def _hours_ago_iso(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _requests_since(name: str, since_iso: str) -> int:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM usage_log WHERE model=? AND ts>=?",
            (name, since_iso),
        ).fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def _model_view(name: str) -> dict:
    return {
        "name": name,
        "role": _REGISTRY[name]["role"],
        "display_name": _REGISTRY[name]["display_name"],
        "state": _get_state(name),
        "loaded": _is_loaded(name),
        "requests_24h": _requests_since(name, _hours_ago_iso(24)),
    }


def _self_rss() -> int:
    try:
        with open("/proc/self/status", "r") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


@app.get("/admin/models")
def admin_models():
    return {"models": [_model_view(n) for n in _REGISTRY]}


@app.post("/admin/models/{name}/{action}")
def admin_action(name: str, action: str):
    if name not in _REGISTRY:
        return JSONResponse({"error": {"message": "unknown model", "code": "not_found"}}, status_code=404)
    if action not in ("start", "suspend", "resume", "shutdown"):
        return JSONResponse({"error": {"message": f"unknown action '{action}'", "code": "bad_request"}}, status_code=400)
    state = _get_state(name)
    allowed = _TRANSITIONS.get(state, {})
    if action not in allowed:
        return JSONResponse(
            {"error": {"message": f"Cannot '{action}' while {state}.", "code": "illegal_transition"}},
            status_code=409,
        )
    new_state = allowed[action]
    lock = _lock_for(name)
    if action == "start":
        try:
            with lock:
                _load_model(name)
        except Exception as e:
            return JSONResponse(
                {"error": {"message": f"load failed: {str(e)[:120]}", "code": "load_failed"}},
                status_code=500,
            )
    elif action == "shutdown":
        with lock:
            _unload_model(name)
    _set_state(name, new_state)
    return _model_view(name)


@app.get("/admin/models/{name}/metrics")
def admin_metrics(name: str, hours: int = 24):
    if name not in _REGISTRY:
        return JSONResponse({"error": {"message": "unknown model", "code": "not_found"}}, status_code=404)
    hours = max(1, min(int(hours), 24 * 30))
    now = datetime.now(timezone.utc)
    anchor = now.replace(minute=0, second=0, microsecond=0)
    keys = [(anchor - timedelta(hours=i)).strftime("%Y-%m-%dT%H") for i in range(hours)][::-1]
    since = (anchor - timedelta(hours=hours - 1)).isoformat()

    conn = _db()
    try:
        rows = conn.execute(
            "SELECT substr(ts,1,13) AS b, COUNT(*) AS n FROM usage_log "
            "WHERE model=? AND ts>=? GROUP BY b",
            (name, since),
        ).fetchall()
    finally:
        conn.close()
    grouped = {r["b"]: int(r["n"]) for r in rows}
    series = [{"ts": k, "requests": grouped.get(k, 0)} for k in keys]
    return {"name": name, "hours": hours, "series": series,
            "totals": {"requests": sum(grouped.values())}}


@app.get("/admin/resources")
def admin_resources():
    return {
        "voice": {"pid": os.getpid(), "rss_bytes": _self_rss()},
        "loaded": [n for n in _REGISTRY if _is_loaded(n)],
    }
