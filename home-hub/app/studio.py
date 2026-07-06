"""Studio — art/animation asset pipeline.

A manifest-backed store that is the **single source of truth the games read**.
Each asset has:
  - a source still image,
  - an optional animation: a hand-rigged Rive file (.riv) OR a generated motion
    clip (.webp from the CPU animator; a real AI model can replace this later),
  - a status: draft | rigging | ready,
  - catalog id + game tags + notes.

Files live under STUDIO_DIR/{images,rive,anim}; the manifest is manifest.json.
All paths in the manifest are relative to STUDIO_DIR and served read-only under
/studio-files.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

_LOCK = threading.RLock()
_VALID_STATUS = ("draft", "rigging", "ready")
_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest_path() -> Path:
    return config.STUDIO_DIR / "manifest.json"


def _slug(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()
    return base or "asset"


def load_manifest() -> Dict[str, Any]:
    p = _manifest_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def save_manifest(m: Dict[str, Any]) -> None:
    p = _manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2), encoding="utf-8")
    tmp.replace(p)


def _unique_id(m: Dict[str, Any], base: str) -> str:
    if base not in m:
        return base
    i = 2
    while f"{base}-{i}" in m:
        i += 1
    return f"{base}-{i}"


def _view(asset: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich a manifest entry with browser URLs (served under /studio-files)."""
    out = dict(asset)
    out["source_url"] = f"/studio-files/{asset['source']}" if asset.get("source") else None
    out["animation_url"] = f"/studio-files/{asset['animation']}" if asset.get("animation") else None
    return out


def list_assets() -> List[Dict[str, Any]]:
    with _LOCK:
        m = load_manifest()
        return [_view(a) for a in sorted(m.values(), key=lambda a: a.get("created", ""), reverse=True)]


def import_generated() -> List[Dict[str, Any]]:
    """Copy new images from the FastSD results dir into the studio as drafts."""
    src_dir = Path(config.FASTSD_RESULTS_DIR)
    if not src_dir.is_dir():
        return []
    with _LOCK:
        m = load_manifest()
        known = {a.get("origin") for a in m.values()}
        added = []
        for f in sorted(src_dir.iterdir()):
            if f.suffix.lower() not in _IMG_EXT or not f.is_file():
                continue
            origin = str(f.resolve())
            if origin in known:
                continue
            added.append(_add_image_file(m, f.read_bytes(), f.name, origin=origin))
        if added:
            save_manifest(m)
        return [_view(a) for a in added]


def add_upload(filename: str, data: bytes) -> Dict[str, Any]:
    with _LOCK:
        m = load_manifest()
        a = _add_image_file(m, data, filename, origin=None)
        save_manifest(m)
        return _view(a)


def _add_image_file(m: Dict[str, Any], data: bytes, filename: str,
                    origin: Optional[str]) -> Dict[str, Any]:
    stem = Path(filename).stem
    ext = Path(filename).suffix.lower()
    if ext not in _IMG_EXT:
        ext = ".png"
    aid = _unique_id(m, _slug(stem))
    rel = f"images/{aid}{ext}"
    dest = config.STUDIO_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    asset = {
        "id": aid,
        "name": stem,
        "catalogId": "",
        "source": rel,
        "animation": None,
        "animationType": None,
        "status": "draft",
        "games": [],
        "notes": "",
        "origin": origin,
        "created": _now(),
        "updated": _now(),
    }
    m[aid] = asset
    return asset


def get(aid: str) -> Optional[Dict[str, Any]]:
    return load_manifest().get(aid)


def set_rive(aid: str, data: bytes) -> Dict[str, Any]:
    with _LOCK:
        m = load_manifest()
        a = m.get(aid)
        if a is None:
            raise KeyError(aid)
        rel = f"rive/{aid}.riv"
        (config.STUDIO_DIR / rel).write_bytes(data)
        a["animation"] = rel
        a["animationType"] = "rive"
        a["updated"] = _now()
        save_manifest(m)
        return _view(a)


def animate_cpu(aid: str) -> Dict[str, Any]:
    """Generate a gentle procedural motion .webp from the still (CPU).

    Placeholder for real AI animation (AnimateDiff/SVD) — same slot, swap later.
    """
    with _LOCK:
        m = load_manifest()
        a = m.get(aid)
        if a is None:
            raise KeyError(aid)
        src = config.STUDIO_DIR / a["source"]
        rel = f"anim/{aid}.webp"
        _procedural_motion(src, config.STUDIO_DIR / rel)
        a["animation"] = rel
        a["animationType"] = "webp"
        a["updated"] = _now()
        save_manifest(m)
        return _view(a)


def update_meta(aid: str, *, catalogId=None, games=None, notes=None,
                status=None, name=None) -> Dict[str, Any]:
    with _LOCK:
        m = load_manifest()
        a = m.get(aid)
        if a is None:
            raise KeyError(aid)
        if catalogId is not None:
            a["catalogId"] = str(catalogId)[:120]
        if name is not None:
            a["name"] = str(name)[:200]
        if notes is not None:
            a["notes"] = str(notes)[:2000]
        if games is not None and isinstance(games, list):
            a["games"] = [str(g)[:60] for g in games][:20]
        if status is not None:
            if status not in _VALID_STATUS:
                raise ValueError("bad status")
            a["status"] = status
        a["updated"] = _now()
        save_manifest(m)
        return _view(a)


def remove_animation(aid: str) -> Dict[str, Any]:
    """Drop just the animation (rig/clip), keeping the source still. The preview
    falls back to the static image, so it becomes visible again. Status is reset
    to draft since a 'ready' asset requires its animation."""
    with _LOCK:
        m = load_manifest()
        a = m.get(aid)
        if a is None:
            raise KeyError(aid)
        rel = a.get("animation")
        if rel:
            try:
                (config.STUDIO_DIR / rel).unlink(missing_ok=True)
            except OSError:
                pass
        a["animation"] = None
        a["animationType"] = None
        if a.get("status") == "ready":
            a["status"] = "draft"
        a["updated"] = _now()
        save_manifest(m)
        return _view(a)


def delete(aid: str) -> bool:
    with _LOCK:
        m = load_manifest()
        a = m.pop(aid, None)
        if a is None:
            return False
        for key in ("source", "animation"):
            rel = a.get(key)
            if rel:
                try:
                    (config.STUDIO_DIR / rel).unlink(missing_ok=True)
                except OSError:
                    pass
        save_manifest(m)
        return True


# --------------------------------------------------------------------------- #
# CPU procedural animator (gentle breathe + bob; alpha-preserving WEBP)
# --------------------------------------------------------------------------- #
def _procedural_motion(src_path: Path, out_path: Path, frames: int = 24) -> None:
    from PIL import Image

    base = Image.open(src_path).convert("RGBA")
    w, h = base.size
    pad = max(4, h // 22)                      # room to bob without clipping
    cw, ch = w, h + 2 * pad
    imgs = []
    for i in range(frames):
        t = i / frames
        bob = int(round(pad * math.sin(2 * math.pi * t)))
        scale = 1.0 + 0.02 * math.sin(2 * math.pi * t)     # breathe
        sw, sh = max(1, int(w * scale)), max(1, int(h * scale))
        frame_img = base.resize((sw, sh), Image.LANCZOS)
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        x = (cw - sw) // 2
        y = pad + bob + (h - sh) // 2
        canvas.alpha_composite(frame_img, (x, y))
        imgs.append(canvas)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    imgs[0].save(out_path, format="WEBP", save_all=True, append_images=imgs[1:],
                 duration=80, loop=0, disposal=2, allow_mixed=True)
