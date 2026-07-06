#!/usr/bin/env bash
# =============================================================================
# install-appliance.sh  —  ONE privileged installer for the whole local LLM stack.
#
# Run this ONCE with sudo. It is the ONLY part of the project that needs root.
# Every service it installs still RUNS ROOTLESS as the project owner — root is
# used only to write system files (systemd units, DNS config, firewall rules).
#
# Consolidates every sudo step across all three layers:
#   • Ollama   (rootless binary ~/.local/bin/ollama)   -> 127.0.0.1:11434 (localhost only)
#   • Gateway  (qwen-stack)                             -> :8080  (API keys)
#   • Home Hub (home-hub)                               -> :80    (http://llm.home)
#
# What it does (idempotent — safe to re-run):
#   1. apt: dnsmasq, avahi-daemon, ufw, python3-venv, libcap2-bin
#   2. systemd services (survive reboot) for ollama, gateway, hub. The hub binds
#      :80 as a non-root user via AmbientCapabilities=CAP_NET_BIND_SERVICE
#      (no setcap, no root at runtime).
#   3. LAN DNS: every WiFi device resolves http://llm.home -> this host
#      (dnsmasq + a REVERSIBLE systemd-resolved stub-off + avahi mDNS llm.local).
#   4. (OPT-IN) ufw firewall: allow the app + DNS ports from your LAN /24 only,
#      keep Ollama localhost-only, and never expose anything to the WAN.
#
# USAGE:
#   sudo bash install-appliance.sh                  # everything EXCEPT the firewall
#   sudo bash install-appliance.sh --with-firewall  # also lock ports to your LAN
#   sudo bash install-appliance.sh --ip 192.168.1.9 # force the LAN IP
#   sudo bash install-appliance.sh --no-dns         # skip the llm.home DNS layer
#   sudo bash install-appliance.sh --no-services    # skip the systemd services
#
# After it runs, manage the stack with systemd (NOT the start-all.sh scripts):
#   sudo systemctl status  ollama qwen-gateway home-hub
#   sudo systemctl restart home-hub
#   journalctl -u home-hub -f
#
# REVERT: see the "TO REVERT" block printed at the end.
# =============================================================================
set -euo pipefail

GW="/home/kanishka/kk_works/LLMs/qwen-stack"
HUB="/home/kanishka/kk_works/LLMs/home-hub"
VOICE="/home/kanishka/kk_works/LLMs/voice-svc"
HOSTNAME_FQDN="llm.home"

usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; }

WITH_FIREWALL=0; DO_DNS=1; DO_SERVICES=1; LAN_IP_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-firewall) WITH_FIREWALL=1 ;;
    --no-dns)        DO_DNS=0 ;;
    --no-services)   DO_SERVICES=0 ;;
    --ip)            shift; LAN_IP_OVERRIDE="${1:-}" ;;
    --ip=*)          LAN_IP_OVERRIDE="${1#*=}" ;;
    -h|--help)       usage; exit 0 ;;
    *) echo "Unknown arg: $1  (try --help)" >&2; exit 2 ;;
  esac
  shift
done

# --- 0. must be root -------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run with sudo:  sudo bash $0" >&2; exit 1
fi

# --- 1. project owner (services run as this user, NOT root) ----------------
if [[ ! -d "$HUB" || ! -d "$GW" ]]; then
  echo "ERROR: expected project dirs $GW and $HUB" >&2; exit 1
fi
RUN_USER="$(stat -c '%U' "$HUB")"
USER_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
OLLAMA_BIN="${USER_HOME}/.local/bin/ollama"
echo "==> runtime user : ${RUN_USER}  (home ${USER_HOME})"
[[ -x "$OLLAMA_BIN" ]] || echo "    WARN: ${OLLAMA_BIN} not found/executable (ollama service may fail)."

