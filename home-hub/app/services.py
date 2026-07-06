"""Local model-service control (start/stop) + status + downloadable catalog.

Two mutually-exclusive stacks on a 16 GB box:
  * "ai"     = Ollama + gateway + voice  (the 5 models: chat/vision/embed + STT/TTS)
  * "images" = the FastSD Image Studio    (separate; art generation)
Starting one stops the other. The hub itself stays up throughout.

Also exposes the downloadable image-model catalog enriched with purpose + minimum
requirements for the "Add new models" screen.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from typing import Any, Dict, List

from . import config

PORTS = {"ollama": 11434, "gateway": 8080, "voice": 8100, "images": 7860}


def _port_up(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.6)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def status() -> Dict[str, Any]:
    ollama, gateway = _port_up(PORTS["ollama"]), _port_up(PORTS["gateway"])
    return {
        "ai": {"running": ollama and gateway, "ollama": ollama,
               "gateway": gateway, "voice": _port_up(PORTS["voice"])},
        "images": {"running": _port_up(PORTS["images"]), "url": config.PUBLIC_IMAGES_URL},
    }


def _sh(cmd: str) -> None:
    """Run a detached shell command (survives the request + a hub restart)."""
    subprocess.Popen(
        ["bash", "-lc", cmd],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def _kill_port(port: int) -> None:
    _sh(
        "pid=$(ss -ltnp 2>/dev/null | awk -v x=':%d$' '$4 ~ x {print $NF}' "
        "| grep -oE 'pid=[0-9]+' | grep -oE '[0-9]+' | head -1); "
        '[ -n "$pid" ] && kill "$pid"' % port
    )


def stop(name: str) -> None:
    if name == "ai":
        _kill_port(PORTS["gateway"]); _kill_port(PORTS["ollama"]); _kill_port(PORTS["voice"])
    elif name == "images":
        _kill_port(PORTS["images"])
    else:
        raise ValueError(name)


def start(name: str) -> None:
    if name == "ai":
        stop("images")  # RAM exclusivity: the two stacks cannot coexist on 16 GB
        _sh(f"cd {config.QWEN_STACK_DIR} && bash start-all.sh")   # ollama + gateway
        _sh(f"cd {config.VOICE_SVC_DIR} && bash start.sh")        # voice (STT/TTS)
    elif name == "images":
        stop("ai")      # RAM exclusivity
        _sh(f"cd {config.FASTSD_DIR} && GRADIO_SERVER_NAME=0.0.0.0 GRADIO_SERVER_PORT=7860 "
            f"nice -n 10 env/bin/python src/app.py -w >> webui.log 2>&1")
    else:
        raise ValueError(name)


def free_image_memory() -> None:
    """Restart the Image Studio to drop any resident model (frees RAM back to ~0.7GB).
    Used before an img2img/upscale when the Studio is still holding FLUX."""
    _kill_port(PORTS["images"])
    _sh(f"cd {config.FASTSD_DIR} && GRADIO_SERVER_NAME=0.0.0.0 GRADIO_SERVER_PORT=7860 "
        f"nice -n 10 env/bin/python src/app.py -w >> webui.log 2>&1")


# --------------------------------------------------------------------------- #
# Downloadable image-model catalog (FastSD OpenVINO) — enriched for the UI
# --------------------------------------------------------------------------- #
# purpose + minimum RAM (this is CPU/OpenVINO; RAM is the constraint) + license.
_IMG_META = {
    "rupeshs/sd-turbo-openvino":
        {"purpose": "Fast general 2D art — SD-Turbo, 1–4 steps, 512px", "min_ram_gb": 9, "license": "Apache-2.0"},
    "rupeshs/sdxs-512-0.9-openvino":
        {"purpose": "Ultra-fast tiny model for 512px sketches", "min_ram_gb": 6, "license": "open"},
    "rupeshs/hyper-sd-sdxl-1-step-openvino-int8":
        {"purpose": "SDXL quality in 1 step (Hyper-SD, int8)", "min_ram_gb": 11, "license": "open"},
    "rupeshs/SDXL-Lightning-2steps-openvino-int8":
        {"purpose": "SDXL quality in 2 steps (Lightning, int8)", "min_ram_gb": 11, "license": "OpenRAIL"},
    "rupeshs/sdxl-turbo-openvino-int8":
        {"purpose": "SDXL-Turbo previews (fast, previews only)", "min_ram_gb": 11, "license": "non-commercial"},
    "rupeshs/LCM-dreamshaper-v7-openvino":
        {"purpose": "Painterly LCM (Dreamshaper, SD1.5)", "min_ram_gb": 8, "license": "open"},
    "Disty0/LCM_SoteMix":
        {"purpose": "Anime / stylized LCM", "min_ram_gb": 8, "license": "open"},
    "rupeshs/sd15-lcm-square-openvino-int8":
        {"purpose": "SD1.5 LCM, square, int8 (light)", "min_ram_gb": 6, "license": "open"},
    "OpenVINO/FLUX.1-schnell-int4-ov":
        {"purpose": "High-quality FLUX-schnell (int4, heavier)", "min_ram_gb": 16, "license": "Apache-2.0"},
    "rupeshs/sana-sprint-0.6b-openvino":
        {"purpose": "Efficient Sana Sprint 0.6B", "min_ram_gb": 7, "license": "open"},
    "rupeshs/flux2-klein-4b-int4-ov":
        {"purpose": "FLUX.2 Klein 4B (int4, quality)", "min_ram_gb": 12, "license": "open"},
}
_DEFAULT_META = {"purpose": "OpenVINO image model", "min_ram_gb": 10, "license": "open"}


def image_models() -> Dict[str, Any]:
    running = _port_up(PORTS["images"])
    cfg = os.path.join(config.FASTSD_DIR, "configs", "openvino-lcm-models.txt")
    cache = os.path.expanduser("~/.cache/huggingface/hub")
    models: List[Dict[str, Any]] = []
    if os.path.isfile(cfg):
        with open(cfg, "r", encoding="utf-8") as fh:
            for line in fh:
                mid = line.strip()
                if not mid:
                    continue
                meta = _IMG_META.get(mid, _DEFAULT_META)
                cache_dir = os.path.join(cache, "models--" + mid.replace("/", "--"))
                models.append({
                    "id": mid,
                    "kind": "image",
                    "cached": os.path.isdir(cache_dir),
                    "recommended": mid == "rupeshs/sd-turbo-openvino",
                    "purpose": meta["purpose"],
                    "min_ram_gb": meta["min_ram_gb"],
                    "license": meta["license"],
                })
    # FLUX.1-schnell (GGUF via stable-diffusion.cpp) — a separate generation mode,
    # not an OpenVINO model, so its files live locally (not the HF cache).
    gguf_diff = os.path.join(config.FASTSD_DIR, "models", "gguf", "diffusion",
                             "flux1-schnell-q4_0.gguf")
    gguf_lib = os.path.join(config.FASTSD_DIR, "libstable-diffusion.so")
    models.append({
        "id": "flux1-schnell-gguf",
        "kind": "image",
        "mode": "GGUF",
        "cached": os.path.isfile(gguf_diff) and os.path.isfile(gguf_lib),
        "recommended": False,
        "purpose": ("Best quality for print / TV — FLUX.1-schnell (Q4 GGUF), "
                    "native up to 1024px, ~4 steps. Slow on CPU (minutes/image) "
                    "but the go-to for shippable game art."),
        "min_ram_gb": 12,
        "license": "Apache-2.0",
    })
    return {"running": running, "url": config.PUBLIC_IMAGES_URL, "models": models}


def download_image_model(model_id: str) -> None:
    """Pre-cache an OpenVINO image model via the FastSD env's huggingface_hub.
    Deliberate downloads override the global offline mode for this one command."""
    py = os.path.join(config.FASTSD_DIR, "env", "bin", "python")
    safe = model_id.replace("'", "")
    _sh(f"HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 '{py}' -c "
        f"\"from huggingface_hub import snapshot_download; snapshot_download('{safe}')\"")


# --------------------------------------------------------------------------- #
# AI-model cache — so the Models page can list the models even when the AI
# stack is stopped (otherwise the gateway/voice can't be queried).
# --------------------------------------------------------------------------- #
_DEFAULT_LLM = [
    {"alias": "qwen2.5-7b", "ollama_tag": "qwen2.5:7b-instruct-q4_K_M",
     "display_name": "qwen2.5-7b", "role": "chat", "state": "stopped"},
    {"alias": "moondream", "ollama_tag": "moondream:latest",
     "display_name": "moondream (vision)", "role": "vision", "state": "stopped"},
    {"alias": "nomic-embed-text", "ollama_tag": "nomic-embed-text:latest",
     "display_name": "nomic-embed-text", "role": "embed", "state": "stopped"},
]
_DEFAULT_VOICE = [
    {"name": "stt", "role": "stt", "display_name": "faster-whisper (base)", "state": "stopped"},
    {"name": "tts", "role": "tts", "display_name": "Kokoro-82M", "state": "stopped"},
]


def _cache_path() -> str:
    return os.path.join(str(config.DATA_DIR), "models_cache.json")


def cache_ai_models(llm: List[Dict[str, Any]], voice: List[Dict[str, Any]]) -> None:
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump({"llm": llm, "voice": voice}, f)
    except Exception:
        pass


def load_cached_ai_models() -> Dict[str, List[Dict[str, Any]]]:
    """Last-known live list, or the core-5 defaults if we've never seen it up."""
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            c = json.load(f)
        if c.get("llm") or c.get("voice"):
            return {"llm": c.get("llm", []), "voice": c.get("voice", [])}
    except Exception:
        pass
    return {"llm": list(_DEFAULT_LLM), "voice": list(_DEFAULT_VOICE)}


