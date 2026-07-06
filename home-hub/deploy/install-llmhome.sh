#!/usr/bin/env bash
# =============================================================================
# deploy/install-llmhome.sh
#
#   THE PRODUCT INSTALLER for the Home LLM Hub appliance.
#   *** THIS SCRIPT REQUIRES sudo / root. *** It is the ONLY part of the project
#   that needs privileges, and it touches the host exactly ONCE to:
#
#     1. Install + configure dnsmasq so every WiFi device can resolve
#        http://llm.home  ->  this host's LAN IP.
#     2. Resolve the Ubuntu systemd-resolved :53 conflict (free port 53 for
#        dnsmasq, surgically and reversibly, while keeping real upstream DNS).
#     3. Enable mDNS fallback (avahi) so http://llm.local works with zero
#        router config, even if LAN DNS is bypassed.
#     4. Let the hub serve on :80 WITHOUT running as root at runtime, by
#        granting cap_net_bind_service to the venv python (setcap, preferred).
#        A reverse-proxy (nginx) fallback is documented if you prefer that.
#     5. Install + enable the home-hub systemd service (runs as the normal user).
#
#   The hub PROCESS still runs rootless (User=kanishka). Only this installer
#   needs root, and only to wire DNS + the :80 capability + the service unit.
#
#   Idempotent: safe to re-run any number of times. Re-running re-detects the
#   LAN IP, re-writes the dnsmasq drop-in, and re-applies setcap (important
#   after a venv/python upgrade, which DROPS the capability).
#
#   USAGE:
#       sudo bash deploy/install-llmhome.sh
#       # optional: pass an explicit LAN IP if auto-detection is wrong:
#       sudo bash deploy/install-llmhome.sh 192.168.1.50
#
#   REVERT: see deploy/README-deploy.md ("Uninstall / revert").
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Must be root.
# ---------------------------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: this product installer must run as root." >&2
    echo "       Re-run:  sudo bash deploy/install-llmhome.sh" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Paths + the unprivileged owner the hub runs as.
#    Resolve the project root from this script (deploy/ is one level down).
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
ENV_FILE="${PROJECT_ROOT}/.env"
SERVICE_SRC="${SCRIPT_DIR}/home-hub.service"
DNSMASQ_DROPIN_DST="/etc/dnsmasq.d/dnsmasq-llm.conf"
SERVICE_DST="/etc/systemd/system/home-hub.service"
HOSTNAME_FQDN="llm.home"

# The hub should run as the user who OWNS the project, not root. Derive it from
# the project directory owner so this works regardless of who invoked sudo.
RUN_USER="$(stat -c '%U' "${PROJECT_ROOT}")"
echo "==> Home LLM Hub product installer"
echo "    project root : ${PROJECT_ROOT}"
echo "    runtime user : ${RUN_USER} (hub runs rootless as this user)"

if [[ ! -x "${VENV_DIR}/bin/uvicorn" ]]; then
    echo "ERROR: venv not found at ${VENV_DIR}." >&2
    echo "       Run the rootless installer first:  ./install.sh" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Detect the LAN IP (per the research recipe: ip route get 1.1.1.1).
#    Allow an explicit override as the first CLI arg.
# ---------------------------------------------------------------------------
LAN_IP="${1:-}"
if [[ -z "${LAN_IP}" ]]; then
    LAN_IP="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' | head -n1 || true)"
fi
if [[ -z "${LAN_IP}" ]]; then
    echo "ERROR: could not auto-detect a LAN IP. Pass it explicitly:" >&2
    echo "       sudo bash deploy/install-llmhome.sh 192.168.1.50" >&2
    exit 1
