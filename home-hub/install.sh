#!/usr/bin/env bash
# =============================================================================
# install.sh - rootless, idempotent installer for the Home LLM Hub (llm.home).
#
# Creates a Python virtualenv, installs dependencies, prepares data/upload/log
# dirs, and initializes the SQLite schema. Safe to re-run: the venv, deps, and
# schema are left intact (CREATE TABLE IF NOT EXISTS). NO ROOT REQUIRED.
#
# This installs the rootless hub that runs on HUB_PORT (default 8090). To serve
# at http://llm.home on :80 with LAN DNS, run the SEPARATE sudo installer
# afterwards:  sudo bash deploy/install-llmhome.sh
#
# Usage:
#     ./install.sh
# =============================================================================
set -euo pipefail

# Resolve the project root from this script's location so it works from any cwd.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
DATA_DIR="${PROJECT_ROOT}/data"
UPLOAD_DIR="${DATA_DIR}/uploads"
LOG_DIR="${PROJECT_ROOT}/logs"
REQUIREMENTS="${PROJECT_ROOT}/requirements.txt"
ENV_FILE="${PROJECT_ROOT}/.env"
ENV_EXAMPLE="${PROJECT_ROOT}/.env.example"

# Pick a Python interpreter (3.10+ required).
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> Home LLM Hub installer (rootless)"
echo "    project root: ${PROJECT_ROOT}"

# ---------------------------------------------------------------------------
# 0. Sanity: Python version >= 3.10
# ---------------------------------------------------------------------------
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "ERROR: '${PYTHON_BIN}' not found on PATH. Install Python 3.10+." >&2
    exit 1
fi
"${PYTHON_BIN}" - <<'PYEOF'
import sys
if sys.version_info < (3, 10):
    sys.exit("ERROR: Python 3.10+ required, found %d.%d" % sys.version_info[:2])
PYEOF

# ---------------------------------------------------------------------------
# 1. Directories (idempotent).
# ---------------------------------------------------------------------------
echo "==> Ensuring data / upload / log directories"
mkdir -p "${UPLOAD_DIR}" "${LOG_DIR}"

# ---------------------------------------------------------------------------
# 2. Virtualenv (idempotent: reuse if present).
# ---------------------------------------------------------------------------
if [[ -d "${VENV_DIR}" && -x "${VENV_DIR}/bin/python" ]]; then
    echo "==> Reusing existing virtualenv at ${VENV_DIR}"
else
    echo "==> Creating virtualenv at ${VENV_DIR}"
    # --without-pip so creation never fails on Debian/Ubuntu where the ensurepip
    # wheels are unavailable; pip is bootstrapped in the next step.
    "${PYTHON_BIN}" -m venv --without-pip "${VENV_DIR}"
fi

VENV_PY="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

# Debian/Ubuntu often create a pip-less venv (ensurepip wheels split into the
# python3-venv package). Bootstrap pip if it is missing.
if ! "${VENV_PY}" -m pip --version >/dev/null 2>&1; then
    echo "==> Bootstrapping pip into the venv"
    if ! "${VENV_PY}" -m ensurepip --upgrade >/dev/null 2>&1; then
        echo "    ensurepip unavailable; fetching get-pip.py"
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "${PROJECT_ROOT}/.get-pip.py"
        "${VENV_PY}" "${PROJECT_ROOT}/.get-pip.py"
        rm -f "${PROJECT_ROOT}/.get-pip.py"
    fi
fi

# ---------------------------------------------------------------------------
# 3. Dependencies.
# ---------------------------------------------------------------------------
if [[ ! -f "${REQUIREMENTS}" ]]; then
    echo "ERROR: ${REQUIREMENTS} not found." >&2
    exit 1
fi
echo "==> Upgrading pip and installing requirements"
"${VENV_PY}" -m pip install --upgrade pip >/dev/null
"${VENV_PIP}" install -r "${REQUIREMENTS}"

