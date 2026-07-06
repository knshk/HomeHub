#!/usr/bin/env bash
# =============================================================================
# uninstall.sh - remove the FREE Home Hub (Type A) on macOS + Linux.
#
# Stops the autostart service, removes the systemd --user unit (Linux) or the
# launchd LaunchAgent (macOS), kills any stray uvicorn processes for the hub +
# gateway, and removes INSTALL_DIR (with confirmation).
#
# Usage:
#   ./uninstall.sh           # interactive (asks before deleting INSTALL_DIR)
#   ./uninstall.sh --yes     # non-interactive (no prompt)
#   HOMEHUB_DIR=... ./uninstall.sh
# =============================================================================
set -euo pipefail

INSTALL_DIR="${HOMEHUB_DIR:-$HOME/.local/share/homehub}"
ASSUME_YES=0
[ "${1:-}" = "--yes" ] && ASSUME_YES=1

say() { printf '==> %s\n' "$*"; }

UNAME_S="$(uname -s)"
case "${UNAME_S}" in
    Darwin) OS="macos" ;;
    Linux)  OS="linux" ;;
    *) echo "unsupported OS '${UNAME_S}'" >&2; exit 1 ;;
esac

# Read the ports from the staged .env files so we kill precisely (fallbacks ok).
read_env() { [ -f "$1" ] && grep -E "^$2=" "$1" 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
HUB_PORT="$(read_env "${INSTALL_DIR}/home-hub/.env" HUB_PORT)";      HUB_PORT="${HUB_PORT:-8090}"
GW_PORT="$(read_env  "${INSTALL_DIR}/qwen-stack/.env" GATEWAY_PORT)"; GW_PORT="${GW_PORT:-8080}"

# --- 1. Stop + remove the autostart unit ------------------------------------
if [ "${OS}" = "linux" ]; then
    UNIT="${HOME}/.config/systemd/user/homehub.service"
    if command -v systemctl >/dev/null 2>&1; then
        say "stopping + disabling systemd --user unit"
        systemctl --user stop homehub.service    >/dev/null 2>&1 || true
        systemctl --user disable homehub.service >/dev/null 2>&1 || true
    fi
    if [ -f "${UNIT}" ]; then
        say "removing ${UNIT}"
        rm -f "${UNIT}"
        systemctl --user daemon-reload >/dev/null 2>&1 || true
    fi
else
    PLIST="${HOME}/Library/LaunchAgents/com.homehub.plist"
    say "unloading launchd LaunchAgent"
    launchctl bootout "gui/$(id -u)/com.homehub" >/dev/null 2>&1 || \
        launchctl unload -w "${PLIST}" >/dev/null 2>&1 || true
    [ -f "${PLIST}" ] && { say "removing ${PLIST}"; rm -f "${PLIST}"; }
fi

# --- 2. Kill any stray processes (bracketed patterns avoid self-match) -------
say "stopping any stray hub/gateway processes"
pkill -TERM -f "[u]vicorn app.main:app .*--port ${HUB_PORT}" 2>/dev/null || true
pkill -TERM -f "[u]vicorn app.main:app .*--port ${GW_PORT}"  2>/dev/null || true
# brief grace, then force.
for _ in 1 2 3 4 5; do
    pgrep -f "[u]vicorn app.main:app .*--port (${HUB_PORT}|${GW_PORT})" >/dev/null 2>&1 || break
    sleep 0.3
done
pkill -KILL -f "[u]vicorn app.main:app .*--port ${HUB_PORT}" 2>/dev/null || true
pkill -KILL -f "[u]vicorn app.main:app .*--port ${GW_PORT}"  2>/dev/null || true

# --- 3. Remove the install dir (with confirmation) --------------------------
if [ -d "${INSTALL_DIR}" ]; then
    if [ "${ASSUME_YES}" != "1" ]; then
        printf 'Delete the install directory and ALL local data?\n  %s\nType "yes" to confirm: ' "${INSTALL_DIR}"
        read -r reply
        if [ "${reply}" != "yes" ]; then
            say "left ${INSTALL_DIR} in place. Service removed; nothing deleted."
            exit 0
        fi
    fi
    say "removing ${INSTALL_DIR}"
    rm -rf "${INSTALL_DIR}"
else
    say "install dir not found (${INSTALL_DIR}); nothing to remove."
fi

say "uninstall complete."