fi
# Basic sanity: dotted-quad.
if ! [[ "${LAN_IP}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: '${LAN_IP}' is not a valid IPv4 address." >&2
    exit 1
fi
echo "==> Using LAN IP: ${LAN_IP}  (-> ${HOSTNAME_FQDN})"

# ---------------------------------------------------------------------------
# 3. Install packages (dnsmasq for LAN DNS, avahi for mDNS fallback).
#    Idempotent: apt-get install is a no-op if already present.
# ---------------------------------------------------------------------------
echo "==> Ensuring dnsmasq + avahi-daemon are installed"
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y >/dev/null 2>&1 || true
    apt-get install -y dnsmasq avahi-daemon >/dev/null
else
    echo "WARN: apt-get not found. Install 'dnsmasq' and 'avahi-daemon' with your"
    echo "      package manager, then re-run this script."
fi

# ---------------------------------------------------------------------------
# 4. Resolve the systemd-resolved :53 conflict (Ubuntu).
#    systemd-resolved binds 127.0.0.53:53 by default, which collides with
#    dnsmasq. We DISABLE only its stub listener (DNSStubListener=no) via a
#    drop-in -- this is surgical and reversible and keeps systemd-resolved doing
#    upstream resolution. dnsmasq then forwards unknown names to the real
#    upstreams via /run/systemd/resolve/resolv.conf.
# ---------------------------------------------------------------------------
if systemctl list-unit-files 2>/dev/null | grep -q '^systemd-resolved\.service'; then
    echo "==> Disabling systemd-resolved stub listener on :53 (drop-in)"
    install -d -m 0755 /etc/systemd/resolved.conf.d
    cat > /etc/systemd/resolved.conf.d/llm-home.conf <<'RESOLVED'
# Installed by Home LLM Hub (deploy/install-llmhome.sh).
# Free TCP/UDP :53 for dnsmasq by turning off the local stub resolver.
# systemd-resolved still performs upstream resolution; dnsmasq forwards to it
# via /run/systemd/resolve/resolv.conf. To revert: delete this file and run
#   sudo systemctl restart systemd-resolved
[Resolve]
DNSStubListener=no
RESOLVED
    systemctl restart systemd-resolved || true

    # Ensure /etc/resolv.conf is usable for the host itself once the stub is off.
    # Point it at the resolved-managed upstream list if present.
    if [[ -e /run/systemd/resolve/resolv.conf ]]; then
        ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf || true
    fi
else
    echo "==> systemd-resolved not present; skipping :53 stub handling"
fi

# ---------------------------------------------------------------------------
# 5. Write the dnsmasq drop-in with the detected IP (idempotent overwrite).
#    Also point dnsmasq at the systemd-resolved upstream list so normal browsing
#    keeps working, and restrict it to the LAN (never an open WAN resolver).
# ---------------------------------------------------------------------------
echo "==> Writing ${DNSMASQ_DROPIN_DST}"
install -d -m 0755 /etc/dnsmasq.d
cat > "${DNSMASQ_DROPIN_DST}" <<EOF
# Installed by Home LLM Hub (deploy/install-llmhome.sh) -- DO NOT EDIT BY HAND;
# re-run the installer to regenerate. Maps the hub hostname to this host and
# forwards everything else to the system upstream resolvers.

# llm.home -> this host (rewritten on every install with the detected LAN IP).
address=/${HOSTNAME_FQDN}/${LAN_IP}

# Forward all other queries to the real upstreams systemd-resolved tracks, so
# regular internet name resolution still works for LAN clients.
resolv-file=/run/systemd/resolve/resolv.conf

# Answer DNS on the LAN address + loopback only. Never an open resolver.
listen-address=${LAN_IP}
listen-address=127.0.0.1
bind-interfaces

# Treat llm.home as authoritative locally; do not forward it upstream.
local=/${HOSTNAME_FQDN}/

# Modest cache; reduce log noise. Increase cache-size for big LANs.
cache-size=1000
EOF

# ---------------------------------------------------------------------------
# 6. Enable + (re)start dnsmasq. Validate config first; fail loudly if bad.
# ---------------------------------------------------------------------------
echo "==> Validating dnsmasq config and (re)starting it"
if command -v dnsmasq >/dev/null 2>&1; then
    if ! dnsmasq --test 2>/dev/null; then
        echo "ERROR: dnsmasq config test failed. Review ${DNSMASQ_DROPIN_DST}." >&2
        exit 1
    fi
fi
systemctl enable dnsmasq >/dev/null 2>&1 || true
systemctl restart dnsmasq

# Enable the mDNS fallback (http://llm.local) -- zero router config needed.
echo "==> Enabling avahi-daemon (mDNS fallback: http://llm.local)"
systemctl enable avahi-daemon >/dev/null 2>&1 || true
systemctl restart avahi-daemon || true

# ---------------------------------------------------------------------------
# 7. Grant the venv python the right to bind :80 WITHOUT root at runtime.
#    setcap is applied to the REAL python binary the venv symlink resolves to
#    (capabilities don't follow symlinks). NOTE: this capability is lost if the
#    venv/python is rebuilt or the system python is upgraded -- just re-run this
#    installer to re-apply. Reverse-proxy alternative documented in the README.
# ---------------------------------------------------------------------------
echo "==> Granting cap_net_bind_service to the venv python (for :80)"
VENV_PY_REAL="$(readlink -f "${VENV_DIR}/bin/python")"
if [[ -z "${VENV_PY_REAL}" || ! -e "${VENV_PY_REAL}" ]]; then
    echo "ERROR: could not resolve the venv python binary." >&2
    exit 1
fi
if command -v setcap >/dev/null 2>&1; then
    setcap 'cap_net_bind_service=+ep' "${VENV_PY_REAL}"
    echo "    setcap applied to: ${VENV_PY_REAL}"
    # Verify.
    if command -v getcap >/dev/null 2>&1; then
        getcap "${VENV_PY_REAL}" || true
    fi
else
    echo "WARN: 'setcap' not found (install libcap2-bin). The hub cannot bind :80"
    echo "      without it. Either install libcap2-bin and re-run, or use the"
    echo "      nginx reverse-proxy fallback (see deploy/README-deploy.md) and"
    echo "      keep HUB_PORT=8090."
fi

# ---------------------------------------------------------------------------
# 8. Flip HUB_PORT to 80 in the project .env so the service binds :80.
#    Idempotent: only rewrites if not already 80. Creates .env from example if
#    missing so the appliance is bootable, but the operator still must fill in
#    the gateway secrets.
# ---------------------------------------------------------------------------
echo "==> Setting HUB_PORT=80 in ${ENV_FILE}"
if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ -f "${PROJECT_ROOT}/.env.example" ]]; then
        cp "${PROJECT_ROOT}/.env.example" "${ENV_FILE}"
        chown "${RUN_USER}:${RUN_USER}" "${ENV_FILE}" 2>/dev/null || true
        chmod 600 "${ENV_FILE}"
        echo "    created .env from .env.example -- REMEMBER to set the gateway secrets!"
    else
        echo "WARN: no .env and no .env.example; skipping HUB_PORT edit."
    fi