# ---------------------------------------------------------------------------
# 4. Local .env (create from example on first run; never overwrite).
# ---------------------------------------------------------------------------
if [[ -f "${ENV_FILE}" ]]; then
    echo "==> .env already present (left untouched)"
elif [[ -f "${ENV_EXAMPLE}" ]]; then
    echo "==> Creating .env from .env.example (edit the REPLACE_ME values!)"
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
else
    echo "WARN: no .env or .env.example found; you must create .env manually."
fi

# Resolve DB_PATH from .env if set, else use the contract default.
DB_PATH="$(
    "${VENV_PY}" - "${ENV_FILE}" <<'PYEOF'
import os, sys
default = "/home/kanishka/kk_works/LLMs/home-hub/data/hub.db"
path = sys.argv[1]
val = default
try:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("DB_PATH=") and not line.startswith("#"):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    val = v
except FileNotFoundError:
    pass
print(val)
PYEOF
)"
echo "==> Using DB_PATH=${DB_PATH}"
mkdir -p "$(dirname "${DB_PATH}")"

# ---------------------------------------------------------------------------
# 5. SQLite schema (idempotent). Mirrors the SHARED CONTRACT exactly.
# ---------------------------------------------------------------------------
echo "==> Initializing SQLite schema"
DB_PATH="${DB_PATH}" "${VENV_PY}" - <<'PYEOF'
import os, sqlite3

