#!/usr/bin/env bash
# =============================================================================
# install.sh - FREE one-click installer for the Home Hub  (Type A: BYO-LLM).
#
#   Type A = "UI / integration only, Bring-Your-Own-LLM".
#   It installs and runs:
#     * the Home Hub      (FastAPI UI + family portal)        -> :HUB_PORT
#     * the qwen-stack    (lightweight auth/proxy "gateway")  -> :8080
#   It does NOT download Ollama and does NOT download any model weights.
#   After install, the user connects their OWN LLM (their local Ollama, or a
#   future cloud key) by editing one file. See the "CONNECT YOUR LLM" section
#   printed at the end.
#
# Targets macOS (Darwin) + Linux. Rootless, idempotent.
#
# Usage:
#   ./install.sh
#
# Environment overrides:
#   HOMEHUB_DIR   install root           (default: $HOME/.local/share/homehub)
#   HUB_PORT      hub UI port            (default: 8090)
#   GATEWAY_PORT  gateway/auth-proxy port(default: 8080)
#   HUB_NAME      friendly hub name      (default: "Home Hub")
#   SRC           local source dir to copy FROM instead of downloading a release
#                 tarball. Must contain home-hub/ and qwen-stack/ subdirs, OR be
#                 a single component dir (auto-detected). Used for testing:
#                     SRC=/path/to/LLMs ./install.sh
#                 If SRC points directly at a home-hub checkout, its sibling
#                 qwen-stack is auto-discovered.
#   PYTHON_BIN    python interpreter     (default: python3)
#   NO_AUTOSTART  =1 to skip installing the systemd/launchd autostart unit.
#   NO_BROWSER    =1 to skip opening the browser.
#   RELEASE_URL   override the release tarball URL (when SRC is not given).
# =============================================================================
set -euo pipefail

# --------------------------------------------------------------------------- #
# Config / defaults
# --------------------------------------------------------------------------- #
INSTALL_DIR="${HOMEHUB_DIR:-$HOME/.local/share/homehub}"
HUB_PORT="${HUB_PORT:-8090}"
GATEWAY_PORT="${GATEWAY_PORT:-8080}"
HUB_NAME="${HUB_NAME:-Home Hub}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NO_AUTOSTART="${NO_AUTOSTART:-0}"
NO_BROWSER="${NO_BROWSER:-0}"

# Placeholder release tarball URL. This MUST be replaced at release time with
# the real artifact URL (see RELEASE.md), or overridden via the RELEASE_URL env
# var. The installer refuses to download from this placeholder (see step 2) so a
# mis-shipped build fails loudly with guidance instead of a confusing 404.
# SRC=... (a local source dir) takes precedence over this and is what tests use.
RELEASE_URL_PLACEHOLDER="https://downloads.example.com/homehub/homehub-latest.tar.gz"
RELEASE_URL="${RELEASE_URL:-$RELEASE_URL_PLACEHOLDER}"

HUB_DIR="${INSTALL_DIR}/home-hub"
GW_DIR="${INSTALL_DIR}/qwen-stack"
LOG_DIR="${INSTALL_DIR}/logs"
LAUNCHER_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/homehub-launch"
LAUNCHER_DST="${INSTALL_DIR}/homehub-launch"

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------- #
# 0. OS / arch detection
# --------------------------------------------------------------------------- #
UNAME_S="$(uname -s)"
UNAME_M="$(uname -m)"
case "${UNAME_S}" in
    Darwin) OS="macos" ;;
    Linux)  OS="linux" ;;
    *) die "unsupported OS '${UNAME_S}'. This installer supports macOS and Linux." ;;
esac
case "${UNAME_M}" in
    x86_64|amd64) ARCH="x86_64" ;;
    arm64|aarch64) ARCH="arm64" ;;
    *) ARCH="${UNAME_M}" ;;  # informational only; pure-python deps, no arch lock
esac
say "Home Hub FREE installer (Type A: UI/integration only, BYO-LLM)"
say "detected: OS=${OS} ARCH=${ARCH}  install_dir=${INSTALL_DIR}"

# --------------------------------------------------------------------------- #
# 1. Require python3 >= 3.10 with a clear, actionable error.
# --------------------------------------------------------------------------- #
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    cat >&2 <<EOF
ERROR: '${PYTHON_BIN}' was not found on your PATH.

