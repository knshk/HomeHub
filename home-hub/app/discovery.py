"""Optional LAN discovery via mDNS/Bonjour (zeroconf).

On app startup we advertise the Home Hub on the local network so other devices
can find it without knowing its IP:

  * a service of type ``_homehub._tcp.local.`` carrying the host LAN IP +
    HUB_PORT and a couple of non-sensitive TXT records (name, version), and
  * best-effort the hostname ``homehub.local`` so a browser can simply open
    ``http://homehub.local:<port>``.

EVERYTHING here is optional and fail-safe. If the ``zeroconf`` package is not
installed, or registration fails for any reason (e.g. avahi/Bonjour already owns
``.local`` on this host, no usable network interface, a port/name clash, ...),
we log a warning and continue. Discovery is a convenience, never a hard
dependency: the hub must keep serving over its IP regardless.

Nothing here exposes secrets. TXT records carry only the friendly name and the
version -- the same non-sensitive identity returned by the public
``/api/discovery`` endpoint.
"""
from __future__ import annotations

import logging
import socket

from . import config

log = logging.getLogger("home-hub.discovery")

SERVICE_TYPE = "_homehub._tcp.local."

# Module-level handles so shutdown can unregister what startup registered.
_zeroconf = None
_service_info = None
# Registered .local hostname (FQDN), if we managed to claim one.
_hostname_registered: str | None = None


def _detect_lan_ip() -> str:
    """Best-effort LAN IPv4 of this host.

    Prefer an explicit ``LAN_IP`` from config; otherwise discover the address
    the kernel would use to reach an off-link destination (no packets are
    actually sent by ``connect()`` on a UDP socket). Falls back to 127.0.0.1.
    """
    configured = (config.LAN_IP or "").strip()
    if configured and configured != "127.0.0.1":
        return configured

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 8.8.8.8 is just a routing hint; nothing is transmitted for UDP connect.
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass

    # Last resort: resolve the hostname (may still be 127.0.0.1 on some hosts).
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip:
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def _short_hostname() -> str:
    try:
        host = socket.gethostname() or "homehub"
    except Exception:
        host = "homehub"
    # Strip any domain suffix; mDNS labels are single-component.
    return host.split(".")[0]


def register() -> None:
    """Register the mDNS service + hostname. Never raises."""
    global _zeroconf, _service_info, _hostname_registered

    try:
        # Imported lazily so a missing package degrades to "discovery off".
        from zeroconf import IPVersion, ServiceInfo, Zeroconf
    except Exception as exc:  # ImportError or anything during import
        log.warning("mDNS discovery disabled: zeroconf import failed: %s", exc)
        return

    try:
        lan_ip = _detect_lan_ip()
        port = int(config.HUB_PORT)
        friendly = config.HUB_NAME or _short_hostname()

        # Instance label for the service. Keep it stable and unique-ish.
        instance = f"{friendly} ({_short_hostname()})"
        service_name = f"{instance}.{SERVICE_TYPE}"

        # Best-effort hostname so http://homehub.local:<port> works. We only try
        # to claim it; avahi may already own .local, in which case zeroconf will
        # raise NonUniqueNameException below and we fall back gracefully.
        server = "homehub.local."

        txt = {
            "name": friendly,
            "version": config.HUB_VERSION,
        }

        info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=service_name,
            addresses=[socket.inet_aton(lan_ip)],
            port=port,
            properties=txt,
            server=server,
        )

        zc = Zeroconf(ip_version=IPVersion.V4Only)
        try:
            # allow_name_change=True lets zeroconf pick e.g. "homehub-2.local"
            # if the name is taken, rather than failing outright.
            zc.register_service(info, allow_name_change=True)
        except Exception as exc:
            # Could not register even with name changes -> abandon, but keep the
            # hub running. Close the Zeroconf we opened.
            log.warning(
                "mDNS discovery disabled: could not register service "
                "(another responder may own .local): %s",
                exc,
            )
            try:
                zc.close()
            except Exception:
                pass
            return

        _zeroconf = zc
        _service_info = info
        # info.server reflects the actually-registered (possibly renamed) name.
        _hostname_registered = (info.server or server).rstrip(".")

        log.info(
            "mDNS discovery active: %s -> %s:%d (hostname %s)",
            service_name,
            lan_ip,
            port,
            _hostname_registered,
        )
    except Exception as exc:
        # Catch-all: discovery must NEVER crash the hub.
        log.warning("mDNS discovery disabled: unexpected error: %s", exc)
        # Ensure we don't leave a half-open Zeroconf around.
        try:
            if _zeroconf is not None:
                _zeroconf.close()
        except Exception:
            pass
        _zeroconf = None
        _service_info = None
        _hostname_registered = None


def unregister() -> None:
    """Unregister and close zeroconf. Never raises."""
    global _zeroconf, _service_info, _hostname_registered
    zc = _zeroconf
    info = _service_info
    try:
        if zc is not None and info is not None:
            try:
                zc.unregister_service(info)
            except Exception as exc:
                log.warning("mDNS unregister failed (continuing): %s", exc)
        if zc is not None:
            try:
                zc.close()
            except Exception as exc:
                log.warning("mDNS close failed (continuing): %s", exc)
    finally:
        _zeroconf = None
        _service_info = None
        _hostname_registered = None