db_path = os.environ["DB_PATH"]
conn = sqlite3.connect(db_path)
try:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS devices (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            device_token_hash TEXT    NOT NULL UNIQUE,
            username          TEXT    NOT NULL,
            role              TEXT    NOT NULL DEFAULT 'guest',
            status            TEXT    NOT NULL DEFAULT 'pending',
            privileges_json   TEXT    NOT NULL DEFAULT '[]',
            created_at        TEXT    NOT NULL,
            last_seen         TEXT
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username  TEXT    NOT NULL,
            title           TEXT,
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id  INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role             TEXT    NOT NULL,
            content          TEXT    NOT NULL,
            created_at       TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username  TEXT    NOT NULL,
            title           TEXT,
            body            TEXT,
            color           TEXT,
            pinned          INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS checklists (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username  TEXT    NOT NULL,
            title           TEXT    NOT NULL,
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS checklist_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            checklist_id  INTEGER NOT NULL REFERENCES checklists(id) ON DELETE CASCADE,
            text          TEXT    NOT NULL,
            done          INTEGER NOT NULL DEFAULT 0,
            position      INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS files (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username  TEXT    NOT NULL,
            kind            TEXT    NOT NULL,          -- 'file' | 'photo'
            filename        TEXT    NOT NULL,
            stored_path     TEXT    NOT NULL,
            mime            TEXT,
            size            INTEGER,
            shared          INTEGER NOT NULL DEFAULT 0,
            caption         TEXT,
            indexed         INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS file_chunks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id      INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            chunk_index  INTEGER NOT NULL,
            text         TEXT    NOT NULL,
            embedding    BLOB                          -- float32 bytes
        );

        CREATE TABLE IF NOT EXISTS user_keys (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username  TEXT    NOT NULL,
            gateway_key_id  TEXT    NOT NULL,
            key_prefix      TEXT,
            name            TEXT,
            created_at      TEXT    NOT NULL,
            revoked         INTEGER NOT NULL DEFAULT 0
        );

        -- Helpful indexes for the per-user / per-parent access patterns.
        CREATE INDEX IF NOT EXISTS idx_conv_owner     ON conversations(owner_username);
        CREATE INDEX IF NOT EXISTS idx_msg_conv       ON messages(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_notes_owner    ON notes(owner_username);
        CREATE INDEX IF NOT EXISTS idx_check_owner    ON checklists(owner_username);
        CREATE INDEX IF NOT EXISTS idx_items_check    ON checklist_items(checklist_id);
        CREATE INDEX IF NOT EXISTS idx_files_owner    ON files(owner_username);
        CREATE INDEX IF NOT EXISTS idx_files_shared   ON files(shared);
        CREATE INDEX IF NOT EXISTS idx_chunks_file    ON file_chunks(file_id);
        CREATE INDEX IF NOT EXISTS idx_keys_owner     ON user_keys(owner_username);
        """
    )
    conn.commit()
finally:
    conn.close()
print("    schema OK (devices, conversations, messages, notes, checklists,")
print("              checklist_items, files, file_chunks, user_keys)")
PYEOF

# ---------------------------------------------------------------------------
# 6. Helpful next steps.
# ---------------------------------------------------------------------------
# Resolve the host LAN IP for the printed URL hint. Prefer LAN_IP from .env,
# else the route-based detection used by the product installer.
LAN_IP="$(
    "${VENV_PY}" - "${ENV_FILE}" <<'PYEOF'
import sys
path = sys.argv[1]
try:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("LAN_IP=") and not line.startswith("#"):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    print(v)
                    break
except FileNotFoundError:
    pass
PYEOF
)"
if [[ -z "${LAN_IP}" ]]; then
    LAN_IP="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' | head -n1 || true)"
fi
LAN_IP="${LAN_IP:-<this-host-LAN-ip>}"

# Read HUB_PORT from .env for the printed URL (fallback 8090).
HUB_PORT="$(
    "${VENV_PY}" - "${ENV_FILE}" <<'PYEOF'
import sys
path = sys.argv[1]
port = "8090"
try:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("HUB_PORT=") and not line.startswith("#"):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    port = v
except FileNotFoundError:
    pass
print(port)
PYEOF
)"

cat <<EOF

============================================================================
 INSTALL COMPLETE  (rootless)
============================================================================

NEXT STEPS

 1. Wire the two gateway secrets into ${ENV_FILE} (chmod 600):

      a) HUB_GATEWAY_KEY - a gateway API key the HUB uses for chat. Mint it
         once from the gateway:

           cd /home/kanishka/kk_works/LLMs/qwen-stack
           make key NAME="home-hub"          # prints qwsk-... ONCE; copy it

         Paste that qwsk-... value as HUB_GATEWAY_KEY in ${ENV_FILE}.

      b) HUB_ADMIN_TOKEN - copy the gateway's ADMIN_TOKEN verbatim:

           grep ADMIN_TOKEN /home/kanishka/kk_works/LLMs/qwen-stack/.env

         Paste the value as HUB_ADMIN_TOKEN in ${ENV_FILE}. The hub uses it
         to mint per-user keys AND to authorize the first admin device.

      c) (optional) HUB_BOOTSTRAP_TOKEN - a separate admin-claim secret so you
         do not have to type the gateway admin token into a browser:

           ${VENV_DIR}/bin/python -c "import secrets; print('hubboot-' + secrets.token_hex(20))"

 2. Make sure the embedding + vision models are pulled in Ollama (one time):

           ollama pull ${EMBED_MODEL:-nomic-embed-text}
           ollama pull ${VISION_MODEL:-moondream}

 3. Start the hub (rootless, on :${HUB_PORT}):

           ./start-all.sh            # background, logs to logs/hub.log
        # or, foreground:
           make run

    Open it from any family device on the LAN:

           http://${LAN_IP}:${HUB_PORT}

 4. Claim the first admin device from your phone/laptop browser:
      - Visit the URL above, enter a username (passwordless).
      - Use the admin-claim flow with HUB_BOOTSTRAP_TOKEN (or HUB_ADMIN_TOKEN).
      - Your device becomes role=admin/status=approved; approve others from the
        admin screen.

 5. (Optional) Serve at http://llm.home on :80 with LAN DNS. Run the SEPARATE
    product installer (needs sudo ONCE; runtime stays rootless):

           sudo bash deploy/install-llmhome.sh

============================================================================
EOF