Home Hub needs Python 3.10 or newer. Install it, then re-run this installer:

  macOS:   brew install python@3.12
           (or download from https://www.python.org/downloads/macos/)
  Debian/Ubuntu:
           sudo apt-get update && sudo apt-get install -y python3 python3-venv
  Fedora:  sudo dnf install -y python3
  Arch:    sudo pacman -S python

If python is installed under a different name, re-run with PYTHON_BIN set:
  PYTHON_BIN=python3.12 ./install.sh
EOF
    exit 1
fi
PYV="$("${PYTHON_BIN}" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
if ! "${PYTHON_BIN}" -c 'import sys;exit(0 if sys.version_info>=(3,10) else 1)'; then
    die "Python 3.10+ required, found ${PYV} (interpreter: $(command -v "${PYTHON_BIN}")).
Install a newer Python and re-run (optionally: PYTHON_BIN=python3.12 ./install.sh)."
fi
say "python OK: ${PYV} ($(command -v "${PYTHON_BIN}"))"

# --------------------------------------------------------------------------- #
# 2. Obtain the app source -> stage into INSTALL_DIR/{home-hub,qwen-stack}
#    SRC (local dir) is preferred (and used by tests); otherwise download the
#    release tarball.
# --------------------------------------------------------------------------- #
mkdir -p "${INSTALL_DIR}" "${LOG_DIR}"

# Copy a source tree EXCLUDING heavy/host-specific dirs (.venv, data, logs,
# __pycache__, .env). We re-create the venv + a fresh FREE-mode .env locally.
copy_component() {  # copy_component SRC_DIR DST_DIR
    local src="$1" dst="$2"
    [ -d "$src" ] || die "source component not found: $src"
    mkdir -p "$dst"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete \
            --exclude '.venv' --exclude 'data' --exclude 'logs' \
            --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' \
            "$src"/ "$dst"/
    else
        # tar fallback (portable; honors excludes via --exclude).
        ( cd "$src" && tar \
            --exclude='./.venv' --exclude='./data' --exclude='./logs' \
            --exclude='*/__pycache__' --exclude='*.pyc' --exclude='./.env' \
            -cf - . ) | ( cd "$dst" && tar -xf - )
    fi
}

resolve_src() {
    # Echoes "HUB_SRC|GW_SRC" given the SRC env, auto-detecting layout.
    local s="$1" hub gw
    if [ -d "$s/home-hub" ] && [ -d "$s/qwen-stack" ]; then
        hub="$s/home-hub"; gw="$s/qwen-stack"
    elif [ -f "$s/requirements.txt" ] && [ -d "$s/app" ] && [ -d "$(dirname "$s")/qwen-stack" ]; then
        # SRC points directly at a home-hub checkout; find sibling qwen-stack.
        hub="$s"; gw="$(dirname "$s")/qwen-stack"
    else
        die "SRC='$s' does not look like a Home Hub source tree.
Expected either:
  - a dir containing both 'home-hub/' and 'qwen-stack/' subdirs, or
  - a 'home-hub' checkout whose sibling 'qwen-stack' exists."
    fi
    printf '%s|%s\n' "$hub" "$gw"
}

if [ -n "${SRC:-}" ]; then
    say "using local SRC: ${SRC}"
    IFS='|' read -r HUB_SRC GW_SRC <<EOF
$(resolve_src "${SRC%/}")
EOF
    say "staging hub source     <- ${HUB_SRC}"
    copy_component "${HUB_SRC}" "${HUB_DIR}"
    say "staging gateway source <- ${GW_SRC}"
    copy_component "${GW_SRC}" "${GW_DIR}"
else
    # Fail loudly (not with a confusing 404) if this build still carries the
    # documented placeholder URL and the user did not override RELEASE_URL.
    if [ "${RELEASE_URL}" = "${RELEASE_URL_PLACEHOLDER}" ]; then
        die "this installer was shipped without a real release URL configured.

RELEASE_URL is still the placeholder:
  ${RELEASE_URL_PLACEHOLDER}

Do one of the following:
  - Install from a local source dir (no download needed):
      SRC=/path/to/LLMs ./install.sh
  - Point at the real release tarball explicitly:
      RELEASE_URL=https://.../homehub-latest.tar.gz ./install.sh

(Maintainers: set the real RELEASE_URL at release time; see RELEASE.md.)"
    fi
    say "downloading release tarball: ${RELEASE_URL}"
    command -v curl >/dev/null 2>&1 || die "curl is required to download the release."
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    if ! curl -fSL "${RELEASE_URL}" -o "${tmp}/homehub.tgz"; then
        die "failed to download ${RELEASE_URL}.
For a local/offline install, point SRC at a source dir instead, e.g.:
  SRC=/path/to/LLMs ./install.sh"
    fi
    tar -xzf "${tmp}/homehub.tgz" -C "${tmp}"
    # The tarball is expected to extract a top dir containing home-hub/ + qwen-stack/.
    top="$(find "${tmp}" -maxdepth 2 -type d -name home-hub | head -n1)"
    [ -n "${top}" ] || die "release tarball did not contain a home-hub/ directory."
    base="$(dirname "${top}")"
    copy_component "${base}/home-hub"   "${HUB_DIR}"
    copy_component "${base}/qwen-stack" "${GW_DIR}"
fi

# Install the runtime launcher.
if [ -f "${LAUNCHER_SRC}" ]; then
    install -m 0755 "${LAUNCHER_SRC}" "${LAUNCHER_DST}"
else
    warn "launcher not found next to install.sh (${LAUNCHER_SRC}); services may fail."
fi

# --------------------------------------------------------------------------- #
# 3. Per-component venv + pip bootstrap + deps.
#    Reuses the Debian/Ubuntu pip-less-venv fix from home-hub/install.sh:
#    create with --without-pip, then ensurepip || curl get-pip.py.
# --------------------------------------------------------------------------- #
setup_venv() {  # setup_venv COMPONENT_DIR
    local dir="$1"
    local venv="${dir}/.venv"
    local req="${dir}/requirements.txt"
    [ -f "$req" ] || die "missing requirements.txt in ${dir}"

    if [ -x "${venv}/bin/python" ]; then
        say "  reusing venv: ${venv}"
    else
        say "  creating venv: ${venv}"
        # --without-pip so creation never fails on Debian/Ubuntu where the
        # ensurepip wheels live in the separate python3-venv package.
        "${PYTHON_BIN}" -m venv --without-pip "${venv}"
    fi

    local vpy="${venv}/bin/python"
    if ! "${vpy}" -m pip --version >/dev/null 2>&1; then
        say "  bootstrapping pip"
        if ! "${vpy}" -m ensurepip --upgrade >/dev/null 2>&1; then
            say "  ensurepip unavailable; fetching get-pip.py"
            command -v curl >/dev/null 2>&1 || die "curl needed to bootstrap pip (or install python3-venv)."
            curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "${dir}/.get-pip.py"
            "${vpy}" "${dir}/.get-pip.py"
            rm -f "${dir}/.get-pip.py"
        fi
    fi

    say "  installing requirements (${req})"
    "${vpy}" -m pip install --upgrade pip >/dev/null
    "${vpy}" -m pip install -r "${req}"
}

say "setting up gateway (auth/proxy) venv"
setup_venv "${GW_DIR}"
say "setting up hub (UI) venv"
setup_venv "${HUB_DIR}"

HUB_PY="${HUB_DIR}/.venv/bin/python"
GW_PY="${GW_DIR}/.venv/bin/python"

# --------------------------------------------------------------------------- #
# 4. Data dirs + SQLite schemas (idempotent).
#    The hub schema is initialized by home-hub/install.sh; the gateway schema by
#    qwen-stack/install.sh. We invoke each project's own installer in a
#    NON-interactive way is overkill; instead we init dirs and let the schemas be
#    created on first run AND mint the gateway key (which creates the gw schema).
#    To be safe we create the data dirs here.
# --------------------------------------------------------------------------- #
mkdir -p "${HUB_DIR}/data/uploads" "${HUB_DIR}/logs" "${GW_DIR}/data" "${GW_DIR}/logs"

# --------------------------------------------------------------------------- #
# 5. Generate secrets + write FREE-mode .env files for BOTH components.
#    Idempotent: if a .env already exists we KEEP its secrets (re-running the
#    installer must not rotate tokens out from under a running hub).
# --------------------------------------------------------------------------- #
gen_token() {  # gen_token PREFIX
    "${PYTHON_BIN}" -c "import secrets;print('${1}'+secrets.token_hex(20))"
}

# Detect LAN IP for friendly URLs (best-effort).
detect_lan_ip() {
    if [ "${OS}" = "linux" ]; then
        ip route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}' | head -n1
    else
        # macOS
        ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true
    fi
}
LAN_IP="$(detect_lan_ip || true)"
LAN_IP="${LAN_IP:-127.0.0.1}"