fi
if [[ -f "${ENV_FILE}" ]]; then
    if grep -qE '^HUB_PORT=' "${ENV_FILE}"; then
        sed -i 's/^HUB_PORT=.*/HUB_PORT=80/' "${ENV_FILE}"
    else
        printf '\nHUB_PORT=80\n' >> "${ENV_FILE}"
    fi
    # Record the detected LAN IP for friendly URLs (optional, harmless).
    if grep -qE '^LAN_IP=' "${ENV_FILE}"; then
        sed -i "s/^LAN_IP=.*/LAN_IP=${LAN_IP}/" "${ENV_FILE}"
    else
        printf 'LAN_IP=%s\n' "${LAN_IP}" >> "${ENV_FILE}"
    fi
fi

# ---------------------------------------------------------------------------
# 9. Install + enable the systemd service (runs as the unprivileged RUN_USER).
# ---------------------------------------------------------------------------
if [[ -f "${SERVICE_SRC}" ]]; then
    echo "==> Installing systemd unit -> ${SERVICE_DST}"
    install -D -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"
    # If the project owner differs from the unit's hardcoded User=, patch it so
    # the service runs as whoever owns the project.
    if [[ "${RUN_USER}" != "kanishka" ]]; then
        sed -i "s/^User=kanishka/User=${RUN_USER}/; s/^Group=kanishka/Group=${RUN_USER}/" "${SERVICE_DST}"
    fi
    systemctl daemon-reload
    systemctl enable home-hub.service >/dev/null 2>&1 || true
    systemctl restart home-hub.service || {
        echo "WARN: home-hub.service did not start cleanly. Check it with:"
        echo "      journalctl -u home-hub.service -e"
    }
else
    echo "WARN: ${SERVICE_SRC} not found; skipping service install."
fi

# ---------------------------------------------------------------------------
# 10. Done. Print verification + the critical router DHCP-DNS step.
# ---------------------------------------------------------------------------
cat <<EOF

============================================================================
 LLM.HOME INSTALL COMPLETE
============================================================================

WHAT HAPPENED
  - dnsmasq now answers   ${HOSTNAME_FQDN}  ->  ${LAN_IP}
  - systemd-resolved stub listener on :53 disabled (reversible drop-in)
  - mDNS fallback enabled: http://llm.local works with NO router config
  - venv python may bind :80 rootless (setcap cap_net_bind_service)
  - HUB_PORT set to 80 in ${ENV_FILE}
  - home-hub.service installed + enabled (runs as user '${RUN_USER}')

*** CRITICAL MANUAL STEP -- ROUTER DHCP DNS ***
  By default only THIS host knows about ${HOSTNAME_FQDN}. To make every phone,
  tablet, and laptop on your WiFi resolve it, point your router's DHCP "DNS
  server" setting at this host:

      Router admin page  ->  LAN / DHCP settings  ->  DNS server
      Set the PRIMARY DNS server to:   ${LAN_IP}
      (Save, then reconnect devices or wait for DHCP lease renewal.)

  If you cannot change the router, devices can still use the mDNS fallback:
      http://llm.local

VERIFY (in this order)
  1) On THIS host:        nslookup ${HOSTNAME_FQDN} 127.0.0.1
  2) On THIS host:        curl -I http://127.0.0.1/healthz
  3) On a WiFi device:    nslookup ${HOSTNAME_FQDN}
  4) On a WiFi device:    open http://${HOSTNAME_FQDN}/     (or http://llm.local/)

ANDROID NOTE
  If a phone cannot resolve ${HOSTNAME_FQDN}, disable "Private DNS":
      Settings > Network & internet > Private DNS > "Off" or "Automatic".
  Private DNS (DoH) bypasses LAN DNS entirely. The http://llm.local fallback
  is unaffected by Private DNS.

SERVICE CONTROL
      sudo systemctl status home-hub.service
      journalctl -u home-hub.service -f

To revert everything, see deploy/README-deploy.md ("Uninstall / revert").
============================================================================
EOF
