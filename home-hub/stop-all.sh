#!/usr/bin/env bash
# =============================================================================
# stop-all.sh - idempotent rootless stop of the Home LLM Hub.
#
# Terminates the uvicorn process(es) for THIS project. Safe to run when the hub
# is already stopped (exits 0, prints a note). NO ROOT.
#
# Usage:  ./stop-all.sh
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${PROJECT_ROOT}/logs"
ENV_FILE="${PROJECT_ROOT}/.env"
PID_FILE="${LOG_DIR}/hub.pid"

# Determine port for a precise match (fallback 8090).
HUB_PORT="8090"
if [[ -f "${ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    set -a; source <(grep -E '^HUB_PORT=' "${ENV_FILE}" || true); set +a
fi
HUB_PORT="${HUB_PORT:-8090}"

# Bracketed pattern so the pkill does NOT match itself or this script.
PATTERN="[u]vicorn .*app.main:app .*--port ${HUB_PORT}"

if ! pgrep -f "${PATTERN}" >/dev/null 2>&1; then
    echo "==> Hub not running (port ${HUB_PORT}). Nothing to stop."
    rm -f "${PID_FILE}"
    exit 0
fi

echo "==> Stopping Home LLM Hub (port ${HUB_PORT})"
# Graceful TERM first.
pkill -TERM -f "${PATTERN}" || true

# Give it a moment, then force-kill any stragglers. Bounded wait via a small
# poll loop (no fixed foreground sleeps of arbitrary length).
for _ in 1 2 3 4 5; do
    pgrep -f "${PATTERN}" >/dev/null 2>&1 || break
    sleep 0.3
done
if pgrep -f "${PATTERN}" >/dev/null 2>&1; then
    echo "    process still alive; sending KILL"
    pkill -KILL -f "${PATTERN}" || true
fi

rm -f "${PID_FILE}"
echo "==> Stopped."