# ---- Gateway .env -----------------------------------------------------------
GW_ENV="${GW_DIR}/.env"
if [ -f "${GW_ENV}" ]; then
    say "gateway .env exists; reusing existing ADMIN_TOKEN"
    ADMIN_TOKEN="$(grep -E '^ADMIN_TOKEN=' "${GW_ENV}" | tail -n1 | cut -d= -f2- || true)"
fi
if [ -z "${ADMIN_TOKEN:-}" ] || [ "${ADMIN_TOKEN}" = "REPLACE_ME_WITH_GENERATED_TOKEN" ]; then
    ADMIN_TOKEN="$(gen_token qwadm-)"
fi
say "writing gateway .env (FREE mode)"
umask 077
cat > "${GW_ENV}" <<EOF
# Auto-generated by the Home Hub FREE installer (Type A: BYO-LLM).
# Gateway = the lightweight auth/proxy in front of your (future) LLM.
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=${GATEWAY_PORT}

# BYO-LLM: this points at a LOCAL Ollama you run yourself. Nothing is installed
# or downloaded by this installer. Start your own Ollama (or change this URL to
# a cloud-backed OpenAI-compatible endpoint) to bring the hub to life.
OLLAMA_BASE_URL=http://127.0.0.1:11434

DB_PATH=${GW_DIR}/data/gateway.db
ADMIN_TOKEN=${ADMIN_TOKEN}
DEFAULT_RPM=60
EOF
chmod 600 "${GW_ENV}"

