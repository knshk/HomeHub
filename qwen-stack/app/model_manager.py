"""Model control plane.

The gateway is the authoritative control plane for the operator-managed models.
This module implements:

  * The lifecycle **state machine** (stopped -> running -> suspended) with the
    Ollama side effects (warm-load on start, unload on shutdown).
  * The **serve-gate** consulted in the proxy path: a suspended or stopped model
    is not served (the family hub and any BYO-key client get a clean 503).
  * **Ollama control** helpers (list installed tags, list loaded, load, unload,
    background pull) over Ollama's HTTP API.
  * Best-effort **resource** stats (per-model resident size from /api/ps, plus
    the ollama server's RSS/CPU and system memory read from /proc on Linux).

It is framework-agnostic (no FastAPI imports) so it stays easy to test and so the
serve-gate can be called cheaply from the hot request path.
"""

from __future__ import annotations

import asyncio
import glob
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import db
from .config import settings

# Warm-loads and unload calls are quick control operations; keep timeouts modest
# except for pulls (which stream a multi-GB download).
_CTRL_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_PS_TIMEOUT = httpx.Timeout(5.0)

# How long a warm-loaded model stays resident before Ollama evicts it.
KEEP_ALIVE = os.getenv("MODEL_KEEP_ALIVE", "30m")

# Legal transitions: {current_state: {action: next_state}}.
_TRANSITIONS = {
    "stopped": {"start": "running"},
    "running": {"suspend": "suspended", "shutdown": "stopped"},
    "suspended": {"resume": "running", "shutdown": "stopped"},
}


