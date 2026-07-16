#!/usr/bin/env bash
# =============================================================================
# enable-platform.sh - one-time (sudo) activation of the platform bundle:
#
#   * home-hub-https.service  hub over HTTPS :443 (fully-installable PWA)
#   * homehub-mdns.service    homehub.local mDNS alias that survives reboots
#   * local certs             regenerated ONLY if missing or expiring soon
#
# Idempotent: safe to re-run at any time. Unit files are only re-copied (and
# services only restarted) when their content actually changed; certs are only
# regenerated when missing or within 30 days of expiry.
#
# Usage:
#   sudo installer/enable-platform.sh
#
# Afterwards, verify WITHOUT sudo:
#   installer/verify-platform.sh
#
# The egress firewall lock is separate (also needs sudo): installer/egress.sh
# =============================================================================
set -euo pipefail

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="${REPO}/installer"
CERTS="${REPO}/home-hub/certs"
# Regenerate the leaf cert when fewer than 30 days of validity remain.
CERT_MIN_SECONDS=$((30 * 24 * 3600))
UNITS=(home-hub-https.service homehub-mdns.service)
# Run cert generation as the repo owner (not root) so the service user can
# still read its own keys and git-ignored files keep their ownership.
APP_USER="$(stat -c %U "$REPO")"

[ "$(id -u)" -eq 0 ] || die "this script needs root. Re-run as: sudo ${BASH_SOURCE[0]}"

FAILED=0

# --------------------------------------------------------------------------- #
# Step 1: local certs (only if missing or expiring within 30 days)
# --------------------------------------------------------------------------- #
say "step 1/3: certificates (${CERTS})"
if [ -f "${CERTS}/homehub.crt" ] && [ -f "${CERTS}/homehub.key" ] \
   && openssl x509 -in "${CERTS}/homehub.crt" -noout -checkend "$CERT_MIN_SECONDS" >/dev/null 2>&1; then
    say "  certs present and valid for 30+ days -- keeping them"
else
    say "  certs missing or expiring -- regenerating as user '${APP_USER}'"
    runuser -u "$APP_USER" -- bash "${INSTALLER}/gen-local-cert.sh"
fi

# --------------------------------------------------------------------------- #
# Step 2: install unit files (copy only when content changed)
# --------------------------------------------------------------------------- #
say "step 2/3: systemd unit files"
CHANGED_UNITS=()
for unit in "${UNITS[@]}"; do
    src="${INSTALLER}/${unit}"
    dst="/etc/systemd/system/${unit}"
    [ -f "$src" ] || die "missing ${src} -- is the repo checkout complete?"
    if cmp -s "$src" "$dst" 2>/dev/null; then
        say "  ${unit}: already installed, unchanged"
    else
        install -m 644 "$src" "$dst"
        CHANGED_UNITS+=("$unit")
        say "  ${unit}: installed -> ${dst}"
    fi
done
if [ "${#CHANGED_UNITS[@]}" -gt 0 ]; then
    systemctl daemon-reload
    say "  daemon-reload done"
fi

# --------------------------------------------------------------------------- #
# Step 3: enable + start (restart only units whose file changed)
# --------------------------------------------------------------------------- #
say "step 3/3: enable + start services"
if ! systemctl cat avahi-daemon.service >/dev/null 2>&1; then
    warn "avahi-daemon.service not found -- homehub-mdns needs it (sudo apt install avahi-daemon)"
fi
for unit in "${UNITS[@]}"; do
    systemctl enable "$unit" >/dev/null 2>&1
    changed=0
    for c in ${CHANGED_UNITS[@]+"${CHANGED_UNITS[@]}"}; do [ "$c" = "$unit" ] && changed=1; done
    if [ "$changed" -eq 1 ] || ! systemctl is-active --quiet "$unit"; then
        say "  ${unit}: (re)starting"
        systemctl restart "$unit" || true
    else
        say "  ${unit}: already running, unit unchanged -- not restarted"
    fi
done

# Give slow starters a moment before reporting, then summarize.
sleep 2
echo
say "summary"
for unit in "${UNITS[@]}"; do
    state="$(systemctl is-active "$unit" 2>/dev/null || true)"
    if [ "$state" = "active" ]; then
        say "  ${unit}: active"
    else
        warn "  ${unit}: ${state:-unknown} -- inspect with: systemctl status ${unit}"
        FAILED=1
    fi
done

echo
say "next steps:"
say "  * verify (no sudo):  ${INSTALLER}/verify-platform.sh"
say "  * trust the CA once per device: http://homehub.local/static/homehub-ca.crt"
say "  * then open https://homehub.local/ and install the PWA"
say "  * optional LAN-only egress lock: sudo ${INSTALLER}/egress.sh lock"
exit "$FAILED"