# --- 2. detect LAN IP ------------------------------------------------------
LAN_IP="${LAN_IP_OVERRIDE}"
[[ -z "$LAN_IP" ]] && LAN_IP="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' | head -n1 || true)"
if ! [[ "$LAN_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: could not detect a valid LAN IP ('${LAN_IP}'). Pass it: --ip 192.168.1.9" >&2; exit 1
fi
SUBNET="$(echo "$LAN_IP" | awk -F. '{print $1"."$2"."$3".0/24"}')"
echo "==> LAN IP       : ${LAN_IP}  (subnet ${SUBNET})  ->  ${HOSTNAME_FQDN}"

# --- 3. packages -----------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
echo "==> Installing packages: dnsmasq avahi-daemon ufw python3-venv libcap2-bin"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y >/dev/null 2>&1 || true
  apt-get install -y dnsmasq avahi-daemon ufw python3-venv libcap2-bin >/dev/null
else
  echo "    WARN: apt-get not found; install those packages with your package manager."
fi

# ===========================================================================
# 4. systemd services (boot persistence) — all rootless via User=
# ===========================================================================
if [[ "$DO_SERVICES" -eq 1 ]]; then
  echo "==> Stopping any rootless instances so systemd can take over"
  pkill -f "[o]llama serve"  2>/dev/null || true
  pkill -f "[-]-port 8080"   2>/dev/null || true
  pkill -f "[-]-port 8090"   2>/dev/null || true
  pkill -f "[-]-port 8100"   2>/dev/null || true
  sleep 2

  echo "==> /etc/systemd/system/ollama.service"
  cat > /etc/systemd/system/ollama.service <<EOF
[Unit]
Description=Ollama (local models) - localhost only
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Environment=HOME=${USER_HOME}
Environment=OLLAMA_HOST=127.0.0.1:11434
Environment=OLLAMA_KEEP_ALIVE=30m
Environment=OLLAMA_NUM_PARALLEL=1
# Allow several managed models resident at once (Home Hub "Models" admin tab).
# On 16 GB, keep the running set within RAM; raise only with more RAM/GPU.
Environment=OLLAMA_MAX_LOADED_MODELS=3
ExecStart=${OLLAMA_BIN} serve
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

  echo "==> /etc/systemd/system/qwen-gateway.service"
  cat > /etc/systemd/system/qwen-gateway.service <<EOF
[Unit]
Description=Qwen gateway (API-key auth in front of Ollama) - :8080
After=ollama.service network-online.target
Wants=ollama.service

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${GW}
EnvironmentFile=${GW}/.env
ExecStart=${GW}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

  echo "==> /etc/systemd/system/home-hub.service"
  cat > /etc/systemd/system/home-hub.service <<EOF
[Unit]
Description=Home LLM Hub (family portal) - :80 (llm.home)
After=qwen-gateway.service ollama.service network-online.target
Wants=qwen-gateway.service

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${HUB}
EnvironmentFile=${HUB}/.env
# Bind :80 as a non-root user WITHOUT setcap:
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ExecStart=${HUB}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 80
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

  echo "==> /etc/systemd/system/voice-svc.service"
  cat > /etc/systemd/system/voice-svc.service <<EOF
[Unit]
Description=Home Hub voice service (faster-whisper STT + Kokoro TTS) - 127.0.0.1:8100
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${VOICE}
ExecStart=${VOICE}/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8100
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  echo "==> Enabling + starting services"
  systemctl enable --now ollama.service
  for _ in $(seq 1 12); do
    curl -sf -m2 http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1
  done
  systemctl enable --now qwen-gateway.service || echo "    WARN: gateway issue -> journalctl -u qwen-gateway -e"
  systemctl enable --now voice-svc.service    || echo "    WARN: voice-svc issue -> journalctl -u voice-svc -e"
  systemctl enable --now home-hub.service     || echo "    WARN: hub issue -> journalctl -u home-hub -e"
fi

# ===========================================================================
# 5. LAN DNS: llm.home -> this host (+ mDNS llm.local fallback)
# ===========================================================================
if [[ "$DO_DNS" -eq 1 ]]; then
  if systemctl list-unit-files 2>/dev/null | grep -q '^systemd-resolved\.service'; then
    echo "==> Freeing :53 for dnsmasq (disable systemd-resolved stub listener; reversible)"
    install -d -m 0755 /etc/systemd/resolved.conf.d
    cat > /etc/systemd/resolved.conf.d/llm-home.conf <<'RES'
# Installed by install-appliance.sh. Frees TCP/UDP :53 for dnsmasq.
# Revert: delete this file, then  sudo systemctl restart systemd-resolved
[Resolve]
DNSStubListener=no
RES
    systemctl restart systemd-resolved || true
    if [[ -e /run/systemd/resolve/resolv.conf ]]; then
      ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf || true
    fi
  fi

  echo "==> /etc/dnsmasq.d/llm-home.conf"
  install -d -m 0755 /etc/dnsmasq.d
  cat > /etc/dnsmasq.d/llm-home.conf <<EOF
# Installed by install-appliance.sh - re-run the installer to regenerate.
address=/${HOSTNAME_FQDN}/${LAN_IP}
resolv-file=/run/systemd/resolve/resolv.conf
listen-address=${LAN_IP}
listen-address=127.0.0.1
bind-interfaces
local=/${HOSTNAME_FQDN}/
cache-size=1000
EOF

  if command -v dnsmasq >/dev/null 2>&1 && ! dnsmasq --test 2>/dev/null; then
    echo "ERROR: dnsmasq config test failed; review /etc/dnsmasq.d/llm-home.conf" >&2; exit 1
  fi
  systemctl enable dnsmasq >/dev/null 2>&1 || true
  systemctl restart dnsmasq
  echo "==> Enabling avahi (mDNS fallback http://llm.local)"
  systemctl enable avahi-daemon >/dev/null 2>&1 || true
  systemctl restart avahi-daemon || true
fi

# ===========================================================================
# 6. (opt-in) ufw firewall — restrict to the LAN /24
# ===========================================================================
if [[ "$WITH_FIREWALL" -eq 1 ]]; then
  echo "==> Configuring ufw (LAN ${SUBNET} only; SSH stays open to avoid lockout)"
  ufw allow 22/tcp >/dev/null                                  # SSH
  ufw allow from "${SUBNET}" to any port 53   proto udp >/dev/null   # DNS for LAN devices
  ufw allow from "${SUBNET}" to any port 53   proto tcp >/dev/null
  ufw allow 5353/udp >/dev/null                                # mDNS (llm.local)
  ufw allow from "${SUBNET}" to any port 80   proto tcp >/dev/null   # Home Hub
  ufw allow from "${SUBNET}" to any port 8080 proto tcp >/dev/null   # Gateway (API keys)
  ufw allow from "${SUBNET}" to any port 8090 proto tcp >/dev/null   # Hub (rootless fallback)
  # NOTE: 11434 (Ollama) is intentionally NOT opened - it stays localhost-only.
  ufw default deny incoming  >/dev/null
  ufw default allow outgoing >/dev/null
  ufw --force enable >/dev/null
  echo "    ufw enabled. Review:  sudo ufw status verbose"
else
  echo "==> Firewall skipped (default). Your home router already blocks the WAN."
  echo "    Re-run with --with-firewall for defense-in-depth on the LAN."
fi

# ===========================================================================
# 7. summary
# ===========================================================================
cat <<EOF

============================================================================
 APPLIANCE INSTALL COMPLETE
============================================================================
 Services (rootless, boot-persistent):
   sudo systemctl status ollama qwen-gateway voice-svc home-hub

 URLs:
   Home Hub :  http://${HOSTNAME_FQDN}/      (or http://${LAN_IP}/  or http://llm.local/)
   Gateway  :  http://${LAN_IP}:8080/v1      (API keys; model qwen2.5-7b)

 *** ONE MANUAL STEP - ROUTER DHCP DNS ***
   Point your router's DHCP "DNS server" at ${LAN_IP} so every phone, tablet,
   and laptop resolves ${HOSTNAME_FQDN}. Save, then reconnect devices.
   Can't change the router?  Use the zero-config fallback: http://llm.local/
   Android: Settings > Network > Private DNS > Off (or use llm.local).

 VERIFY:
   nslookup ${HOSTNAME_FQDN} 127.0.0.1
   curl -I http://127.0.0.1/healthz
   curl -s http://127.0.0.1:8080/healthz

 TO REVERT EVERYTHING:
   sudo systemctl disable --now home-hub voice-svc qwen-gateway ollama
   sudo rm -f /etc/systemd/system/home-hub.service \\
              /etc/systemd/system/voice-svc.service \\
              /etc/systemd/system/qwen-gateway.service \\
              /etc/systemd/system/ollama.service
   sudo rm -f /etc/dnsmasq.d/llm-home.conf \\
              /etc/systemd/resolved.conf.d/llm-home.conf
   sudo systemctl daemon-reload
   sudo systemctl restart systemd-resolved 2>/dev/null || true
   sudo systemctl restart dnsmasq 2>/dev/null || true
   # if you used --with-firewall:  sudo ufw reset
============================================================================
EOF
