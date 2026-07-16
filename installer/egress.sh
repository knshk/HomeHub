#!/usr/bin/env bash
# =============================================================================
# egress.sh - LAN-only egress lock for the appliance services (the 🟠
# "egress firewall allowlist" item: belt-and-suspenders on top of the
# app-level offline hardening).
#
# DESIGN -- why systemd IPAddressDeny/IPAddressAllow instead of nftables:
#   All appliance services run as the SAME uid (kanishka) as the user's own
#   desktop session, so an nftables/iptables uid-match rule cannot separate
#   "ollama phoning home" from "the user browsing the web". systemd's
#   IPAddress* directives attach an eBPF filter to each service's OWN cgroup,
#   locking exactly that process tree and nothing else -- no global firewall
#   state, removable per-service, and visible via `systemctl show`.
#
# What gets locked (drop-in: /etc/systemd/system/<svc>.d/90-egress-lock.conf):
#   ollama.service, voice-svc.service, home-hub.service
#   (+ home-hub-https.service when installed -- same app, must not become the
#    one unlocked path to the internet)
# qwen-gateway.service stays UNLOCKED on purpose: it is the sanctioned,
# disclosed egress path for future cloud providers.
#
# Subcommands:
#   sudo installer/egress.sh lock     install drop-ins + restart locked services
#   sudo installer/egress.sh unlock   remove drop-ins + restart. Needed
#                                     TEMPORARILY for Ollama model pulls and
#                                     hub HuggingFace image-model downloads
#                                     (registries live outside the LAN).
#                                     Re-lock right after: egress.sh lock
#   installer/egress.sh status        show per-service state (no sudo needed)
# =============================================================================
set -euo pipefail

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

DROPIN_NAME="90-egress-lock.conf"
# Allow loopback + RFC1918 home LAN; everything else (incl. WAN) is denied.
DROPIN_CONTENT="# Installed by installer/egress.sh -- LAN-only egress/ingress lock.
# Remove TEMPORARILY for model downloads with: sudo installer/egress.sh unlock
[Service]
IPAddressDeny=any
IPAddressAllow=localhost 192.168.0.0/16
"

# Services to lock. qwen-gateway.service is deliberately absent (see header).
SERVICES=(ollama.service voice-svc.service home-hub.service)
# The HTTPS twin runs the same hub code; include it once it is installed.
if [ -f /etc/systemd/system/home-hub-https.service ]; then
    SERVICES+=(home-hub-https.service)
fi

dropin_path() { printf '/etc/systemd/system/%s.d/%s' "$1" "$DROPIN_NAME"; }
unit_installed() { [ -f "/etc/systemd/system/$1" ]; }
require_root() { [ "$(id -u)" -eq 0 ] || die "'$1' needs root. Re-run as: sudo ${BASH_SOURCE[0]} $1"; }

# Restart a service so the (added/removed) eBPF filter takes effect -- but only
# if it is currently running; inactive units pick the change up on next start.
restart_if_active() {
    if systemctl is-active --quiet "$1"; then
        say "  ${1}: restarting to apply"
        systemctl restart "$1" || warn "  ${1}: restart failed -- check: systemctl status $1"
    else
        say "  ${1}: not running -- change applies on next start"
    fi
}

cmd_lock() {
    require_root lock
    changed_any=0
    for svc in "${SERVICES[@]}"; do
        if ! unit_installed "$svc"; then
            warn "${svc}: unit not installed -- skipping"
            continue
        fi
        dropin="$(dropin_path "$svc")"
        if [ -f "$dropin" ] && printf '%s' "$DROPIN_CONTENT" | cmp -s - "$dropin"; then
            say "${svc}: already locked"
            continue
        fi
        mkdir -p "$(dirname "$dropin")"
        printf '%s' "$DROPIN_CONTENT" > "$dropin"
        chmod 644 "$dropin"
        say "${svc}: drop-in installed -> ${dropin}"
        changed_any=1
        NEED_RESTART+=("$svc")
    done
    if [ "$changed_any" -eq 1 ]; then
        systemctl daemon-reload
        for svc in "${NEED_RESTART[@]}"; do restart_if_active "$svc"; done
    fi
    say "lock done. Inspect anytime with: ${BASH_SOURCE[0]} status"
}

cmd_unlock() {
    require_root unlock
    changed_any=0
    for svc in "${SERVICES[@]}"; do
        dropin="$(dropin_path "$svc")"
        if [ ! -f "$dropin" ]; then
            say "${svc}: already unlocked"
            continue
        fi
        rm -f "$dropin"
        rmdir --ignore-fail-on-non-empty "$(dirname "$dropin")"
        say "${svc}: drop-in removed"
        changed_any=1
        NEED_RESTART+=("$svc")
    done
    if [ "$changed_any" -eq 1 ]; then
        systemctl daemon-reload
        for svc in "${NEED_RESTART[@]}"; do restart_if_active "$svc"; done
    fi
    warn "services can now reach the internet. Re-lock after your model pull:"
    warn "  sudo ${BASH_SOURCE[0]} lock"
}

cmd_status() {
    # Read-only: works without sudo (/etc/systemd is world-readable, and
    # `systemctl show` is an unprivileged query).
    for svc in "${SERVICES[@]}" qwen-gateway.service; do
        if ! unit_installed "$svc"; then
            printf '%-28s not installed\n' "$svc"
            continue
        fi
        dropin="$(dropin_path "$svc")"
        deny="$(systemctl show -p IPAddressDeny --value "$svc" 2>/dev/null || true)"
        if [ "$svc" = "qwen-gateway.service" ]; then
            # Sanctioned cloud egress path -- must stay unlocked.
            if [ -f "$dropin" ] || [ -n "$deny" ]; then
                printf '%-28s LOCKED (unexpected! gateway is the sanctioned egress path)\n' "$svc"
            else
                printf '%-28s unlocked (by design: sanctioned cloud egress path)\n' "$svc"
            fi
        elif [ -f "$dropin" ]; then
            if [ -n "$deny" ]; then
                printf '%-28s LOCKED  (IPAddressDeny=%s)\n' "$svc" "$deny"
            else
                printf '%-28s locked on disk, not loaded yet (needs daemon-reload + restart)\n' "$svc"
            fi
        else
            printf '%-28s unlocked\n' "$svc"
        fi
    done
}

case "${1:-}" in
    lock)   NEED_RESTART=(); cmd_lock ;;
    unlock) NEED_RESTART=(); cmd_unlock ;;
    status) cmd_status ;;
    *) die "usage: ${BASH_SOURCE[0]} {lock|unlock|status}  (lock/unlock need sudo)" ;;
esac