class ModelError(Exception):
    """Control-plane error carrying an HTTP status, message and machine code."""

    def __init__(self, status_code: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


# --------------------------------------------------------------------------- #
# Ollama HTTP helpers
# --------------------------------------------------------------------------- #
def _base() -> str:
    return settings.ollama_base_url


async def ollama_installed() -> List[Dict[str, Any]]:
    """Installed models from ``/api/tags`` -> [{tag, size_bytes}]."""
    try:
        async with httpx.AsyncClient(timeout=_PS_TIMEOUT) as client:
            resp = await client.get(f"{_base()}/api/tags")
            if resp.status_code != 200:
                return []
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for m in (data.get("models") or []):
        tag = m.get("name") or m.get("model")
        if tag:
            out.append({"tag": tag, "size_bytes": int(m.get("size") or 0)})
    return out


async def ollama_loaded() -> List[Dict[str, Any]]:
    """Currently resident models from ``/api/ps``."""
    try:
        async with httpx.AsyncClient(timeout=_PS_TIMEOUT) as client:
            resp = await client.get(f"{_base()}/api/ps")
            if resp.status_code != 200:
                return []
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for m in (data.get("models") or []):
        tag = m.get("name") or m.get("model")
        if not tag:
            continue
        out.append({
            "tag": tag,
            "size_bytes": int(m.get("size") or 0),
            "size_vram_bytes": int(m.get("size_vram") or 0),
            "expires_at": m.get("expires_at"),
        })
    return out


async def _loaded_tags() -> set:
    return {m["tag"] for m in await ollama_loaded()}


async def ollama_load(tag: str) -> None:
    """Warm-load a model into memory (best-effort; never raises)."""
    try:
        async with httpx.AsyncClient(timeout=_CTRL_TIMEOUT) as client:
            await client.post(
                f"{_base()}/api/generate",
                json={"model": tag, "prompt": "", "keep_alive": KEEP_ALIVE},
            )
    except httpx.HTTPError:
        pass


async def ollama_unload(tag: str) -> None:
    """Evict a model from memory (keep_alive=0). Best-effort; never raises."""
    try:
        async with httpx.AsyncClient(timeout=_CTRL_TIMEOUT) as client:
            await client.post(
                f"{_base()}/api/generate",
                json={"model": tag, "prompt": "", "keep_alive": 0},
            )
    except httpx.HTTPError:
        pass


# --------------------------------------------------------------------------- #
# Registry view (enriched with live Ollama + usage state)
# --------------------------------------------------------------------------- #
async def list_models_enriched(window_hours: int = 24) -> List[Dict[str, Any]]:
    """Managed models joined with live ``loaded`` status and windowed usage."""
    models = db.list_models()
    loaded = {}
    for m in await ollama_loaded():
        loaded[m["tag"]] = m
    since = _iso_hours_ago(window_hours)
    out = []
    for m in models:
        tag = m["ollama_tag"]
        live = loaded.get(tag)
        totals = db.model_usage_totals([m["alias"], tag], since)
        out.append({
            **m,
            "loaded": live is not None,
            "resident_bytes": int(live["size_bytes"]) if live else 0,
            "requests_24h": totals["requests"],
            "prompt_tokens_24h": totals["prompt_tokens"],
            "completion_tokens_24h": totals["completion_tokens"],
        })
    return out


# --------------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------------- #
def _require(alias: str) -> Dict[str, Any]:
    m = db.get_model(alias)
    if m is None:
        raise ModelError(404, f"Model '{alias}' is not registered.", "model_not_found")
    return m


async def apply_action(alias: str, action: str) -> Dict[str, Any]:
    """Apply a lifecycle action, performing the Ollama side effect. Returns the
    updated model row. Raises ModelError on an illegal transition."""
    m = _require(alias)
    state = m["state"]
    allowed = _TRANSITIONS.get(state, {})
    if action not in allowed:
        raise ModelError(
            409,
            f"Cannot '{action}' a model that is '{state}'.",
            "illegal_transition",
        )
    new_state = allowed[action]
    tag = m["ollama_tag"]

    # Side effects. State is written first so the serve-gate reflects intent
    # immediately; the (slow) warm-load runs in the background.
    db.set_model_state(alias, new_state)
    if action == "start":
        asyncio.create_task(ollama_load(tag))
    elif action == "shutdown":
        await ollama_unload(tag)
    # suspend/resume are gate-only; the model stays resident.

    updated = db.get_model(alias) or m
    return updated


# --------------------------------------------------------------------------- #
# Serve-gate (called from the proxy path)
# --------------------------------------------------------------------------- #
def serve_check(client_model: str) -> Optional[Tuple[int, str, str]]:
    """Decide whether a request for ``client_model`` may be served.

    Returns None when allowed (running, or an unmanaged pass-through model), else
    ``(status_code, message, code)`` describing why it is blocked.
    """
    m = db.get_model_by_alias_or_tag(client_model)
    if m is None:
        return None  # unmanaged / pass-through model — not gated
    state = m["state"]
    if state == "running":
        return None
    if state == "suspended":
        return (503, f"Model '{m['alias']}' is suspended by the administrator.",
                "model_suspended")
    return (503, f"Model '{m['alias']}' is stopped. Start it in the admin console.",
            "model_stopped")


# --------------------------------------------------------------------------- #
# Background pulls (adding new targeted models)
# --------------------------------------------------------------------------- #
_PULLS: Dict[str, Dict[str, Any]] = {}


async def _do_pull(tag: str) -> None:
    state = _PULLS[tag]
    try:
        timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{_base()}/api/pull",
                json={"model": tag, "stream": True},
            ) as resp:
                if resp.status_code >= 400:
                    state.update(status="error", detail=f"HTTP {resp.status_code}")
                    return
                async for line in resp.aiter_lines():
                    line = (line or "").strip()
                    if not line:
                        continue
                    import json
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("error"):
                        state.update(status="error", detail=str(ev["error"]))
                        return
                    completed = int(ev.get("completed") or 0)
                    total = int(ev.get("total") or 0)
                    state.update(
                        detail=ev.get("status") or state.get("detail"),
                        completed=completed,
                        total=total,
                        percent=round(100 * completed / total, 1) if total else state.get("percent", 0),
                    )
        state.update(status="done", percent=100.0)
    except httpx.HTTPError as exc:
        state.update(status="error", detail=f"{exc.__class__.__name__}")


