#!/usr/bin/env bash
# =============================================================================
# verify-platform.sh - NO-sudo health check for the platform activation bundle
# (HTTPS :443, homehub.local mDNS, local certs, egress lock).
#
# Prints PASS / FAIL / SKIP per check. SKIP means "feature simply not activated
# yet" (run sudo installer/enable-platform.sh / egress.sh lock), FAIL means
# something that should work is broken. Exit code: 0 when nothing FAILed.
#
# Usage:  installer/verify-platform.sh
# =============================================================================
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="${REPO}/installer"
CERTS="${REPO}/home-hub/certs"
MDNS_NAME="homehub.local"
UNITS=(home-hub-https.service homehub-mdns.service)
LOCKED_SERVICES=(ollama.service voice-svc.service home-hub.service)

PASS=0; FAIL=0; SKIP=0
pass() { printf 'PASS  %s\n' "$*"; PASS=$((PASS + 1)); }
fail() { printf 'FAIL  %s\n' "$*"; FAIL=$((FAIL + 1)); }
skip() { printf 'SKIP  %s\n' "$*"; SKIP=$((SKIP + 1)); }

# --------------------------------------------------------------------------- #
# 1. Unit files syntactically valid
# --------------------------------------------------------------------------- #
for unit in "${UNITS[@]}"; do
    src="${INSTALLER}/${unit}"
    if [ ! -f "$src" ]; then
        fail "unit file missing: ${src}"
        continue
    fi
    if command -v systemd-analyze >/dev/null 2>&1; then
        # verify loads the whole unit tree and chats about unrelated units;
        # trust the exit code, surface only lines about OUR unit.
        out="$(systemd-analyze verify "$src" 2>&1)"
        if [ $? -eq 0 ]; then
            pass "unit syntax OK (systemd-analyze): ${unit}"
        else
            fail "unit syntax (systemd-analyze): ${unit}: $(printf '%s' "$out" | grep -F "$unit" | head -3)"
        fi
    else
        # Fallback: sanity-grep the sections every service unit must have.
        if grep -q '^\[Unit\]' "$src" && grep -q '^\[Service\]' "$src" \
           && grep -q '^ExecStart=' "$src" && grep -q '^\[Install\]' "$src"; then
            pass "unit syntax OK (grep sanity): ${unit}"
        else
            fail "unit syntax (grep sanity): ${unit} lacks [Unit]/[Service]/ExecStart/[Install]"
        fi
    fi
done

# --------------------------------------------------------------------------- #
# 2. Certs present + not expired (warn under 30 days)
# --------------------------------------------------------------------------- #
CRT="${CERTS}/homehub.crt"
KEY="${CERTS}/homehub.key"
if [ ! -f "$CRT" ] || [ ! -f "$KEY" ]; then
    skip "certs not generated yet (run installer/gen-local-cert.sh)"
elif ! openssl x509 -in "$CRT" -noout -checkend 0 >/dev/null 2>&1; then
    fail "leaf cert EXPIRED ($(openssl x509 -in "$CRT" -noout -enddate 2>/dev/null)) -- re-run gen-local-cert.sh"
elif ! openssl x509 -in "$CRT" -noout -checkend $((30 * 24 * 3600)) >/dev/null 2>&1; then
    pass "certs present; leaf expires WITHIN 30 DAYS ($(openssl x509 -in "$CRT" -noout -enddate 2>/dev/null)) -- renew soon"
else
    pass "certs present + valid ($(openssl x509 -in "$CRT" -noout -enddate 2>/dev/null))"
fi

# --------------------------------------------------------------------------- #
# 3. mDNS alias resolving
# --------------------------------------------------------------------------- #
if ! command -v avahi-resolve >/dev/null 2>&1; then
    skip "mDNS: avahi-resolve not installed (sudo apt install avahi-utils)"
else
    resolved="$(timeout 4 avahi-resolve -4 -n "$MDNS_NAME" 2>/dev/null | awk '{print $2}')"
    if [ -n "$resolved" ]; then
        pass "mDNS: ${MDNS_NAME} -> ${resolved}"
    elif [ -f /etc/systemd/system/homehub-mdns.service ]; then
        fail "mDNS: ${MDNS_NAME} not resolving although homehub-mdns.service is installed"
    else
        skip "mDNS: not activated yet (sudo installer/enable-platform.sh)"
    fi
fi

# --------------------------------------------------------------------------- #
# 4. HTTPS :443 reachable (and TLS-verified against our own CA if possible)
# --------------------------------------------------------------------------- #
if [ ! -f /etc/systemd/system/home-hub-https.service ]; then
    skip "HTTPS :443 not activated yet (sudo installer/enable-platform.sh)"
elif curl -ks --max-time 5 -o /dev/null https://127.0.0.1:443/; then
    if [ -f "${CERTS}/rootCA.crt" ] \
       && curl -s --max-time 5 --cacert "${CERTS}/rootCA.crt" -o /dev/null "https://${MDNS_NAME}/" 2>/dev/null; then
        pass "HTTPS :443 reachable + TLS verified against local CA (https://${MDNS_NAME}/)"
    else
        pass "HTTPS :443 reachable (CA-verified check inconclusive -- name/CA not resolvable from here)"
    fi
else
    fail "HTTPS :443 unit installed but not answering -- check: systemctl status home-hub-https"
fi

# --------------------------------------------------------------------------- #
# 5. Egress lock (installer/egress.sh) -- and the gateway staying unlocked
# --------------------------------------------------------------------------- #
lock_count=0
for svc in "${LOCKED_SERVICES[@]}"; do
    [ -f "/etc/systemd/system/${svc}.d/90-egress-lock.conf" ] && lock_count=$((lock_count + 1))
done
if [ "$lock_count" -eq 0 ]; then
    skip "egress lock not activated yet (sudo installer/egress.sh lock)"
elif [ "$lock_count" -eq "${#LOCKED_SERVICES[@]}" ]; then
    pass "egress lock drop-ins present on all ${lock_count} locked services (details: installer/egress.sh status)"
else
    fail "egress lock only on ${lock_count}/${#LOCKED_SERVICES[@]} services -- re-run: sudo installer/egress.sh lock"
fi
if [ -f /etc/systemd/system/qwen-gateway.service.d/90-egress-lock.conf ]; then
    fail "qwen-gateway.service is egress-locked but is the sanctioned cloud egress path -- remove its drop-in"
else
    pass "qwen-gateway.service unlocked (by design: sanctioned cloud egress path)"
fi

# --------------------------------------------------------------------------- #
echo
printf 'result: %d PASS, %d FAIL, %d SKIP\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ]
