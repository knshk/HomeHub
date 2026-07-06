#!/usr/bin/env bash
# =============================================================================
# start-all.sh - idempotent rootless start of the Home LLM Hub.
#
# Starts the FastAPI/uvicorn app in the background on HUB_HOST:HUB_PORT (read
# from .env, defaults 0.0.0.0:8090). Re-running while it is already up does
# nothing. Logs go to logs/hub.log. NO ROOT.
#
# Usage:  ./start-all.sh
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
LOG_DIR="${PROJECT_ROOT}/logs"
ENV_FILE="${PROJECT_ROOT}/.env"
LOG_FILE="${LOG_DIR}/hub.log"
PID_FILE="${LOG_DIR}/hub.pid"

mkdir -p "${LOG_DIR}"

if [[ ! -x "${VENV_DIR}/bin/uvicorn" ]]; then
    echo "ERROR: venv not found at ${VENV_DIR}. Run ./install.sh first." >&2
    exit 1
fi

# -- Load HUB_HOST / HUB_PORT from .env (without leaking other secrets) --------
HUB_HOST="0.0.0.0"
HUB_PORT="8090"
if [[ -f "${ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    set -a; source <(grep -E '^(HUB_HOST|HUB_PORT)=' "${ENV_FILE}" || true); set +a
fi
HUB_HOST="${HUB_HOST:-0.0.0.0}"
HUB_PORT="${HUB_PORT:-8090}"

# -- Idempotency: bracketed pattern avoids matching the grep/pgrep itself ------
# Pattern matches THIS project's uvicorn for app.main on the chosen port.
PATTERN="[u]vicorn .*app.main:app .*--port ${HUB_PORT}"

if pgrep -f "${PATTERN}" >/dev/null 2>&1; then
    EXISTING="$(pgrep -f "${PATTERN}" | tr '\n' ' ')"
    echo "==> Hub already running on :${HUB_PORT} (pid(s): ${EXISTING}). Nothing to do."
    exit 0
fi

echo "==> Starting Home LLM Hub on ${HUB_HOST}:${HUB_PORT}"
echo "    logs:  ${LOG_FILE}"

cd "${PROJECT_ROOT}"
# nohup + disown so it survives this shell; --env-file so uvicorn-launched app
# sees the full config. We still pass host/port explicitly to guarantee bind.
nohup "${VENV_DIR}/bin/uvicorn" app.main:app \
    --host "${HUB_HOST}" --port "${HUB_PORT}" \
    >>"${LOG_FILE}" 2>&1 &
HUB_PID=$!
disown "${HUB_PID}" 2>/dev/null || true
echo "${HUB_PID}" > "${PID_FILE}"

# Brief liveness check (no foreground sleep loops; just confirm the pid stuck).
if kill -0 "${HUB_PID}" 2>/dev/null; then
    echo "==> Started (pid ${HUB_PID})."
    echo "    Open: http://${HUB_HOST/0.0.0.0/<this-host-LAN-ip>}:${HUB_PORT}"
    echo "    Tail logs: tail -f ${LOG_FILE}"
else
    echo "ERROR: hub failed to start; see ${LOG_FILE}" >&2
    exit 1
fi