def start_pull(tag: str) -> Dict[str, Any]:
    """Kick off (or resume reporting for) a background model pull."""
    cur = _PULLS.get(tag)
    if cur and cur.get("status") == "pulling":
        return cur
    _PULLS[tag] = {"tag": tag, "status": "pulling", "completed": 0,
                   "total": 0, "percent": 0.0, "detail": "starting"}
    asyncio.create_task(_do_pull(tag))
    return _PULLS[tag]


def pull_status(tag: str) -> Dict[str, Any]:
    return _PULLS.get(tag, {"tag": tag, "status": "idle"})


# --------------------------------------------------------------------------- #
# Auto-detection: role guessing, registry reconcile, GGUF folder import
# --------------------------------------------------------------------------- #
_VISION_HINTS = ("moondream", "llava", "bakllava", "-vl", "vl-", "vision",
                 "minicpm-v", "cogvlm", "qwen2-vl", "qwen2.5-vl")
_EMBED_HINTS = ("embed", "nomic-embed", "bge", "gte-", "-e5", "mxbai", "snowflake-arctic-embed")


def guess_role(tag: str) -> str:
    """Heuristically classify a model tag as embed | vision | chat."""
    t = tag.lower()
    if any(h in t for h in _EMBED_HINTS):
        return "embed"
    if any(h in t for h in _VISION_HINTS):
        return "vision"
    return "chat"


def derive_alias(tag: str) -> str:
    """Turn an ollama tag into a path-safe client alias.

    Strips a registry path and the ubiquitous ``:latest`` suffix so a model
    imported as ``mymodel:latest`` becomes the clean alias ``mymodel``.
    """
    name = tag.split("/")[-1]           # drop any registry path
    if name.endswith(":latest"):
        name = name[: -len(":latest")]
    alias = re.sub(r"[^A-Za-z0-9._-]", "-", name.replace(":", "-"))
    return alias.strip("-") or "model"


async def reconcile_registry() -> List[Dict[str, Any]]:
    """Register any installed Ollama model not already managed and not dismissed.

    New models are added 'stopped' with a guessed role. Returns the rows added.
    """
    installed = await ollama_installed()
    registered_tags = {m["ollama_tag"] for m in db.list_models()}
    dismissed = db.dismissed_tags()
    added: List[Dict[str, Any]] = []
    for it in installed:
        tag = it["tag"]
        if tag in registered_tags or tag in dismissed:
            continue
        # Pick a free alias (disambiguate against a different existing model).
        base = derive_alias(tag)
        alias, n = base, 1
        while True:
            existing = db.get_model(alias)
            if existing is None or existing["ollama_tag"] == tag:
                break
            alias = f"{base}-{n}"
            n += 1
        row = db.upsert_model(alias, tag, base, role=guess_role(tag), state="stopped")
        added.append(row)
    return added


# --- GGUF drop-in folder import ------------------------------------------- #
_IMPORTS: Dict[str, Dict[str, Any]] = {}


def _gguf_model_name(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"[^A-Za-z0-9._-]", "-", base).strip("-").lower() or "model"


