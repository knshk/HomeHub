"""Configuration: env vars + sane defaults. Loads a .env if present."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load .env from the project root (one level above app/) if it exists.
    _root = Path(__file__).resolve().parent.parent
    _env = _root / ".env"
    if _env.exists():
        load_dotenv(_env)
    else:
        load_dotenv()  # also pick up a CWD .env if any
except Exception:
    # python-dotenv missing should not crash; env vars still work.
    pass


# --- Privacy: no phone-home. Mirror of privacy.env, applied in-process so the hub
# AND every service it spawns (FastSD, Ollama/gateway, voice, image workers) inherit
# it. This is EXTERNAL telemetry only — the hub's own local usage metrics stay.
# setdefault(): an explicit override (e.g. HF_HUB_OFFLINE=0 to add a model) wins.
for _pk, _pv in {
    "GRADIO_ANALYTICS_ENABLED": "False",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
    "DISABLE_TELEMETRY": "1",
}.items():
    os.environ.setdefault(_pk, _pv)


def _b(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# Project root: /home/kanishka/kk_works/LLMs/home-hub
ROOT = Path(__file__).resolve().parent.parent

# Single source of truth for the app version (mirrors FastAPI app version and
# the /healthz + /api/discovery payloads).
HUB_VERSION = "1.0.0"

# --- Network ---
HUB_HOST = os.getenv("HUB_HOST", "0.0.0.0")
HUB_PORT = int(os.getenv("HUB_PORT", "8090"))
LAN_IP = os.getenv("LAN_IP", "127.0.0.1")

# --- Identity (exposed by the public /api/discovery endpoint) ---
# Friendly name shown by the login gate's "Find your Home Hub" step.
HUB_NAME = os.getenv("HUB_NAME", "Home Hub")
# Origin the browser should navigate to once a hub is found. Defaults to the
# advertised LAN address; override with PUBLIC_BASE_URL when behind a name/proxy.
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL")
                   or f"http://{LAN_IP}:{HUB_PORT}").rstrip("/")

# --- Upstreams (hub is a CLIENT; never modify the gateway) ---
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://127.0.0.1:8080").rstrip("/")
HUB_GATEWAY_KEY = os.getenv("HUB_GATEWAY_KEY", "")
HUB_ADMIN_TOKEN = os.getenv("HUB_ADMIN_TOKEN", "")
HUB_BOOTSTRAP_TOKEN = os.getenv("HUB_BOOTSTRAP_TOKEN", "")
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5-7b")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
# Recommended commercially-licensed defaults (Apache-2.0).
VISION_MODEL = os.getenv("VISION_MODEL", "moondream")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

# --- Upstream: local voice service (STT + TTS) ---
# A SEPARATE local process the operator runs (faster-whisper + kokoro-onnx).
# The hub is a CLIENT only and proxies to it; keep on localhost.
VOICE_URL = os.getenv("VOICE_URL", "http://127.0.0.1:8100").rstrip("/")
VOICE_DEFAULT_VOICE = os.getenv("VOICE_DEFAULT_VOICE", "af_sarah")

# --- Upstream: local Image Studio (FastSD CPU / OpenVINO) ---
# A separate local process (gradio WebUI). The hub only checks reachability and
# links the browser to it; it does not proxy generation. LAN-facing so other
# devices on the WiFi can open it from the hub link.
IMAGES_URL = os.getenv("IMAGES_URL", "http://127.0.0.1:7860").rstrip("/")
PUBLIC_IMAGES_URL = (os.getenv("PUBLIC_IMAGES_URL")
                     or f"http://{LAN_IP}:7860").rstrip("/")

# Cap on uploaded audio bytes proxied to the voice service.
MAX_VOICE_BYTES = int(os.getenv("MAX_VOICE_BYTES", str(25 * 1024 * 1024)))  # 25 MB

# --- Storage ---
DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "hub.db"))
DATA_DIR = Path(DB_PATH).resolve().parent
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(DATA_DIR / "uploads")))

# --- Studio: art/animation asset pipeline (manifest-backed store the games read) ---
STUDIO_DIR = Path(os.getenv("STUDIO_DIR", str(DATA_DIR / "studio")))
# Where the Image Studio (FastSD CPU) writes generated images, so we can import.
FASTSD_RESULTS_DIR = os.getenv("FASTSD_RESULTS_DIR",
                               "/home/kanishka/kk_works/fastsdcpu/results")

# --- Service control (the hub can start/stop the local model services) ---
# On a 16 GB box the LLM stack and the Image Studio can't run together, so the
# hub offers start/stop with LLM<->Image exclusivity.
QWEN_STACK_DIR = os.getenv("QWEN_STACK_DIR", "/home/kanishka/kk_works/LLMs/qwen-stack")
VOICE_SVC_DIR = os.getenv("VOICE_SVC_DIR", "/home/kanishka/kk_works/LLMs/voice-svc")
FASTSD_DIR = os.getenv("FASTSD_DIR", "/home/kanishka/kk_works/fastsdcpu")
OLLAMA_BIN = os.getenv("OLLAMA_BIN", os.path.expanduser("~/.local/bin/ollama"))

# --- Smart Home (hybrid: LAN-local control + cloud-by-exception push) --------
# Feature flag for the Home tab / /api/home routes. On by default, but the
# feature is INERT until an admin connects a provider (no outbound calls until
# then). The provider token is kept in the encrypted secret store, not here.
SMARTHOME_ENABLED = _b(os.getenv("SMARTHOME_ENABLED"), True)
SMARTHOME_DEFAULT_PROVIDER = os.getenv("SMARTHOME_DEFAULT_PROVIDER", "home_assistant")
# Refuse a public provider URL by default so control stays on the home LAN and
# consistent with the egress lock. Flip only for a routed-VPN Home Assistant.
SMARTHOME_ALLOW_NON_LAN = _b(os.getenv("SMARTHOME_ALLOW_NON_LAN"), False)

# Frontend assets live under app/ (served by the backend). Allow override; if the
# package-local dir is empty, fall back to a project-root static/templates dir.
_APP_DIR = Path(__file__).resolve().parent


def _pick_dir(env_name: str, app_local: Path, root_fallback: Path) -> Path:
    override = os.getenv(env_name)
    if override:
        return Path(override)
    # Prefer whichever directory actually contains files.
    if app_local.exists() and any(app_local.iterdir()):
        return app_local
    if root_fallback.exists() and any(root_fallback.iterdir()):
        return root_fallback
    return app_local


STATIC_DIR = _pick_dir("STATIC_DIR", _APP_DIR / "static", ROOT / "static")
TEMPLATES_DIR = _pick_dir("TEMPLATES_DIR", _APP_DIR / "templates", ROOT / "templates")

# --- Cookies ---
DEVICE_COOKIE = "hub_device"
# Document: Secure cookies require TLS. On plain-HTTP LAN we keep Secure off.
COOKIE_SECURE = _b(os.getenv("COOKIE_SECURE"), False)
COOKIE_SAMESITE = "lax"
COOKIE_MAX_AGE = int(os.getenv("COOKIE_MAX_AGE", str(60 * 60 * 24 * 365)))  # 1 year

# --- CSRF ---
CSRF_HEADER = "x-hub-csrf"  # case-insensitive compare

# --- Indexing ---
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "900"))      # chars per chunk
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))  # 50 MB

# --- Privilege model ---
ALL_PRIVILEGES = [
    "chat", "notes", "checklists",
    "files_read", "files_write",
    "photos_read", "photos_write",
    "api_keys",
]
ROLE_DEFAULT_PRIVILEGES = {
    "guest": ["chat"],
    "member": [
        "chat", "notes", "checklists",
        "files_read", "files_write",
        "photos_read", "photos_write",
    ],
    "admin": list(ALL_PRIVILEGES),
}
VALID_ROLES = ("admin", "member", "guest")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    for sub in ("images", "rive", "anim"):
        (STUDIO_DIR / sub).mkdir(parents=True, exist_ok=True)
