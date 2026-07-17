"""Smart Home hybrid adapter layer (🏡 Smart Home).

This is the SKELETON for the hybrid smart-home integration. It is inert until
an admin connects a provider (routes_smarthome POST /connect); nothing here
makes an outbound call at import or startup.

Three "hybrid" mechanisms this layer is built around
----------------------------------------------------
1. PROVIDER hybrid — one `SmartHomeProvider` interface, many backends. Home
   Assistant is the first (huge device ecosystem + it already holds Apple's
   vetted critical-alerts entitlement via its Companion app, which the Safety
   roadmap reuses). The interface leaves room for direct Matter/mDNS adapters
   later without touching the routes or the Home tab.

2. LOCAL/CLOUD hybrid (the privacy-critical one) — state and control are
   LAN-local: the hub talks to Home Assistant directly over the home network,
   which the egress lock already permits (192.168.0.0/16). The ONLY
   cloud-by-exception path is a push notification to a *locked / off-LAN*
   phone (APNs/FCM) — represented here by `PushBridge`, a stub that will be
   routed through the sanctioned gateway egress, never the locked-down hub.
   `require_lan_url()` refuses a public provider URL so the local half stays
   local.

3. CONTROL hybrid — an action is a small normalized dict
   ({"action": "set_brightness_pct", "params": {"value": 30}}) that BOTH the
   Home tab UI and a future voice+LLM intent layer ("dim the living room to
   30%") produce. `action_to_ha_service()` is the single translation seam from
   that normalized action to a concrete provider service call.

The pure functions (normalize_ha_state, action_to_ha_service, domain_of,
is_controllable, require_lan_url) carry no I/O and are unit-tested directly.
The HTTP methods only run inside request handlers, after config is present.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx

from . import config, secrets_store, smarthome_store

# Namespace/name under which the provider token is kept in the encrypted store.
SECRET_NAMESPACE = "smarthome"
SECRET_NAME = "provider_token"

PROVIDERS = ("home_assistant",)

# Entity domains the hub is willing to expose a control for. Everything else
# (sensor, binary_sensor, device_tracker, weather, sun, ...) is read-only.
CONTROLLABLE_DOMAINS = {
    "light", "switch", "fan", "lock", "cover", "climate",
    "media_player", "scene", "script", "input_boolean", "vacuum",
}

# Short timeout: the provider is on the LAN, so a slow reply means trouble and
# we would rather surface it than hang a family-facing request.
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


class SmartHomeError(Exception):
    """Raised for connection / provider errors (routes map to 502/503)."""


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — unit-tested directly)
# ---------------------------------------------------------------------------
def domain_of(entity_id: str) -> str:
    """'light.living_room' -> 'light'."""
    return (entity_id or "").split(".", 1)[0]


def is_controllable(domain: str) -> bool:
    return domain in CONTROLLABLE_DOMAINS


def require_lan_url(base_url: str) -> str:
    """Return the normalized base URL, or raise ValueError if it is not a LAN
    address. Keeps the local half of the hybrid actually local and consistent
    with the egress lock. Overridable with SMARTHOME_ALLOW_NON_LAN for the rare
    routed-VPN setup."""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("base_url is required")
    parsed = urlparse(base_url if "://" in base_url else "http://" + base_url)
    host = parsed.hostname
    if not host:
        raise ValueError("base_url must include a host, e.g. http://192.168.1.20:8123")
    if config.SMARTHOME_ALLOW_NON_LAN:
        return base_url
    # .local (mDNS) hosts are LAN by definition.
    if host == "localhost" or host.endswith(".local"):
        return base_url
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(
            "Home Assistant must be a LAN address (a private IP or a .local "
            "name) so control stays on your home network. Got a public host: "
            f"{host}")
    if not (ip.is_private or ip.is_loopback):
        raise ValueError(
            f"{host} is a public address; the smart-home provider must be on "
            "your LAN (192.168.x / 10.x / 172.16-31.x / localhost / *.local).")
    return base_url


def normalize_ha_state(state: dict) -> dict:
    """Map one Home Assistant /api/states entry to the hub's entity shape.

    HA gives {entity_id, state, attributes:{friendly_name, ...}}. Area is not
    on the state object in HA's REST API (it needs the template/registry API);
    the skeleton reads an optional attributes.area if present and otherwise
    leaves it None for the registry-sync follow-up to fill.
    """
    entity_id = state.get("entity_id", "")
    domain = domain_of(entity_id)
    attrs = state.get("attributes") or {}
    return {
        "entity_id": entity_id,
        "domain": domain,
        "name": attrs.get("friendly_name") or entity_id,
        "area": attrs.get("area"),
        "state": state.get("state"),
        "attributes": attrs,
        "controllable": is_controllable(domain),
    }


def action_to_ha_service(domain: str, action: str,
                         params: dict | None = None) -> tuple[str, str, dict]:
    """Translate a normalized hub action into an HA service call.

    Returns (service_domain, service, service_data). Raises ValueError for an
    action that does not apply to the domain. This is the single control seam
    shared by the Home tab and the future NL-intent layer.
    """
    params = params or {}
    a = (action or "").strip()

    if not is_controllable(domain):
        raise ValueError(f"domain '{domain}' is read-only")

    # Generic on/off works for most controllable domains.
    if a in ("turn_on", "turn_off", "toggle"):
        return domain, a, {}

    if domain == "light" and a == "set_brightness_pct":
        pct = _as_int(params.get("value"), "value", 0, 100)
        return "light", "turn_on", {"brightness_pct": pct}

    if domain == "cover" and a in ("open", "close", "stop"):
        return "cover", {"open": "open_cover", "close": "close_cover",
                         "stop": "stop_cover"}[a], {}

    if domain == "lock" and a in ("lock", "unlock"):
        return "lock", a, {}

    if domain == "climate" and a == "set_temperature":
        temp = _as_number(params.get("value"), "value")
        return "climate", "set_temperature", {"temperature": temp}

    if domain in ("scene", "script") and a in ("turn_on", "activate", "run"):
        return domain, "turn_on", {}

    raise ValueError(f"action '{action}' is not supported for domain '{domain}'")


def _as_int(value, label: str, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be an integer")
    if not (lo <= n <= hi):
        raise ValueError(f"{label} must be between {lo} and {hi}")
    return n


def _as_number(value, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")


# ---------------------------------------------------------------------------
# Provider interface + Home Assistant adapter
# ---------------------------------------------------------------------------
class SmartHomeProvider:
    """Backend-agnostic provider contract. All methods are async and only
    called from request handlers (never at import)."""

    name = "base"

    async def test_connection(self) -> None:
        raise NotImplementedError

    async def fetch_states(self) -> list[dict]:
        """Return a list of normalized entity dicts."""
        raise NotImplementedError

    async def call_action(self, entity_id: str, action: str,
                          params: dict | None = None) -> dict:
        raise NotImplementedError


class HomeAssistantProvider(SmartHomeProvider):
    """Home Assistant over its REST API (long-lived access token).

    REST polling is the skeleton's data path; a live WebSocket subscription
    (instant state pushes) is the documented next step — see docs/design/
    smart-home.md. Auth is Bearer <token>; the token comes from the encrypted
    secret store, never from this object's repr.
    """

    name = "home_assistant"

    def __init__(self, base_url: str, token: str):
        self.base_url = require_lan_url(base_url)
        self._token = token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json"}

    async def test_connection(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.get(f"{self.base_url}/api/", headers=self._headers())
        except httpx.HTTPError as e:
            raise SmartHomeError(f"cannot reach Home Assistant at {self.base_url}: {e}")
        if r.status_code == 401:
            raise SmartHomeError("Home Assistant rejected the token (401)")
        if r.status_code >= 400:
            raise SmartHomeError(f"Home Assistant returned HTTP {r.status_code}")

    async def fetch_states(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.get(f"{self.base_url}/api/states", headers=self._headers())
        except httpx.HTTPError as e:
            raise SmartHomeError(f"cannot fetch states: {e}")
        if r.status_code == 401:
            raise SmartHomeError("Home Assistant rejected the token (401)")
        if r.status_code >= 400:
            raise SmartHomeError(f"Home Assistant returned HTTP {r.status_code}")
        return [normalize_ha_state(s) for s in r.json()
                if isinstance(s, dict) and s.get("entity_id")]

    async def call_action(self, entity_id: str, action: str,
                          params: dict | None = None) -> dict:
        service_domain, service, data = action_to_ha_service(
            domain_of(entity_id), action, params)
        data = {**data, "entity_id": entity_id}
        url = f"{self.base_url}/api/services/{service_domain}/{service}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.post(url, headers=self._headers(), json=data)
        except httpx.HTTPError as e:
            raise SmartHomeError(f"action failed: {e}")
        if r.status_code >= 400:
            raise SmartHomeError(f"Home Assistant returned HTTP {r.status_code}")
        return {"ok": True, "service": f"{service_domain}.{service}"}


# ---------------------------------------------------------------------------
# Cloud-by-exception push bridge (STUB — the only path that ever leaves the LAN)
# ---------------------------------------------------------------------------
class PushBridge:
    """Delivers an alert to a locked / off-LAN phone via APNs / FCM.

    Reaching a locked phone is physically impossible on-LAN, so this is the one
    sanctioned cloud exception in the smart-home stack. It is a stub in the
    skeleton: wiring it up belongs with the native app shells + Safety roadmap,
    and it will egress through the gateway (unlocked service), not the hub.
    """

    def available(self) -> bool:
        return False

    async def notify(self, target: str, title: str, body: str,
                     critical: bool = False) -> None:
        raise NotImplementedError(
            "push bridge (APNs/FCM) is not wired yet — see the Safety roadmap")


# ---------------------------------------------------------------------------
# Wiring: build a provider from stored config + the encrypted token
# ---------------------------------------------------------------------------
def enabled() -> bool:
    return bool(config.SMARTHOME_ENABLED)


def get_provider() -> SmartHomeProvider | None:
    """Construct the configured provider, or None if not set up. Reads the
    token from the encrypted secret store. Never raises for 'not configured'."""
    if not enabled():
        return None
    cfg = smarthome_store.get_config()
    if not cfg["configured"]:
        return None
    token = secrets_store.get_secret(SECRET_NAMESPACE, SECRET_NAME)
    if not token:
        return None
    if cfg["provider"] == "home_assistant":
        try:
            return HomeAssistantProvider(cfg["base_url"], token)
        except ValueError:
            return None
    return None