async def _do_import(path: str, name: str) -> None:
    state = _IMPORTS[name]
    mfpath = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False) as tf:
            tf.write(f"FROM {path}\n")
            mfpath = tf.name
        env = {**os.environ,
               "OLLAMA_HOST": settings.ollama_base_url.split("://", 1)[-1]}
        proc = await asyncio.create_subprocess_exec(
            settings.ollama_bin, "create", name, "-f", mfpath,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        if proc.returncode == 0:
            state.update(status="done", detail="imported")
            await reconcile_registry()  # register the freshly-created tag
        else:
            tail = (out or b"").decode("utf-8", "ignore").strip().splitlines()
            state.update(status="error", detail=(tail[-1] if tail else "ollama create failed")[:200])
    except Exception as exc:  # noqa: BLE001 - surface any failure as status
        state.update(status="error", detail=f"{exc.__class__.__name__}: {str(exc)[:160]}")
    finally:
        if mfpath:
            try:
                os.unlink(mfpath)
            except OSError:
                pass


def _start_import(path: str, name: str) -> None:
    _IMPORTS[name] = {"name": name, "source": os.path.basename(path),
                      "status": "importing", "detail": "creating"}
    asyncio.create_task(_do_import(path, name))


async def scan_models_dir() -> Dict[str, Any]:
    """Scan MODELS_DIR for *.gguf files and import any not already in Ollama."""
    d = settings.models_dir
    out: Dict[str, Any] = {"dir": d, "importing": [], "already_present": [], "note": None}
    if not os.path.isdir(d):
        out["note"] = "models directory does not exist"
        return out
    installed_base = {it["tag"].split(":")[0] for it in await ollama_installed()}
    for path in sorted(glob.glob(os.path.join(d, "*.gguf"))):
        name = _gguf_model_name(path)
        if name in installed_base:
            out["already_present"].append(name)
            continue
        cur = _IMPORTS.get(name)
        if cur and cur.get("status") == "importing":
            out["importing"].append(name)
            continue
        _start_import(path, name)
        out["importing"].append(name)
    return out


def import_status(name: str) -> Dict[str, Any]:
    return _IMPORTS.get(name, {"name": name, "status": "idle"})


# --------------------------------------------------------------------------- #
# Resource stats (best-effort; Linux /proc + Ollama /api/ps)
# --------------------------------------------------------------------------- #
def _iso_hours_ago(hours: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _find_ollama_pid() -> Optional[int]:
    """Locate the `ollama serve` process pid via /proc (Linux)."""
    try:
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open(f"/proc/{name}/comm", "r") as fh:
                    if fh.read().strip() != "ollama":
                        continue
                with open(f"/proc/{name}/cmdline", "rb") as fh:
                    cmd = fh.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
                if "serve" in cmd:
                    return int(name)
            except (OSError, ValueError):
                continue
    except OSError:
        return None
    return None


def _proc_rss_bytes(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status", "r") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _proc_cpu_ticks(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/stat", "r") as fh:
            parts = fh.read().split()
        # utime (14) + stime (15), 1-indexed per proc(5).
        return int(parts[13]) + int(parts[14])
    except (OSError, ValueError, IndexError):
        return 0


def _system_mem() -> Dict[str, int]:
    out = {"mem_total_bytes": 0, "mem_available_bytes": 0}
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    out["mem_total_bytes"] = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    out["mem_available_bytes"] = int(line.split()[1]) * 1024
    except OSError:
        pass
    return out


async def resources() -> Dict[str, Any]:
    """Best-effort live resource snapshot for the dashboard."""
    pid = _find_ollama_pid()
    ollama: Dict[str, Any] = {"pid": pid, "rss_bytes": 0, "cpu_percent": None}
    if pid:
        ollama["rss_bytes"] = _proc_rss_bytes(pid)
        try:
            hz = os.sysconf("SC_CLK_TCK") or 100
            t0 = _proc_cpu_ticks(pid)
            await asyncio.sleep(0.2)
            t1 = _proc_cpu_ticks(pid)
            ollama["cpu_percent"] = round(100.0 * (t1 - t0) / (0.2 * hz), 1)
        except (OSError, ValueError):
            ollama["cpu_percent"] = None
    return {
        "ollama": ollama,
        "system": {**_system_mem(), "cpu_count": os.cpu_count()},
        "loaded": await ollama_loaded(),
        "max_loaded_models": int(os.getenv("OLLAMA_MAX_LOADED_MODELS", "0")) or None,
    }
