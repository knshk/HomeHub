#!/usr/bin/env bash
# =============================================================================
# homehub.sh — one command to run the whole appliance and make it reachable
# from every device on your WiFi.
#
#   sudo ./homehub.sh start     start/refresh all services, open the LAN
#                               firewall, publish homehub.local, print URLs
#   ./homehub.sh status         show service + reachability status (no sudo)
#   ./homehub.sh url            just print the addresses to open
#   sudo ./homehub.sh restart   restart services (after a code change)
#   sudo ./homehub.sh stop      stop the family-facing services (leaves ollama)
#   sudo ./homehub.sh stop-all  stop EVERYTHING, incl. ollama + the mDNS advert
#                               (alias: shutdown)
#   sudo ./homehub.sh https     turn on local HTTPS + persistent homehub.local
#                               (delegates to installer/enable-platform.sh)
#
# Why a firewall step: the hub already binds 0.0.0.0:80, but ufw (default
# deny-incoming) blocks other devices while localhost still works — which looks
# like "running but unreachable". `start` opens :80/:443 to the LAN only.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICES=(ollama qwen-gateway home-hub voice-svc)   # order: deps before hub
FAMILY_FACING=(home-hub qwen-gateway voice-svc)      # stop-set (leave ollama)
LAN_SUBNET="192.168.0.0/16"                          # RFC1918 home LAN
PORTS=(80 443)

lan_ip() { hostname -I | tr ' ' '\n' | grep -E '^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)' | head -1; }

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "This step needs root. Re-run:  sudo $0 $*" >&2
    exit 1
  fi
}

open_firewall() {
  if ! command -v ufw >/dev/null 2>&1; then
    echo "  · ufw not installed — nothing to open (is another firewall in use?)"
    return
  fi
  if ! ufw status 2>/dev/null | grep -q "Status: active"; then
    echo "  · ufw is inactive — no firewall is blocking the LAN"
    return
  fi
  for p in "${PORTS[@]}"; do
    # Idempotent: `ufw allow` is a no-op if the rule already exists.
    ufw allow from "$LAN_SUBNET" to any port "$p" proto tcp >/dev/null
    echo "  · ufw: allowed $LAN_SUBNET -> :$p/tcp"
  done
  ufw reload >/dev/null 2>&1 || true
}

publish_mdns() {
  local ip; ip="$(lan_ip)"
  [ -z "$ip" ] && { echo "  · no LAN IP found — skipping mDNS"; return; }
  # Prefer the persistent unit (installed by enable-platform.sh); else publish a
  # transient advert that survives this script via systemd-run.
  if systemctl list-unit-files 2>/dev/null | grep -q '^homehub-mdns.service'; then
    systemctl restart homehub-mdns.service 2>/dev/null && echo "  · mDNS: homehub-mdns.service (persistent)" && return
  fi
  if command -v avahi-publish >/dev/null 2>&1; then
    systemctl stop homehub-mdns-transient.service 2>/dev/null || true
    if systemd-run --unit=homehub-mdns-transient --property=Restart=always \
         avahi-publish -a -R homehub.local "$ip" >/dev/null 2>&1; then
      echo "  · mDNS: publishing homehub.local -> $ip (transient; run 'https' to make it permanent)"
    fi
  else
    echo "  · avahi-publish not found — homehub.local won't resolve (use the IP)"
  fi
}

print_urls() {
  local ip; ip="$(lan_ip)"
  echo
  echo "  Open Home Hub from ANY device on the same WiFi:"
  echo "      http://${ip:-<this-box-LAN-IP>}"
  echo "      http://homehub.local        (once mDNS is up)"
  echo
  echo "  Install as an app (PWA): open one of the URLs above, then use the"
  echo "  Install prompt (Android/desktop) or Share -> Add to Home Screen (iOS)."
  echo "  Full Android/desktop install needs HTTPS:  sudo $0 https"
}

cmd_start() {
  need_root start
  echo "Starting HomeHub…"
  systemctl restart "${SERVICES[@]}"
  echo "  · services restarted: ${SERVICES[*]}"
  open_firewall
  publish_mdns
  # brief health probe
  sleep 1
  if curl -fsS --max-time 3 http://127.0.0.1:80/healthz >/dev/null 2>&1; then
    echo "  · hub healthy on :80"
  else
    echo "  · WARNING: hub did not answer /healthz yet — check: journalctl -u home-hub -n 50"
  fi
  print_urls
}

cmd_restart() { need_root restart; systemctl restart "${SERVICES[@]}"; echo "restarted: ${SERVICES[*]}"; }
cmd_stop()    { need_root stop; systemctl stop "${FAMILY_FACING[@]}"; echo "stopped: ${FAMILY_FACING[*]} (ollama left running)"; }

cmd_stop_all() {
  need_root stop-all
  # Stop dependents before ollama (reverse of start order). `|| true` so one
  # already-stopped/odd unit can't abort a full takedown under `set -e`.
  local order=(home-hub qwen-gateway voice-svc ollama)
  systemctl stop "${order[@]}" 2>/dev/null || true
  # Stop any mDNS advert too, so homehub.local stops pointing at a dead hub.
  systemctl stop homehub-mdns-transient.service 2>/dev/null || true
  systemctl stop homehub-mdns.service 2>/dev/null || true
  echo "stopped EVERYTHING: ${order[*]} (+ mDNS advert)"
  echo "note: the LAN firewall rule is left in place (harmless with nothing"
  echo "      listening); 'start' or a reboot brings services + mDNS back up."
}

cmd_status() {
  echo "Services:"
  for s in "${SERVICES[@]}"; do printf "  %-14s %s\n" "$s" "$(systemctl is-active "$s" 2>/dev/null || echo unknown)"; done
  echo
  echo "Listening:"
  ss -tlnp 2>/dev/null | grep -E ':80 |:8080|:443 ' | sed 's/^/  /' || true
  echo
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    if ufw status 2>/dev/null | grep -qE "$LAN_SUBNET.*(80|443)|(^| )(80|443)/tcp"; then
      echo "Firewall: ufw active, LAN allowed to :80/:443  ✓"
    else
      echo "Firewall: ufw active but :80/:443 NOT opened to the LAN  ✗  (run: sudo $0 start)"
    fi
  else
    echo "Firewall: ufw inactive/absent — not blocking"
  fi
  print_urls
}

cmd_https() { need_root https; exec "$HERE/installer/enable-platform.sh"; }

case "${1:-start}" in
  start)   cmd_start ;;
  restart) cmd_restart ;;
  stop)    cmd_stop ;;
  stop-all|shutdown) cmd_stop_all ;;
  status)  cmd_status ;;
  url|urls) print_urls ;;
  https)   cmd_https ;;
  *) echo "usage: $0 {start|restart|stop|stop-all|status|url|https}"; exit 1 ;;
esac