# --------------------------------------------------------------------------- #
# Resource usage: per-model disk (always, even when the model is down), plus
# live RAM/CPU per service (only when running). Disk is cached (it changes only
# when a model is pulled/removed). All best-effort — never raises.
# --------------------------------------------------------------------------- #
_WHISPER_CACHE = os.path.expanduser("~/.cache/huggingface/hub/models--Systran--faster-whisper-base")
_HF_HUB = os.path.expanduser("~/.cache/huggingface/hub")
_OLLAMA_MODELS = os.path.expanduser("~/.ollama/models")
_DISK_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_DISK_TTL = 30.0


def _du_bytes(path: str) -> int:
    if not path or not os.path.exists(path):
        return 0
    try:
        r = subprocess.run(["du", "-sb", path], capture_output=True, text=True, timeout=20)
        # du prints the grand total even when it exits 1 (e.g. an unreadable
        # subdir), so trust stdout when it parses rather than gating on rc.
        parts = r.stdout.split()
        return int(parts[0]) if parts and parts[0].isdigit() else 0
    except Exception:
        return 0


def _file_bytes(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _ollama_disk_map() -> Dict[str, int]:
    """Map 'name:tag' -> bytes by parsing manifests (works when ollama is down)."""
    base = os.path.join(_OLLAMA_MODELS, "manifests")
    out: Dict[str, int] = {}
    if not os.path.isdir(base):
        return out
    for root, _dirs, files in os.walk(base):
        for fn in files:
            try:
                mf = json.load(open(os.path.join(root, fn), encoding="utf-8"))
                if not isinstance(mf, dict):
                    continue
                cfg = mf.get("config")
                size = int(cfg.get("size", 0)) if isinstance(cfg, dict) else 0
                for layer in (mf.get("layers") or []):
                    if isinstance(layer, dict):
                        size += int(layer.get("size", 0) or 0)
            except Exception:
                continue
            out[f"{os.path.basename(root)}:{fn}"] = size
    return out


def _ollama_model_disk(tag: str, dmap: Dict[str, int]) -> int:
    if not tag:
        return 0
    key = tag if ":" in tag else tag + ":latest"
    return dmap.get(key, 0)


def _kokoro_files() -> List[str]:
    d = os.path.join(config.VOICE_SVC_DIR, "models")
    return [os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin")]


def _hf_cache_dir(model_id: str) -> str:
    return os.path.join(_HF_HUB, "models--" + model_id.replace("/", "--"))


def _flux_gguf_disk() -> int:
    """FLUX GGUF isn't an HF-cache model: its files live in fastsdcpu/models/gguf/*
    (diffusion + clip + t5xxl + vae) plus the sd.cpp shared library."""
    gguf_dir = os.path.join(config.FASTSD_DIR, "models", "gguf")
    so = os.path.join(config.FASTSD_DIR, "libstable-diffusion.so")
    return _du_bytes(gguf_dir) + _file_bytes(so)


def _image_model_disk(m: Dict[str, Any]) -> int:
    if m.get("id") == "flux1-schnell-gguf":
        return _flux_gguf_disk()
    return _du_bytes(_hf_cache_dir(m["id"]))


def _compute_disk() -> Dict[str, Any]:
    dmap = _ollama_disk_map()
    cached = load_cached_ai_models()
    llm = {m["alias"]: _ollama_model_disk(m.get("ollama_tag", m["alias"]), dmap)
           for m in cached.get("llm", [])}
    voice = {
        "stt": _du_bytes(_WHISPER_CACHE),
        "tts": sum(_file_bytes(f) for f in _kokoro_files()),
    }
    image = {m["id"]: _image_model_disk(m)
             for m in image_models()["models"] if m["cached"]}
    ai_disk = sum(llm.values()) + sum(voice.values())
    img_disk = sum(image.values())
    return {"llm": llm, "voice": voice, "image": image,
            "ai_disk": ai_disk, "img_disk": img_disk, "total_disk": ai_disk + img_disk}


def _disk_cached() -> Dict[str, Any]:
    now = time.monotonic()
    if _DISK_CACHE["data"] is None or now - _DISK_CACHE["ts"] > _DISK_TTL:
        _DISK_CACHE["data"] = _compute_disk()
        _DISK_CACHE["ts"] = now
    return _DISK_CACHE["data"]


def _listen_pids() -> Dict[int, int]:
    """port -> pid for listening sockets (single ss call)."""
    out: Dict[int, int] = {}
    try:
        r = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            port_m = re.search(r":(\d+)$", parts[3])
            pid_m = re.search(r"pid=(\d+)", line)
            if port_m and pid_m:
                out[int(port_m.group(1))] = int(pid_m.group(1))
    except Exception:
        pass
    return out


def _proc_rss(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _proc_ticks(pid: int) -> int:
    """utime+stime (clock ticks). Parse after the last ')' so a comm containing
    spaces or parentheses doesn't shift the field offsets."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            raw = f.read()
        rest = raw[raw.rfind(")") + 1:].split()   # rest[0]=state, rest[1]=ppid
        return int(rest[11]) + int(rest[12])        # utime(14) + stime(15)
    except (OSError, ValueError, IndexError):
        return 0


def _child_map() -> Dict[int, List[int]]:
    """ppid -> [child pids] over all processes (single /proc scan)."""
    kids: Dict[int, List[int]] = {}
    try:
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open(f"/proc/{name}/stat", encoding="utf-8") as f:
                    raw = f.read()
                ppid = int(raw[raw.rfind(")") + 1:].split()[1])
                kids.setdefault(ppid, []).append(int(name))
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        pass
    return kids


def _descendants(pid: int, kids: Dict[int, List[int]]) -> List[int]:
    out, stack, seen = [], [pid], set()
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        stack.extend(kids.get(p, []))
    return out


def _proc_stats(pids: List[int]) -> Dict[str, Any]:
    """RSS + CPU% for the given service PIDs AND their descendants — e.g. the
    `ollama runner` child that actually holds the model weights and burns CPU."""
    pids = [p for p in pids if p]
    if not pids:
        return {"rss_bytes": 0, "cpu_percent": 0.0}
    kids = _child_map()
    allp = set()
    for p in pids:
        allp.update(_descendants(p, kids))
    hz = os.sysconf("SC_CLK_TCK") or 100
    start = time.monotonic()
    t0 = {p: _proc_ticks(p) for p in allp}
    time.sleep(0.2)
    elapsed = max(0.05, time.monotonic() - start)
    rss = sum(_proc_rss(p) for p in allp)
    cpu = sum(max(0, _proc_ticks(p) - t0.get(p, 0)) for p in allp)
    return {"rss_bytes": rss, "cpu_percent": round(100.0 * cpu / (elapsed * hz), 1)}


def resources_overview() -> Dict[str, Any]:
    """Per-service + aggregate resources. Disk is always reported; RAM/CPU only
    when the service is running (0/absent otherwise)."""
    disk = _disk_cached()
    st = status()
    ai_running = bool(st["ai"]["running"])
    img_running = bool(st["images"]["running"])
    pids = _listen_pids()
    ai_proc = _proc_stats([pids.get(11434), pids.get(8080), pids.get(8100)]) if ai_running else None
    img_proc = _proc_stats([pids.get(7860)]) if img_running else None

    ai_models = {k: {"disk_bytes": v} for k, v in disk["llm"].items()}
    ai_models.update({k: {"disk_bytes": v} for k, v in disk["voice"].items()})
    img_models = {k: {"disk_bytes": v} for k, v in disk["image"].items()}

    def svc(running, disk_bytes, proc, models):
        d = {"running": running, "disk_bytes": disk_bytes, "models": models}
        if running and proc:
            d["rss_bytes"] = proc["rss_bytes"]
            d["cpu_percent"] = proc["cpu_percent"]
        return d

    agg = {"disk_bytes": disk["total_disk"]}
    if ai_running or img_running:
        agg["rss_bytes"] = (ai_proc["rss_bytes"] if ai_proc else 0) + (img_proc["rss_bytes"] if img_proc else 0)
        agg["cpu_percent"] = round((ai_proc["cpu_percent"] if ai_proc else 0)
                                   + (img_proc["cpu_percent"] if img_proc else 0), 1)
    return {
        "services": {
            "ai": svc(ai_running, disk["ai_disk"], ai_proc, ai_models),
            "images": svc(img_running, disk["img_disk"], img_proc, img_models),
        },
        "aggregate": agg,
    }