# ---- Mint the hub's gateway API key (qwsk-...) via the gateway's own CLI -----
# adminctl.py operates directly on the gateway SQLite (no running server needed)
# and creates the schema on demand. We re-use an existing key on re-install.
HUB_ENV="${HUB_DIR}/.env"
HUB_GATEWAY_KEY=""
if [ -f "${HUB_ENV}" ]; then
    EXISTING_GW_KEY="$(grep -E '^HUB_GATEWAY_KEY=' "${HUB_ENV}" | tail -n1 | cut -d= -f2- || true)"
    case "${EXISTING_GW_KEY}" in
        qwsk-*) HUB_GATEWAY_KEY="${EXISTING_GW_KEY}"; say "reusing existing HUB_GATEWAY_KEY" ;;
    esac
fi
if [ -z "${HUB_GATEWAY_KEY}" ]; then
    say "minting a gateway API key for the hub (adminctl.py create)"
    MINT_OUT="$( cd "${GW_DIR}" && DB_PATH="${GW_DIR}/data/gateway.db" \
        "${GW_PY}" adminctl.py create --name "home-hub" 2>/dev/null || true )"
    HUB_GATEWAY_KEY="$(printf '%s\n' "${MINT_OUT}" | grep -oE 'qwsk-[0-9a-f]{40}' | head -n1 || true)"
    [ -n "${HUB_GATEWAY_KEY}" ] || warn "could not mint a gateway key automatically; \
the hub will run but chat stays disabled until you set HUB_GATEWAY_KEY in ${HUB_ENV}."
fi

# ---- Hub .env ---------------------------------------------------------------
# Reuse an existing HUB_BOOTSTRAP_TOKEN if present (idempotent).
HUB_BOOTSTRAP_TOKEN=""
if [ -f "${HUB_ENV}" ]; then
    HUB_BOOTSTRAP_TOKEN="$(grep -E '^HUB_BOOTSTRAP_TOKEN=' "${HUB_ENV}" | tail -n1 | cut -d= -f2- || true)"
fi
[ -n "${HUB_BOOTSTRAP_TOKEN}" ] || HUB_BOOTSTRAP_TOKEN="$(gen_token hubboot-)"

# Reuse an existing HUB_SETUP_CODE if present (idempotent); else a fresh 6-digit
# code the first-run browser screen uses to create the first admin.
HUB_SETUP_CODE=""
if [ -f "${HUB_ENV}" ]; then
    HUB_SETUP_CODE="$(grep -E '^HUB_SETUP_CODE=' "${HUB_ENV}" | tail -n1 | cut -d= -f2- || true)"
fi
[ -n "${HUB_SETUP_CODE}" ] || HUB_SETUP_CODE="$("${PYTHON_BIN}" -c "import secrets;print(secrets.randbelow(900000)+100000)")"

