"""Image processing for the Images ribbon.

Runs each op as a subprocess in the FastSD venv (imgop_worker.py), writing the
result into FastSD's results/ dir so it shows up in the ribbon automatically.
Slow ops (img2img/rembg/upscale) run in background threads; a small in-flight
counter drives the UI's 'processing…' hint. 'to_studio' is a quick local copy.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from typing import Any, Dict, List

from . import config

_WORKER = os.path.join(config.FASTSD_DIR, "imgop_worker.py")
_PY = os.path.join(config.FASTSD_DIR, "env", "bin", "python")
_RESULTS = config.FASTSD_RESULTS_DIR

_OPS = ("img2img", "rembg", "upscale", "to_studio")
_LABEL = {"img2img": "variation", "rembg": "cut-out", "upscale": "upscaled"}

_LOCK = threading.Lock()
_INFLIGHT = {"n": 0}
# Serialize the heavy workers: only ONE image model may load at a time. Without
# this, multi-selecting N images spawned N subprocesses that each loaded a ~5GB
# model concurrently → 16GB overflow → the whole box froze. Extra jobs queue here
# (they still count as in-flight so the UI shows "processing").
_GEN_SEM = threading.Semaphore(1)

# Rough peak RAM (GB) each op needs to load its model. Refuse to start if the box
# doesn't have this much free — otherwise a worker loading sd-turbo (~5GB) ON TOP
# of FLUX still resident in the Studio (~12GB) overflows 16GB and freezes the box.
_OP_MEM_GB = {"img2img": 6.0, "upscale": 3.0, "rembg": 1.0, "to_studio": 0.0}


def _mem_available_gb() -> float:
    """Free RAM the kernel says is available right now (reclaimable cache included)."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 / 1024
    except Exception:
        pass
    return 999.0  # unknown → don't block


def _safe_src(name: str) -> str:
    """Resolve a client-supplied filename to a real file inside results/."""
    p = os.path.join(_RESULTS, os.path.basename(name))
    return p if os.path.isfile(p) else ""


def _run(op: str, src: str, prompt: str, scale: int) -> None:
    with _LOCK:
        _INFLIGHT["n"] += 1
    try:
        uid = uuid.uuid4().hex
        out = os.path.join(_RESULTS, f"{uid}-1.png")
        # nice/ionice: keep the box responsive while a job runs (best-effort).
        cmd = ["nice", "-n", "10", _PY, _WORKER, "--op", op, "--src", src, "--out", out]
        if prompt:
            cmd += ["--prompt", prompt]
        if op == "upscale":
            cmd += ["--scale", str(scale)]
        with _GEN_SEM:                       # one model in memory at a time
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode == 0 and os.path.isfile(out):
            label = (f"[{_LABEL.get(op, op)}] " + (prompt or os.path.basename(src))).strip()
            try:
                with open(os.path.join(_RESULTS, f"{uid}.json"), "w", encoding="utf-8") as f:
                    json.dump({"lcm_diffusion_setting": {"prompt": label[:200]}}, f)
            except OSError:
                pass
    except Exception:
        pass
    finally:
        with _LOCK:
            _INFLIGHT["n"] = max(0, _INFLIGHT["n"] - 1)


def _to_studio(src: str) -> None:
    from . import studio
    with open(src, "rb") as f:
        studio.add_upload(os.path.basename(src), f.read())


def process(op: str, files: List[str], prompt: str = "", scale: int = 2) -> Dict[str, Any]:
    if op not in _OPS:
        raise ValueError("bad op")
    # Memory guard: refuse to load a model if the box can't fit it on top of
    # whatever the Studio already has resident (prevents the FLUX + sd-turbo freeze).
    need = _OP_MEM_GB.get(op, 3.0)
    avail = _mem_available_gb()
    if need and avail < need and _INFLIGHT["n"] == 0:
        return {"op": op, "started": [], "skipped": [os.path.basename(f) for f in files],
                "error": "low_memory",
                "message": (f"Not enough free memory — {avail:.1f} GB free, this needs ~{int(need)} GB. "
                            "The Image Studio still has a model loaded (e.g. FLUX). Click "
                            "“Free image memory” to unload it, then try again.")}
    started, skipped = [], []
    for fn in files:
        src = _safe_src(fn)
        if not src:
            skipped.append(fn)
            continue
        if op == "to_studio":
            try:
                _to_studio(src)
            except Exception:
                skipped.append(fn)
                continue
        else:
            threading.Thread(target=_run, args=(op, src, prompt, int(scale)), daemon=True).start()
        started.append(os.path.basename(fn))
    return {"op": op, "started": started, "skipped": skipped}


def inflight() -> Dict[str, int]:
    with _LOCK:
        return {"processing": _INFLIGHT["n"]}
