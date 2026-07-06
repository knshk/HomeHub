#!/usr/bin/env bash
# =============================================================================
# install.sh - one-shot, idempotent installer for the qwen-stack gateway.
#
# Creates a Python virtualenv, installs dependencies, prepares the data dir,
# and initializes the SQLite schema. Safe to re-run: existing venv, deps, and
# schema are left intact (CREATE TABLE IF NOT EXISTS).
#
# Usage:
#     ./install.sh
# =============================================================================
set -euo pipefail

# Resolve the project root from this script's location so it works from any cwd.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
DATA_DIR="${PROJECT_ROOT}/data"
REQUIREMENTS="${PROJECT_ROOT}/requirements.txt"
ENV_FILE="${PROJECT_ROOT}/.env"
ENV_EXAMPLE="${PROJECT_ROOT}/.env.example"

# Pick a Python interpreter (3.10+ required).
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> qwen-stack installer"
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
# 1. requirements.txt - create from the contract's minimal deps if missing.
# ---------------------------------------------------------------------------
if [[ ! -f "${REQUIREMENTS}" ]]; then
    echo "==> requirements.txt not found; writing minimal default"
    cat > "${REQUIREMENTS}" <<'REQEOF'
fastapi
uvicorn[standard]
httpx
python-dotenv
REQEOF
fi

# ---------------------------------------------------------------------------
# 2. Virtualenv (idempotent: reuse if already present).
# ---------------------------------------------------------------------------
if [[ -x "${VENV_DIR}/bin/python" ]]; then
    echo "==> reusing existing venv at ${VENV_DIR}"
else
    echo "==> creating venv at ${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# ---------------------------------------------------------------------------
# 3. Dependencies.
# ---------------------------------------------------------------------------
echo "==> upgrading pip and installing dependencies"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip >/dev/null
"${VENV_DIR}/bin/python" -m pip install -r "${REQUIREMENTS}"

# ---------------------------------------------------------------------------
# 4. Data directory.
# ---------------------------------------------------------------------------
echo "==> ensuring data directory exists"
mkdir -p "${DATA_DIR}"

# ---------------------------------------------------------------------------
# 5. Initialize SQLite schema (idempotent).
#    DB_PATH is sourced from .env if present, else falls back to the default.
# ---------------------------------------------------------------------------
DB_PATH="${DB_PATH:-${DATA_DIR}/gateway.db}"
if [[ -f "${ENV_FILE}" ]]; then
    # Pull DB_PATH out of .env if the operator set a custom location.
    ENV_DB_PATH="$(grep -E '^DB_PATH=' "${ENV_FILE}" | tail -n1 | cut -d= -f2- || true)"
    if [[ -n "${ENV_DB_PATH}" ]]; then
        DB_PATH="${ENV_DB_PATH}"
    fi
fi

echo "==> initializing SQLite schema at ${DB_PATH}"
DB_PATH="${DB_PATH}" "${VENV_DIR}/bin/python" -c '
import os, sqlite3, pathlib

db_path = os.environ["DB_PATH"]
pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(db_path)
try:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            name               TEXT    NOT NULL,
            key_prefix         TEXT    NOT NULL,
            key_hash           TEXT    NOT NULL UNIQUE,
            created_at         TEXT    NOT NULL,
            revoked            INTEGER NOT NULL DEFAULT 0,
            rpm_limit          INTEGER NOT NULL DEFAULT 60,
            daily_token_limit  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS usage_log (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id             INTEGER,
            ts                 TEXT,
            model              TEXT,
            prompt_tokens      INTEGER,
            completion_tokens  INTEGER,
            status             INTEGER
        );
        """
    )
    conn.commit()
finally:
    conn.close()
print("    schema OK (api_keys, usage_log)")
'

# ---------------------------------------------------------------------------
# 6. Helpful next steps.
# ---------------------------------------------------------------------------
# Best-effort detection of the host LAN IP for the printed base_url hint.
LAN_IP="$(ip -4 addr show scope global 2>/dev/null \
    | grep -oP 'inet \K[0-9.]+' | head -n1 || true)"
LAN_IP="${LAN_IP:-<this-host-LAN-ip>}"

cat <<EOF

============================================================================
 INSTALL COMPLETE
============================================================================

NEXT STEPS

 1. Create your local config (once), then edit the ADMIN_TOKEN:

        cp ${ENV_EXAMPLE} ${ENV_FILE}
        chmod 600 ${ENV_FILE}
        # generate a fresh admin token:
        ${VENV_DIR}/bin/python -c "import secrets; print('qwadm-' + secrets.token_hex(20))"
        # paste it as ADMIN_TOKEN= in ${ENV_FILE}

 2. Start the gateway (foreground, for a quick check):

        make run
        # or, explicitly:
        ${VENV_DIR}/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080

    For production, install the systemd unit in deploy/qwen-gateway.service.

 3. Create your first API key (gateway must be running; replace \$ADMIN_TOKEN):

        make key NAME="laptop"
        # or via curl:
        curl -s http://127.0.0.1:8080/admin/keys \\
             -H "Authorization: Bearer \$ADMIN_TOKEN" \\
             -H "Content-Type: application/json" \\
             -d '{"name":"laptop"}'

    The plaintext key (qwsk-...) is shown EXACTLY ONCE. Copy it now.

 4. Point your OpenAI-compatible client at the gateway from the LAN:

        base_url = http://${LAN_IP}:8080/v1
        api_key  = qwsk-...            (the key from step 3)
        model    = qwen2.5-7b

    Clients MUST hit the gateway on :8080 - never Ollama on :11434 directly.

 5. Lock down the host before exposing it (see deploy/firewall.md):
      - Bind Ollama to 127.0.0.1 (deploy/ollama-subnet.conf)
      - ufw allow 8080/11434 from the LAN /24 only; deny WAN

============================================================================
EOF