say "writing hub .env (FREE mode)"
cat > "${HUB_ENV}" <<EOF
# Auto-generated by the Home Hub FREE installer (Type A: BYO-LLM).
# This is the UI/family portal. It talks to the gateway (auth/proxy) above and,
# through it, to YOUR LLM. No model is bundled.

HUB_NAME=${HUB_NAME}
HUB_HOST=0.0.0.0
HUB_PORT=${HUB_PORT}

# The gateway this hub proxies chat through (installed alongside, localhost).
GATEWAY_URL=http://127.0.0.1:${GATEWAY_PORT}
# Key the hub uses to call the gateway (minted above).
HUB_GATEWAY_KEY=${HUB_GATEWAY_KEY}
# Gateway admin token: lets the hub mint per-user keys AND claim the 1st admin.
HUB_ADMIN_TOKEN=${ADMIN_TOKEN}
# Separate bootstrap secret to claim the first admin device in the browser
# without typing the gateway admin token. Keep it private.
HUB_BOOTSTRAP_TOKEN=${HUB_BOOTSTRAP_TOKEN}
# First-run setup code (printed at the end of install). On a fresh hub with no
# admin yet, the browser "Welcome" screen accepts this to create the first
# admin — no token typing. Ignored once an admin exists.
HUB_SETUP_CODE=${HUB_SETUP_CODE}

# BYO-LLM: your local Ollama (used for embeddings/vision at index time). Nothing
# is downloaded by this installer; point this at your own Ollama when ready.
OLLAMA_URL=http://127.0.0.1:11434
EMBED_MODEL=nomic-embed-text
VISION_MODEL=moondream

DB_PATH=${HUB_DIR}/data/hub.db
LAN_IP=${LAN_IP}
EOF
chmod 600 "${HUB_ENV}"

# Initialize the hub SQLite schema now (so first request is fast). The hub
# creates tables itself on startup, but we pre-create the data dir + DB by
# importing the app's db module if available; tolerate failure.
( cd "${HUB_DIR}" && DB_PATH="${HUB_DIR}/data/hub.db" "${HUB_PY}" - <<'PYEOF' >/dev/null 2>&1 || true
import os, sqlite3, pathlib
p = os.environ["DB_PATH"]; pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
sqlite3.connect(p).close()
PYEOF
) || true

# --------------------------------------------------------------------------- #
# 6. Autostart unit (rootless) + start now.
# --------------------------------------------------------------------------- #
install_autostart_linux() {
    local unit_dir="${HOME}/.config/systemd/user"
    local unit="${unit_dir}/homehub.service"
    mkdir -p "${unit_dir}"
    say "installing systemd --user unit: ${unit}"
    cat > "${unit}" <<EOF
[Unit]
Description=Home Hub (FREE / BYO-LLM) - UI + auth/proxy gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HOMEHUB_DIR=${INSTALL_DIR}
# Launcher starts the gateway in the background, then execs the hub (foreground)
# so systemd tracks the hub as the unit's main process.
ExecStart=${LAUNCHER_DST} --foreground
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

    if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
        systemctl --user daemon-reload
        systemctl --user enable --now homehub.service || warn "could not enable/start the user unit."
        cat <<EOF
    NOTE: so the hub keeps running after you log out / on boot, enable lingering:
        sudo loginctl enable-linger ${USER}
EOF
    else
        warn "systemd --user not available in this session; starting via launcher instead."
        START_VIA_LAUNCHER=1
    fi
}

install_autostart_macos() {
    local agents="${HOME}/Library/LaunchAgents"
    local plist="${agents}/com.homehub.plist"
    mkdir -p "${agents}"
    say "installing launchd LaunchAgent: ${plist}"
    cat > "${plist}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.homehub</string>
  <key>ProgramArguments</key>
  <array>
    <string>${LAUNCHER_DST}</string>
    <string>--foreground</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>HOMEHUB_DIR</key><string>${INSTALL_DIR}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${LOG_DIR}/launchd.out.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/launchd.err.log</string>
</dict>
</plist>
EOF
    # bootout any previous instance, then bootstrap (idempotent).
    launchctl bootout "gui/$(id -u)/com.homehub" >/dev/null 2>&1 || true
    if launchctl bootstrap "gui/$(id -u)" "${plist}" >/dev/null 2>&1; then
        launchctl enable "gui/$(id -u)/com.homehub" >/dev/null 2>&1 || true
    else
        # Fallback for older macOS.
        launchctl load -w "${plist}" >/dev/null 2>&1 || { warn "launchctl load failed; starting via launcher."; START_VIA_LAUNCHER=1; }
    fi
}

