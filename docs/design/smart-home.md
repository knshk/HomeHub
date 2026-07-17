# Smart Home — Hybrid Integration (skeleton)

**Status:** skeleton shipped (backend + adapter + Home tab + tests). Inert until
an admin links a provider; goes live after the next hub restart.

## Purpose

Give the family a single, private place to see and control the home — lights,
locks, climate, sensors — from the same hub that already holds chat, calendar
and photos. The design keeps control **on the home network** by default and
treats the cloud as a narrow, disclosed exception rather than the default path.

## The three "hybrid" mechanisms

The integration is deliberately built around three hybrids, so the skeleton
leaves the right seams for the full build-out.

### 1. Provider hybrid — one interface, many backends
`smarthome.SmartHomeProvider` is a small async contract
(`test_connection` / `fetch_states` / `call_action`). `HomeAssistantProvider`
is the first implementation. Home Assistant is the anchor because it already
speaks to thousands of devices and its Companion app holds Apple's vetted
critical-alerts entitlement (which the Safety roadmap reuses). The interface
leaves room for direct Matter/mDNS adapters later without touching the routes
or the Home tab.

### 2. Local/cloud hybrid — the privacy-critical one
State and control are **LAN-local**: the hub talks to Home Assistant directly
over the home network, which the egress lock already permits
(`192.168.0.0/16`). `smarthome.require_lan_url()` refuses a public provider URL
(overridable with `SMARTHOME_ALLOW_NON_LAN` for a routed-VPN HA) so the local
half stays local.

The **only** cloud-by-exception path is a push notification to a *locked /
off-LAN* phone — physically impossible on-LAN. That is represented by
`smarthome.PushBridge` (a stub in the skeleton). When wired, it egresses
through the **gateway** service (the sanctioned, unlocked egress path), never
through the locked-down hub.

### 3. Control hybrid — one action shape, two producers
An action is a small normalized dict —
`{"action": "set_brightness_pct", "params": {"value": 30}}` — that BOTH the
Home tab UI and a future voice+LLM intent layer ("dim the living room to 30%")
produce. `smarthome.action_to_ha_service()` is the single translation seam from
that normalized action to a concrete provider service call, so the NL layer
lands later with no change to the routes.

## Design

```
 Home tab (SPA)            hub :80 / :443                 LAN
 ────────────      ┌───────────────────────────┐   ┌──────────────────┐
  status  ───────► │ routes_smarthome /api/home │   │ Home Assistant   │
  connect ───────► │  ├ smarthome_store (sqlite)│   │  REST /api/states│
  device  ───────► │  │   config·cache·perms·favs│──┼─►│  /api/services  │
  on/off  ───────► │  └ smarthome (adapter)      │   │  (LAN address)   │
                   │      ├ HomeAssistantProvider │   └──────────────────┘
                   │      ├ pure: normalize/action│
                   │      └ PushBridge (stub)─────┼───►  APNs/FCM  (future,
                   │   token ◄─ secrets_store      │      via gateway egress)
                   └───────────────────────────┘
```

- **`smarthome_store.py`** — sqlite persistence, table-lazy and `db_path`-
  injectable like `calendar_store`/`secrets_store`: provider config (no token),
  a wholesale-replaced entity **cache** (so the Home tab renders when HA is
  briefly unreachable), per-entity **permissions**, per-user **favourites**.
- **`smarthome.py`** — the adapter. Pure, I/O-free helpers
  (`normalize_ha_state`, `action_to_ha_service`, `require_lan_url`,
  `domain_of`, `is_controllable`) carry the testable logic; the HTTP methods
  only run inside request handlers, after config exists. Data path is REST
  polling; a live **WebSocket** subscription is the documented next step.
- **`routes_smarthome.py`** — `/api/home/*`. Every endpoint returns a graceful
  "not set up" shape when no provider is connected, so the tab always renders.

## API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/home/status` | any approved | feature/config/connection summary |
| GET | `/api/home/config` | admin | provider config + token **hint** (never the token) |
| POST | `/api/home/connect` | admin | store token (encrypted) + save config + probe + sync |
| POST | `/api/home/disconnect` | admin | forget provider + delete token |
| POST | `/api/home/sync` | admin | re-poll provider, refresh cache |
| GET | `/api/home/entities` | any approved | cached entities (+ `can_control` per device) |
| GET | `/api/home/rooms` | any approved | entities grouped by area |
| POST | `/api/home/entities/{id}/action` | admin, or per-entity grant | dispatch a normalized action |
| GET/PUT | `/api/home/entities/{id}/permissions` | admin | per-entity control grants |
| GET/POST/DELETE | `/api/home/favorites[/{id}]` | any approved | per-user pins |

## Security

- **LAN-only provider.** `require_lan_url()` rejects public hosts; the provider
  must be a private IP or `*.local`. This keeps control local and consistent
  with `installer/egress.sh` (the hub is egress-locked to the LAN; HA is
  reachable, the public internet is not).
- **Token custody.** The Home Assistant long-lived token is written to the
  **encrypted secret store** (`secrets_store`, namespace `smarthome`), 0600 key,
  Fernet-encrypted. No endpoint returns it — the most that leaks is a masked
  hint on the admin config view.
- **Permission model.** Reads are open to any approved device. Control requires
  admin **or** an explicit per-entity grant (`role:<role>` or `user:<username>`;
  a user grant overrides the role grant; default deny). This is the seed of the
  scoped "per-user device permissions" feature.
- **Cloud boundary.** Nothing here reaches the internet. The one future cloud
  path (push to a locked phone) is isolated in `PushBridge` and will egress via
  the gateway, not the hub.

## Tests

Offline, tmp-sqlite, no network:
- `tests/test_smarthome.py` — pure helpers: entity normalization, the
  action→HA-service map (on/off, brightness %, cover, lock, climate,
  unsupported), and the LAN-only URL guard (private/`.local` accepted, public
  rejected, override honoured).
- `tests/test_smarthome_store.py` — config round-trip (no token leak),
  connection health, wholesale entity-cache replace + domain filter + room
  grouping, permission precedence (user over role, default deny), favourites.

## Operational notes

- **Enable/disable:** `SMARTHOME_ENABLED` (default on). The feature is inert
  until an admin connects a provider — no outbound calls happen before that.
- **Connecting:** Home tab → *Connect* → HA LAN address + a long-lived token
  (HA → profile → Long-Lived Access Tokens). The hub probes `/api/` then syncs
  `/api/states` into the cache.
- **Egress interaction:** if the egress lock is active, HA on the LAN is
  reachable; a public URL is both rejected by `require_lan_url()` and blocked by
  the lock.

## Roadmap to 🟢

1. Live state via HA **WebSocket** (instant updates, no poll).
2. Area/room enrichment from HA's registry (the REST state API omits area).
3. **Voice + LLM control** — NL intent → normalized action (the seam exists).
4. Per-user device-permission **UI** (the store + checks exist).
5. `PushBridge` (APNs/FCM) with the native app shells — the cloud-by-exception
   path for locked phones, routed via the gateway.