START_VIA_LAUNCHER=0
if [ "${NO_AUTOSTART}" = "1" ]; then
    say "NO_AUTOSTART=1: skipping autostart unit; starting once via launcher"
    START_VIA_LAUNCHER=1
elif [ "${OS}" = "linux" ]; then
    install_autostart_linux
else
    install_autostart_macos
fi

# If we couldn't (or were asked not to) use the service manager, start directly.
if [ "${START_VIA_LAUNCHER}" = "1" ]; then
    HOMEHUB_DIR="${INSTALL_DIR}" "${LAUNCHER_DST}" || warn "launcher reported a problem; check ${LOG_DIR}."
fi

# --------------------------------------------------------------------------- #
# 7. Open the browser (unless suppressed).
# --------------------------------------------------------------------------- #
HUB_URL="http://localhost:${HUB_PORT}"
if [ "${NO_BROWSER}" != "1" ]; then
    say "opening ${HUB_URL}"
    (
        for _ in $(seq 1 30); do
            curl -sf -m1 "http://127.0.0.1:${HUB_PORT}/" >/dev/null 2>&1 && break
            sleep 0.5
        done
        if   command -v xdg-open >/dev/null 2>&1; then xdg-open "${HUB_URL}" >/dev/null 2>&1 || true
        elif command -v open     >/dev/null 2>&1; then open "${HUB_URL}"     >/dev/null 2>&1 || true
        fi
    ) >/dev/null 2>&1 &
fi

# --------------------------------------------------------------------------- #
# 8. Next steps.
# --------------------------------------------------------------------------- #
cat <<EOF

============================================================================
 HOME HUB INSTALLED  (FREE / Type A: UI + integration only, BYO-LLM)
============================================================================

 Hub UI         : ${HUB_URL}
 On your LAN    : http://${LAN_IP}:${HUB_PORT}
 Gateway (proxy): http://127.0.0.1:${GATEWAY_PORT}
 Install dir    : ${INSTALL_DIR}
 Logs           : ${LOG_DIR}/hub.log , ${LOG_DIR}/gateway.log

 First admin device — no token typing needed:
   1) On any device on your WiFi, open   http://${LAN_IP}:${HUB_PORT}
   2) On the "Welcome" setup screen, pick a name and enter this setup code:

            ${HUB_SETUP_CODE}

   Then set an admin PIN in Settings so anyone can join from any device.
   (Advanced) The admin token also still works: ${HUB_BOOTSTRAP_TOKEN}

 >>> CONNECT YOUR LLM (this FREE build downloads NO model) <<<
   The hub + gateway are running, but they need an LLM to chat with. Choose one:

   A) Use your own local Ollama (recommended for the FREE build):
        1. Install Ollama yourself:  https://ollama.com/download
        2. Pull a model, e.g.:       ollama pull qwen2.5:7b-instruct-q4_K_M
        3. (optional, for files/photos search):
             ollama pull nomic-embed-text
             ollama pull moondream
        4. Ollama listens on http://127.0.0.1:11434 by default -- already wired.
           Restart the hub to pick it up:  see "Manage" below.

   B) Point at a cloud / remote OpenAI-compatible endpoint instead:
        Edit ${GW_DIR}/.env -> OLLAMA_BASE_URL=<your endpoint>
        (and add that provider's auth if it needs one), then restart.

 Manage the service:
EOF
if [ "${OS}" = "linux" ] && [ "${START_VIA_LAUNCHER}" != "1" ]; then
cat <<EOF
   Status : systemctl --user status homehub.service
   Restart: systemctl --user restart homehub.service
   Logs   : journalctl --user -u homehub.service -f
   Keep running after logout: sudo loginctl enable-linger ${USER}
EOF
elif [ "${OS}" = "macos" ]; then
cat <<EOF
   Status : launchctl print gui/$(id -u)/com.homehub
   Restart: launchctl kickstart -k gui/$(id -u)/com.homehub
   Logs   : tail -f ${LOG_DIR}/hub.log
EOF
else
cat <<EOF
   Start  : HOMEHUB_DIR=${INSTALL_DIR} ${LAUNCHER_DST}
   Logs   : tail -f ${LOG_DIR}/hub.log
EOF
fi
cat <<EOF

 Uninstall: ./uninstall.sh    (stops the service and removes ${INSTALL_DIR})
============================================================================
EOF
