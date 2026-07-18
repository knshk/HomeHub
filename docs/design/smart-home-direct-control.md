# Smart Home — Direct Device Control (no Home Assistant)

Status: **living document · last updated 2026-07-18.** This supersedes the
Home-Assistant-centric approach: the skeleton in `smart-home.md` stands, and
this doc records the **pivot to direct control**. Nothing here is built yet —
it is the design the shipped `SmartHomeProvider` seam will grow into.

## 1. Status & intent

The skeleton in `smart-home.md` (commit `d1c79a8`) is real and correct: an
abstract `SmartHomeProvider` contract, a `smarthome_store` (sqlite config /
entity cache / per-entity permissions / favourites), `routes_smarthome`
(`/api/home/*`), and a Home tab in the SPA. That doc anchored the first
provider on **Home Assistant** — the fastest way to reach thousands of devices
from one long-lived token.

This document records a deliberate change of direction: **HomeHub will control
devices directly, without Home Assistant in the middle.** The abstraction that
`smart-home.md` reserved for "direct Matter/mDNS adapters later" becomes the
*primary* path, not a future footnote. `HomeAssistantProvider` demotes from
"the anchor" to an **optional, likely-dropped adapter** — a compatibility
bridge for a household that already runs HA, never a dependency HomeHub ships
around.

This is a living doc. The device catalog (§8) and the phasing (§12) will move
as adapters get scoped and built; a Changelog at the end tracks that. Where a
specific technical claim is not yet confirmed it is marked **(verify)** rather
than asserted — accuracy over bravado, as everywhere in these docs.

## 2. The decision & rationale — direct control, no Home Assistant

Home Assistant is excellent software and it is *the* reason the skeleton could
ship a working Home tab against ~2000 integrations from one token. The decision
to move off it is not about HA's quality; it is about what HomeHub is trying to
be — a **privacy-first appliance the household can fully reason about**. Three
reasons, in the user's own framing:

- **Isolation.** HA is a large third-party surface — its own web server, its
  own supervisor/add-on ecosystem, its own auth, its own update cadence, its
  own attack surface — sitting inside the trust boundary HomeHub is supposed to
  own. HomeHub **cannot lock it down** the way it locks down its own services
  (`installer/egress.sh` per-service eBPF egress, the Fernet secret store, the
  approval model). Putting the whole home behind a box HomeHub can't fully
  constrain undercuts the point of an appliance.
- **No per-integration treadmill.** HA's breadth is bought with continuous
  maintenance — integrations chase vendor cloud API changes, breaking-change
  releases, add-on churn. Depending on HA means inheriting that treadmill at
  one remove: when an integration breaks, HomeHub's Home tab breaks, and the
  fix lives in someone else's release. Owning a **curated, small** adapter set
  is less coverage but a maintenance surface HomeHub controls.
- **Privacy — traffic never leaves the LAN or transits a third party.** With HA
  in the middle, every command and every state update flows hub → HA → device.
  Direct control removes the middle hop entirely: hub → device, on the LAN,
  full stop. For the brands HomeHub supports, **device traffic never leaves the
  home network and never transits a third party** — not HA, and (by design, see
  §9/§10) not a vendor cloud. That is the whole point of going direct.

The tradeoff is stated plainly in §10 and is not hidden: **narrower coverage in
exchange for real isolation and no dependency treadmill.**

## 3. The fundamental challenge — protocol fragmentation

There is no single "control any device" API. This is the hard truth the whole
smart-home space is built on, and it is exactly the gap Home Assistant fills.

A "smart home" is a dozen incompatible ecosystems wearing one label. A Shelly
relay speaks HTTP/JSON-RPC over the LAN; a LIFX bulb speaks a published binary
UDP protocol; a Hue bulb speaks Zigbee to a bridge that then speaks HTTPS+SSE; a
Matter lock speaks CASE-encrypted sessions reached through a Thread border
router; a SwitchBot curtain speaks reverse-engineered BLE GATT; a Ring doorbell
speaks *only* to Amazon's cloud. There is no common wire format, no common
discovery mechanism, no common auth model, and no common state/notification
channel across them.

Home Assistant's ~2000 integrations exist precisely because **each ecosystem
needs its own adapter** — its own transport, its own discovery, its own
pairing, its own quirks. HA's value is the aggregation of two thousand of those.
Going direct means HomeHub writes and maintains a **small, curated subset** of
those adapters itself. This doc's job is to map the terrain (§6–§8) so the
subset is chosen for maximum privacy-preserving coverage per unit of
maintenance, and to be honest (§10) that it will never be all of it.

## 4. How this evolves the existing skeleton

The pivot **keeps almost everything** the skeleton shipped. The seam was drawn
in the right place; only what sits behind it changes.

- **`SmartHomeProvider` survives — it was the right abstraction.** The async
  contract (`test_connection` / `fetch_states` / `call_action`) does not care
  whether the backend is HA or a Shelly on the LAN. Instead of *one* provider
  (HA) we now have *many* providers/adapters behind the same contract — the
  "provider hybrid" was always meant to hold more than one implementation.
- **`HomeAssistantProvider` becomes optional / dropped.** It stays in the tree
  as a compatibility adapter for households already running HA (see §10), but
  it is no longer the anchor and no longer a shipped dependency.
- **Add a multi-adapter device registry + LAN discovery.** The new centre of
  gravity is a **device registry**: a normalized inventory of discovered and
  configured devices, each tagged with the adapter that owns it. Discovery
  extends the existing `discovery.py` (today it *advertises* the hub over
  zeroconf; it grows the ability to *browse* — mDNS service types, plus
  UDP-broadcast/SSDP/BLE probes per §6).
- **Reuse `smarthome_store`, `routes_smarthome`, the Home tab.** The store's
  tables (`sh_config`, `sh_entities`, `sh_permissions`, `sh_favorites`) already
  model configured backends, a wholesale-replaced entity cache, per-entity
  role/user permissions, and favourites — all of which apply unchanged to a
  registry of direct devices. The `/api/home/*` routes and the Home tab render
  the same normalized entities regardless of which adapter produced them.
- **Generalize `action_to_ha_service` into per-adapter dispatch.** The single
  translation seam from a normalized action dict to a concrete call becomes a
  **per-adapter dispatch**: each adapter maps `{"action": …, "params": …}` onto
  its own protocol (Shelly RPC, LIFX UDP frame, Matter cluster command, Zigbee
  command, BLE GATT write). The normalized action shape the UI and the future
  voice/LLM layer produce does not change.

**The three hybrids still frame it** — they simply generalize past HA:

1. **Provider hybrid** — *one interface, many backends.* Now literally many:
   one `SmartHomeProvider` contract, N per-protocol/per-vendor adapters.
2. **Local/cloud hybrid** — *LAN-first; cloud only for locked-phone push.*
   Direct control makes this *more* true: the control path is now hub → device
   with no third party at all. The only sanctioned cloud path remains the
   `PushBridge` stub — APNs/FCM to a locked/off-LAN phone, egressing via the
   **gateway**, never the hub (§9).
3. **Control hybrid** — *one normalized action shape, many producers.* The Home
   tab UI and the future voice+LLM intent layer both emit the same action dict;
   per-adapter dispatch is the only thing that changed underneath.

## 5. Architecture

```
 Home tab (SPA)          hub :80 / :443                         LAN devices
 ────────────     ┌──────────────────────────────────┐
  status  ──────► │ routes_smarthome  /api/home/*      │
  discover ─────► │  ├ smarthome_store (sqlite)         │
  device  ──────► │  │   config · entity-cache ·         │
  on/off  ──────► │  │   perms(role:/user:) · favourites │
                  │  ├ device registry                  │
                  │  │   (normalized inventory,          │
                  │  │    adapter-tagged, stable IDs)    │
                  │  │                                   │
                  │  ├ discovery ─────────────┐          │
                  │  │   mDNS/zeroconf browse  │  probe   │
                  │  │   SSDP/UPnP · UDP bcast  ├────────► │  (finds devices)
                  │  │   BLE scan · Matter comm.│          │
                  │  │                          │          │
                  │  └ adapters (SmartHomeProvider each) ─┼──► control / state
                  │      ├ local-IP   Shelly·Kasa·LIFX·   │     e.g.  HTTP/JSON-RPC
                  │      │             WLED·WiZ·Tapo·…     │           UDP/TCP binary
                  │      ├ bridge     Hue·Lutron·Bond·… ──┼──► vendor hub on LAN
                  │      ├ matter     MatterProvider ─────┼──► matter controller
                  │      │             (WebSocket)         │     sidecar → Matter/Thread
                  │      ├ radio      zigpy / zwave-js ────┼──► USB coordinator → mesh
                  │      └ ble        bleak / BlueZ ───────┼──► BLE GATT / advertisements
                  │                                        │
                  │   device keys ◄─ secrets_store (Fernet, 0600)
                  │   PushBridge (stub) ───────────────────┼──► APNs/FCM (future,
                  └──────────────────────────────────────┘      via gateway egress)

 egress lock (installer/egress.sh): hub egress pinned to 192.168.0.0/16.
 Every control/state arrow above stays on the LAN. The gateway is the only
 sanctioned cloud path, used solely by the future PushBridge.
```

Two cross-cutting facilities the diagram leans on:

- **Secret store** — any device secret or local key (a Nanoleaf token, a
  Lutron TLS cert+key PEM pair, a Tapo/TP-Link account, a Tuya per-device key, a
  Matter fabric root, a Zigbee/Z-Wave network key) lands in the **encrypted
  Fernet secret store** (`secrets_store.py`, 0600 key), one namespace per
  ecosystem — exactly as the HA long-lived token does today (§9).
- **Egress lock** — `installer/egress.sh` pins hub/ollama/voice egress to the
  LAN via per-service systemd eBPF. Every adapter's traffic is LAN-only, so the
  lock and direct control reinforce each other: a device that would phone home
  is both unsupported by design *and* blocked by the lock (§9).

## 6. Transport & discovery taxonomy

Direct control means owning discovery — HomeHub can no longer let HA find
things. The mechanisms, and where each shows up in the catalog:

- **mDNS / zeroconf (DNS-SD).** The workhorse. Devices advertise service types
  the hub browses: `_shelly._tcp`, `_wled._tcp`, `_hue._tcp`, `_lutron._tcp`,
  `_esphomelib._tcp`, `_nanoleafapi._tcp`, `_elg._tcp`, `_matterc._udp` /
  `_matter._tcp`, and more. Extends `discovery.py` from advertise-only to
  browse.
- **SSDP / UPnP multicast.** SSDP-style discovery on `239.255.255.250` — used
  by Yeelight (`:1982`) and by some UPnP-ish gear.
- **UDP broadcast probes.** Vendor-specific broadcast-and-listen: Kasa/UDP 9999,
  Tapo/UDP 20002, LIFX GetService/UDP 56700, WiZ/UDP 38899, Tuya/UDP 6666-6667,
  Magic Home/UDP 48899, Broadlink/UDP. Each is a small per-ecosystem prober.
- **BLE scan.** Bluetooth passive/active scan for BLE-local devices (SwitchBot,
  August/Yale, Xiaomi sensors) and for fresh Matter devices awaiting
  commissioning (BlueZ / `bleak`).
- **Matter commissioning.** A device advertises as commissionable
  (`_matterc._udp` or a BLE beacon); onboarding needs the 11-digit setup code /
  QR (passcode + discriminator), typically over BLE for a fresh device, then it
  becomes an operational node on the fabric (`_matter._tcp`).
- **Cloud-key-then-local ("semi-local").** A distinct pattern, not really
  discovery: the device is fully LAN-controllable, but the *key* to talk to it
  is fetched once from the vendor cloud (Tuya per-device local key, Tapo/KLAP
  account-derived session, Roborock `local_key`, encrypted-BLE-lock offline
  keys). This needs a documented **semi-local provisioning window** — a brief,
  deliberate egress to harvest keys, after which the egress lock re-seals and
  all control stays local (§9, §11).

Discovery is always **fail-safe and additive**: a device the hub can't
auto-discover can be added by IP, and mDNS + a stable device ID (not the IP)
are what the registry pins identity to, so DHCP churn doesn't lose a device
(§11).

## 7. Auth / commissioning models

Direct control means HomeHub owns pairing too. The models, easiest to hardest:

- **None.** Credential-free LAN devices — Shelly (default), LIFX, WLED, WiZ,
  Elgato, legacy Kasa, Magic Home. Discover and control; nothing to store.
- **Local token / credentials (minted on the device).** A button-press or API
  call mints a long-lived local token or password stored in the secret store:
  Nanoleaf (`POST /api/v1/new` after a 5-7s hold), Hue (link-button →
  application key), IKEA DIRIGERA (action button → bearer token), Bond (device
  token), ESPHome Noise PSK, optional Shelly/WLED/Tasmota passwords. **No cloud
  account** for any of these.
- **Local mutual-TLS cert pair.** Lutron Caséta/RA2 issues a **client TLS
  cert + key** on button-press pairing; the secret store holds the PEM key *and*
  cert (not a token). Broadlink does a device auth handshake → per-session key.
- **Cloud-key extraction (semi-local).** A one-time login to the vendor cloud
  harvests a local key, then control is fully local: Tuya (per-device local key
  via a Tuya IoT developer account), Tapo/KLAP (account email+password derives
  the local session), Roborock (`local_key`), iRobot (BLID+password via the
  `getpassword` handshake), encrypted BLE locks (offline key + slot). Requires
  the semi-local provisioning window (§6, §9). Keys can rotate on re-pair (§11).
- **Matter QR + BLE.** Per device: scan a QR or type the 11-digit setup code,
  commission over BLE, device joins the fabric. The controller sidecar owns the
  fabric root CA and issues NOCs — as sensitive as any provider key, backed up
  separately (§9). Clunkier than pasting one HA token, per device.
- **Zigbee / Z-Wave pairing & inclusion.** Deliberate, hardware-gated. Zigbee:
  "permit join" (~60-250s) + device interview; Zigbee 3.0 install codes on some.
  Z-Wave: inclusion + a 5-digit **DSK PIN** for S2 (mandatory-in-spirit for
  locks); SmartStart QR on 700/800-series. The coordinator holds the AES-128
  network key — back it up or lose the mesh on stick replacement (§11).

## 8. Device & brand handling catalog

This is the verified, adversarially fact-checked catalog the direct-control
plan is scoped against. **Scope note repeated for honesty:** the shipped
skeleton implements only `HomeAssistantProvider`; every device below is
*already* reachable today *indirectly* through HA. The
`local-adapter-now / bridge-adapter / matter-controller` verbs describe a
**future direct adapter feasible under the `SmartHomeProvider` seam** — not code
that ships today. Handling verbs and privacy verdicts follow this repo's
established vocabulary (`local-default, cloud-by-exception`; `(verify)` on
uncertain claims).

### 8.1 Handling buckets — what each is, what HomeHub needs, privacy verdict

**A — Local-IP adapter now (Wi-Fi/Ethernet LAN API, no hub, no extra hardware).** Devices with a documented or well-known local IP API reachable directly over the home network — Shelly, LIFX, WLED, WiZ, Elgato, ESPHome/Kauf/Athom, legacy Kasa, Nanoleaf, Magic Home, plus the semi-local cases (Tapo, Tuya) and ONVIF/RTSP cameras. HomeHub needs a set of small per-ecosystem async adapters behind the existing `SmartHomeProvider` contract — the seam `smart-home.md` already reserved for "direct Matter/mDNS adapters." `require_lan_url()` already permits these (private/`.local`), discovery extends `discovery.py`'s zeroconf plus UDP-broadcast/SSDP probes, and any secret (Noise PSK, Nanoleaf token, Tapo/TP-Link account, Tuya per-device key) lands in the Fernet secret store under a per-ecosystem namespace. **Privacy: excellent** for the credential-free set; **good/conditional** where a cloud account seeds the local session (Tapo, Tuya) or an app toggle is required (Yeelight). Nothing here egresses once configured — it fits the egress lock with no cloud carve-out.

**B — First-party bridge-local adapter (a vendor hub on the LAN with a local API).** Hue Bridge (CLIP v2 REST + SSE), Lutron Caséta (LEAP mutual-TLS), Bond (REST + UDP push), Broadlink (AES UDP blaster), IKEA DIRIGERA (HTTPS + websocket). A distinct middle ground: not cloud, not Home Assistant — a documented (or stable reverse-engineered) local API on a vendor box. HomeHub needs one `bridge-adapter` `SmartHomeProvider` per bridge; several are push-capable (Hue SSE, Lutron/Bond/Dirigera events), so they satisfy the WebSocket/live-state roadmap item **without** HA. The secret store must hold more than tokens: Lutron stores a **TLS cert+key PEM pair**, Broadlink a per-device session key, the rest bearer tokens. **Privacy: good** (a vendor box on your LAN; remote/Alexa features are cloud opt-in, but the control path is local).

**C — Matter / Thread (controller sidecar; Thread also needs a border router).** The standards-based, local-by-design path: Matter over Wi-Fi (Tapo/Meross/Govee/Sengled/LIFX/WiZ Matter), Matter over Thread (Eve, Nanoleaf, newer Aqara, Tapo Thread, Matter locks), and Matter bridges (Hue, Dirigera, Aqara/SwitchBot hubs). This lands as a second `SmartHomeProvider` — a `MatterProvider` talking to a **Matter controller sidecar over a LAN WebSocket**, same normalized action dict and routes. The load-bearing cost is not adapter code but the runtime: a controller is mandatory and is **not** an in-process pip lib — run a **Matter controller sidecar** (either `python-matter-server`, the established Home Assistant/Nabucasa controller, or a newer matter.js-based server) as its own systemd service beside the hub. *(Which controller and exact versions to pin at scoping — this space moves quickly; see §13.)* It owns the **fabric root CA and issues NOCs** to devices — as sensitive as any provider key; back it up separately (losing it orphans the fabric). Commissioning needs a **BLE radio** (scan a QR / type an 11-digit code per device — clunkier than HA's paste-a-token). **Matter-over-Thread additionally needs a Thread Border Router**: run your own OTBR (SkyConnect/ZBT-1 or nRF52840 + OpenThread), because commercial BRs (HomePod mini, Apple TV, Nest) are closed. **Privacy: excellent** — CASE-encrypted, no vendor cloud in the control path, no egress carve-out; residual caveat is some vendors' parallel cloud for firmware. Coverage lags native integrations (bridged devices expose only mapped clusters), so keep HA as the broader fallback — complementary, not either/or.

**D — Zigbee / Z-Wave (USB radio coordinator — hardware).** The strongest privacy transport, but hardware-gated: nothing works without a physical USB coordinator — a Zigbee dongle (~$20-40) and/or a **region-locked** Z-Wave stick (~$25-50, frequency fixed at manufacture, must match the country). Onboarding is deliberate pairing (Zigbee "permit join" + interview; Z-Wave inclusion + a 5-digit DSK PIN for S2), not auto-discovery. Three paths, honesty-ordered: **(1)** if the household already runs HA on the LAN, both radios arrive for **free** as HA entities via the shipped `HomeAssistantProvider` — zero new radio code, dongle lives on the HA box; **(2)** for a HA-free appliance, Zigbee via **zigpy** (pure-Python, in-process, HA's ZHA stack) or **zigbee2mqtt** (widest device support, + Node + MQTT broker); **(3)** Z-Wave has no pure-Python option — node-zwave-js + zwave-js-server (Node) driven by the `zwave-js-server-python` WebSocket client, so Z-Wave inherently drags in a Node runtime. Chipset picks the zigpy radio lib (bellows/zigpy-znp/zigpy-deconz). **Privacy: excellent** (local mesh, no vendor cloud once paired) — the reason to bother with the hardware.

**E — Cloud-required → unsupported by design.** Ring, Google Nest (SDM), Arlo, Ecobee, Wyze, Emporia (stock), Meross (stock), most Govee, and cloud cameras/doorbells as a class: control and often video round-trip a vendor cloud with an account + 2FA, on fragile unofficial libraries. These contradict the appliance's stance and are excluded as first-party targets (steer families to ONVIF/RTSP cameras and to reflashing Emporia/Sonoff). They remain reachable **indirectly** through HA if a household insists. **Privacy: poor(cloud).** The important nuance is the **semi-local subset** — Tuya, Roborock, iRobot older Roombas (and the encrypted BLE locks in F) are **conditional**, not poor: they control fully on-LAN/BLE after a **one-time** cloud key/password pull. That implies a distinct onboarding mode — a **provisioning window where egress to the vendor cloud is briefly allowed to harvest keys, then the egress lock re-seals** and all control stays local. This does not fit behind a permanently-sealed lock the way HA does; it deserves its own documented "semi-local provisioning" flow.

**F — BLE-local (appliance BLE radio, no remote without a hub/cloud).** SwitchBot, August/Yale locks, and cheap BLE sensors (Xiaomi/Mijia, Qingping) controlled/read directly over Bluetooth by the appliance (BlueZ/`bleak`) with nothing leaving the room. HomeHub needs a **BLE radio on the box** (or BLE-proxy nodes) — the ~10 m range forces physical proximity, and one radio saturates with many devices; passive sensors need a per-model advertisement parser. Encrypted locks (SwitchBot Lock, August/Yale) are **semi-local**: a one-time offline-key pull from the vendor account (same provisioning-window pattern as E), then fully local BLE — good for a lock since there's no cloud in the critical path. **Privacy: excellent** for listen-only sensors, **good** for the locks; **no remote access** without the vendor hub/cloud. SwitchBot Hub 2/Mini can alternatively surface these as a Matter bridge (Bucket C).

### 8.2 The catalog

Columns are consistent across all six bucket tables: **Category · Example brands · Transport · Local vs Cloud · Discovery · Auth & pairing · HomeHub handling · Privacy · Key caveats.** Tier-2/3 credentials map onto the encrypted Fernet secret store (`secrets_store`, one namespace per ecosystem), exactly like the HA long-lived token.

#### Bucket A — Local-IP adapter now (Wi-Fi/Ethernet LAN API, no hub, no extra hardware)

| Category | Example brands | Transport | Local vs Cloud | Discovery | Auth & pairing | HomeHub handling | Privacy | Key caveats |
|---|---|---|---|---|---|---|---|---|
| Relays / switches / dimmers / plugs / sensors | Shelly Gen1 (1, 1PM, 2.5, Dimmer2, Plug S, H&T) **and** Gen2/3/4 (Plus/Pro/Mini) | Gen1: HTTP REST (/relay,/status,/settings) + CoAP/CoIoT UDP 5683 push. Gen2+: JSON-RPC 2.0 over HTTP /rpc + **outbound WebSocket push**; opt MQTT on both | local | mDNS `_shelly._tcp` / `_http._tcp` + CoIoT multicast | none by default; opt HTTP Basic (Gen1) / digest (Gen2+) | local-adapter-now | excellent | One brand, two generations — `aioshelly` covers both by generation flag. WebSocket push (Gen2+) drives a live Home tab without polling. Cleanest local target. **lib: aioshelly** |
| Smart plugs / bulbs / switches / strips | TP-Link **Kasa** (HS100/103/105/110, KP115/125, KL130, HS300, EP-series) | Local TCP 9999, TP-Link "smarthome" JSON with rolling-XOR (autokey 0xAB); UDP 9999 discovery | local (legacy) / semi-local (KLAP-migrated newer HW) | UDP broadcast 9999 | none (legacy); **newer KLAP-migrated units need TP-Link account creds** (verify per hw/fw rev) | local-adapter-now | excellent (legacy) | `python-kasa` auto-detects transport. XOR is obfuscation, not security. Some newer Kasa hardware/firmware moved to KLAP (cloud creds for the local handshake) — check the rev. **lib: python-kasa** |
| Smart plugs / bulbs / switches / cameras | TP-Link **Tapo** (P100/110/115, L510/530/900, C-series cams, S-series) | Local **KLAP** (AES-128-CBC + HMAC handshake) over HTTP; UDP 20002 discovery; cams via Tapo/ONVIF/RTSP | **semi-local** (cloud creds → local session) | UDP broadcast 20002 (authenticated `Device.update()` for full info) | **TP-Link (Tapo) account email+password** used to derive the local session (store in secret store) — *all Tapo devices require auth* | local-adapter-now | good | Control is LAN-local; creds are not round-tripped to cloud. TP-Link's 2023 AES→KLAP switch has broken libs on firmware bumps — the single most consequential moving target. Newer Tapo also ship **Matter/Thread** → see Bucket C. **lib: python-kasa (SMART/Tapo); plugp100; PyTapo (cams)** |
| Smart bulbs / strips | **LIFX** (A19/BR30, Z/Beam, Tile, Candle) | **Published** LIFX LAN Protocol — binary UDP 56700 | local | UDP broadcast GetService 56700 → StateService reply | none | local-adapter-now | excellent | One of the few vendors with an officially published LAN protocol; fully offline. Some newer bulbs are also Matter → Bucket C. **lib: aiolifx (async); lifxlan** |
| DIY / ESP LED controllers | **WLED** (any ESP8266/ESP32; QuinLED, Athom WLED) | HTTP JSON (/json/state,/json/info) + WebSocket push + realtime UDP (DRGB/DNRGB/WARLS) and DDP/E1.31/Art-Net | local | mDNS `_wled._tcp` | none (opt settings-UI PIN) | local-adapter-now | excellent | Open-source firmware, entirely local, well-documented JSON API; realtime UDP ideal for effects/notifications. Ideal target. **lib: wled** |
| Smart bulbs / plugs / bars | **WiZ** (Philips-owned; A19/filament, plugs, bars) | JSON over **UDP 38899** (getPilot/setPilot); UDP 38900 push/heartbeat | local | UDP broadcast 38899 | none | local-adapter-now | excellent | Undocumented-but-stable local UDP; fully offline once on Wi-Fi. Some models also Matter → Bucket C. **lib: pywizlight** |
| Smart bulbs / strips / ceiling lamps | **Yeelight** (Xiaomi/Yeelight; Color/White, Lightstrip, ceiling) | TCP 55443 line-based JSON-RPC; SSDP-style multicast | local — **only if the "LAN Control"/Developer Mode toggle is enabled per device** | SSDP multicast 239.255.255.250:1982 | none, **but the LAN Control toggle must be ON in the Yeelight/Xiaomi Home app first** | local-adapter-now | good | Silent failure if the toggle is off — the biggest gotcha. Xiaomi Home app migration can hide the toggle / push you to the miIO path (verify per model/region). **lib: python-yeelight (provides async `AsyncBulb`)** |
| RGBW panels / lines / shapes | **Nanoleaf** (Light Panels/Shapes/Elements/Lines, Canvas, Essentials) | Local HTTP OpenAPI :16021 with per-controller token; UDP streaming | local | mDNS `_nanoleafapi._tcp` | hold power 5-7s → `POST /api/v1/new` mints a long-lived token (secret store) | local-adapter-now | excellent | Officially documented local API. **Essentials + 2024 panels also speak Thread/Matter** → alternative path in Bucket C. **lib: aionanoleaf; nanoleafapi; pynanoleaf** |
| Streaming / studio lights | **Elgato** Key Light / Air / Light Strip / Ring Light | Local HTTP REST :9123 (/elgato/lights) | local | mDNS `_elg._tcp` | none | local-adapter-now | excellent | Entirely local, no account/token — PUT brightness/temperature to the IP. Among the simplest integrations. (The studio-light maker — unrelated to "Ring" doorbells.) **lib: elgato; leglight** |
| DIY firmware framework (plugs/sensors/switches/climate/covers) | **ESPHome** (any ESP/RP2040; **Athom**, **Kauf**, many vendors preflash it) | Native API — protobuf over TCP 6053, opt **Noise (Curve25519)** encryption, **push** state stream; MQTT optional | local | mDNS `_esphomelib._tcp` | Noise PSK (preferred) or API password (opt; secret store) | local-adapter-now | excellent | Gold-standard local protocol: persistent push, no polling, no cloud/account. Athom & Kauf ship preflashed ESPHome (branded ESPHome devices). Entity set = whatever the YAML defines. **lib: aioesphomeapi** |
| DIY firmware (plugs/bulbs/switches/sensors) | **Tasmota** (flashed onto Sonoff/**Athom**/generic ESP) | HTTP command API (/cm?cmnd=) + **MQTT** (idiomatic); Berry/rules | local | MQTT discovery topic (needs broker); limited mDNS; else HTTP scan | opt web-UI password; MQTT broker creds if used | local-adapter-now (MQTT path = bridge-adapter, needs a broker) | excellent | HTTP cmnd works standalone; MQTT is the idiomatic path and needs a local Mosquitto. No first-party async Python lib. **lib: none official — aiohttp (HTTP) or asyncio/paho MQTT** |
| Switches / plugs / relays (**split case**) | **Sonoff / ITEAD** (Basic, Mini, S31, TH, POW, ZBMini) | Stock: eWeLink cloud (Coolkit MQTT/HTTPS). **DIY Mode: local HTTP + mDNS.** Reflashed Tasmota/ESPHome: fully local | **split** — cloud (stock) / local (DIY or reflashed) | DIY mode: mDNS `_ewelink._tcp`; stock: cloud enumeration | stock: eWeLink account; DIY: none over LAN; reflashed: per-firmware | bridge-adapter (stock) → local-adapter-now (DIY / reflashed) | conditional | Stock talks to eWeLink cloud (poor). DIY Mode gives a limited local HTTP+mDNS API on *some* SKUs; reflashing Tasmota/ESPHome is the clean local answer. Newer firmware has locked DIY mode on some SKUs. **lib: pysonofflan/sonoff-lan (DIY, aging — verify); aioesphomeapi (reflashed)** |
| Generic Tuya/Smart Life Wi-Fi (huge OEM base) | **Tuya / Smart Life Wi-Fi** (Teckin, Gosund, Treatlife, Lonsonho, LSC, many no-names) | Local **TCP 6668**, AES-encrypted Tuya protocol (v3.1–3.5); UDP 6666/6667 encrypted discovery | **semi-local** (per-device local key from cloud once → LAN-only) | UDP broadcast 6666 (v3.1) / 6667 (v3.3+); else pull from Tuya cloud | **per-device local key** extracted once via a Tuya IoT developer account, then local | local-adapter-now (after one-time key pull; needs a provisioning egress window) | conditional | Massive ecosystem, fully offline after keying. Keys rotate if re-paired; **v3.4/3.5 crypto changes broke older libs**; some newer devices force cloud. **lib: tinytuya; localtuya (HA)** |
| Magic Home / Flux LED Wi-Fi controllers | **Magic Home / MagicLight / Flux / LEDENET / Zengge** OEM RGB(W) | Local **TCP 5577** binary protocol; UDP 48899 discovery/config | local | UDP broadcast 48899 (HF-A11ASSISTHREAD probe) | none | local-adapter-now | good | Long-standing reverse-engineered local protocol, fully offline. Byte-frame command set varies across Zengge OEM variants/firmware; newer "Zengge Mesh"/BLE variants differ. **lib: flux_led** |
| Preflashed local Wi-Fi plugs/bulbs/strips | **Athom** / **Kauf** (preflashed ESPHome or Tasmota) | Reduces to the ESPHome (TCP 6053) or Tasmota (HTTP/MQTT) row per SKU | local | ESPHome mDNS `_esphomelib._tcp` / Tasmota MQTT | ESPHome Noise PSK or none; Tasmota opt web pw | local-adapter-now | excellent | Ships local-first, zero cloud/account out of the box. Handling = the ESPHome or Tasmota row depending on firmware ordered. **lib: aioesphomeapi or MQTT/HTTP** |
| IP cameras (local) | **ONVIF/RTSP** cams (Reolink, Amcrest, Hikvision/Dahua OEM, Axis; some Reolink doorbells) | RTSP video (TCP/UDP 554) + ONVIF SOAP/HTTP control (PTZ/events/snapshots) | local | ONVIF **WS-Discovery** (UDP multicast 3702); manual RTSP URL | per-camera user/password (Digest); recommend a camera VLAN with no internet | local-adapter-now | excellent | The privacy-preferred camera path — video/events stay on the LAN. **Firewall cameras off the internet** (many phone home). RTSP decode needs an external transcoder (ffmpeg/go2rtc), not pure-Python; low-latency live view wants WebRTC via go2rtc (verify). **lib: onvif-zeep / onvif-zeep-async + ffmpeg/go2rtc** |

#### Bucket B — First-party bridge-local adapter (a vendor hub on the LAN with a documented/stable local API)

| Category | Example brands | Transport | Local vs Cloud | Discovery | Auth & pairing | HomeHub handling | Privacy | Key caveats |
|---|---|---|---|---|---|---|---|---|
| Lighting bridge (Zigbee behind it) | **Philips Hue Bridge v2** (+ bulbs/sensors) | Local HTTPS REST **CLIP v2** (/clip/v2) + **SSE eventstream** push; Zigbee down to bulbs | bridge-local | mDNS `_hue._tcp` + N-UPnP (discovery.meethue.com) fallback | press the physical **link button** → POST mints an application key (bearer token) — **no cloud account for local control** | bridge-adapter | good | Genuine local API with instant push (SSE) — no cloud round-trip. Self-signed cert (pin it); remote/Alexa features are cloud opt-in. **Alt paths:** Hue bulbs also pair to a generic Zigbee coordinator (Bucket D); the bridge can be exposed as a **Matter bridge** (Bucket C — on/off/dim/color only, no scenes/Entertainment). **lib: aiohue** |
| Dimmers / switches / shades / remotes | **Lutron Caséta / RA2 Select** | **LEAP** over TLS :8081 (push events) | bridge-local | mDNS `_lutron._tcp` | button-press pairing issues a **client TLS cert + key pair** (mutual TLS) — secret store holds PEM key **and** cert | bridge-adapter | good | Rock-solid local LEAP, push, no cloud for control. Auth is a cert pair, not a token. Strong recommend. **lib: pylutron_caseta** |
| Ceiling fans / fireplaces / IR-RF gear / shades | **Bond Bridge** | Local HTTP REST + **UDP BPUP** push channel | bridge-local | mDNS `_bond._tcp` | local API token on the device (v2 firmware) | bridge-adapter | good | Makes RF/IR fans & fireplaces controllable. IR/RF is **open-loop** — optimistic state, no real feedback. Vendor box on the LAN. **lib: bond-async; bond-api** |
| Universal IR/RF blasters (+ some plugs) | **Broadlink** RM/SP/MP | Local AES-encrypted **UDP** (port 80 discovery + command) — the blaster *is* the bridge to dumb IR/RF gear | bridge-local | UDP broadcast | device "auth" handshake → per-session key; learning mode captures codes locally | bridge-adapter | good | Fully local, no cloud once learned. Same open-loop/optimistic-state caveat as Bond. Newer firmware occasionally tightens local auth. **lib: python-broadlink** |
| Zigbee/Matter hub (TRÅDFRI successor) | **IKEA DIRIGERA** (bulbs/plugs/blinds/sensors) | Local HTTPS REST :8443 + websocket events | bridge-local | mDNS `_ihsp._tcp`; manual IP | press the action button → request a bearer token — no cloud account for local control | bridge-adapter | good | Local REST + websocket. Lib is unofficial/reverse-engineered (IKEA firmware can shift the schema). **Also a Matter controller + Thread Border Router** (Bucket C). Older TRÅDFRI gateway used CoAP/DTLS (`pytradfri`). **lib: dirigera (unofficial)** |

#### Bucket C — Matter / Thread (standards-based, local-by-design; needs a controller sidecar; Thread also needs a border router)

| Category | Example brands | Transport | Local vs Cloud | Discovery | Auth & pairing | HomeHub handling | Privacy | Key caveats |
|---|---|---|---|---|---|---|---|---|
| Matter over **Wi-Fi/Ethernet** | Tapo Matter, Meross Matter, Govee Wi-Fi Matter, Sengled, LIFX, WiZ, select Nanoleaf/SwitchBot, "Works-with-Matter" plugs | Wi-Fi/Ethernet IPv6/IPv4; **CASE-encrypted** operational sessions (UDP/TCP); BLE only for first-time commissioning | local | mDNS `_matterc._udp` (commissionable) + `_matter._tcp` (operational); BLE scan for fresh devices | 11-digit numeric setup code / QR (passcode + discriminator); BLE onboarding, or on-network commissioning if already on Wi-Fi | matter-controller | excellent | Simplest Matter subtype — **no Thread Border Router needed**. Requires the controller runtime (below). Wi-Fi Matter = mains-powered/heavier (not battery sensors). **Controller = a separate sidecar service** (own systemd unit, WebSocket API), not an in-process pip lib. **controller: `python-matter-server` (established) or a matter.js-based server, over WebSocket — pick + pin at scoping (verify)** |
| Matter over **Thread** | Eve (all Thread), Nanoleaf Thread, newer Aqara (Thread sensors/locks via Hub M3), Onvis, Third Reality, Tapo Thread, Yale/Aqara Matter locks, some Govee Thread | Thread (IEEE 802.15.4 / 6LoWPAN / IPv6 mesh) reached via a **Thread Border Router**; BLE for commissioning, then Thread operational creds handed over | **local (hardware dependency: a Thread BR on the LAN)** | mDNS advertised across the BR's SRP→mDNS backbone; BLE scan for commissioning | QR / 11-digit setup code + BLE onboarding; device receives Thread network credentials | matter-controller | excellent | **Requires a Thread Border Router.** HomeHub should run its **own OTBR** (802.15.4 dongle: SkyConnect/ZBT-1 or nRF52840 + OpenThread) — commercial BRs (HomePod mini, Apple TV 4K, Nest Hub 2, Nest WiFi Pro, Google TV Streamer) route Thread but are **closed** to third-party controllers. 2 BRs recommended for mesh resilience. Same controller stack as Wi-Fi Matter. |
| Matter **bridges** (Zigbee/BLE/proprietary hub → Matter) | **Hue Bridge v2**, **IKEA Dirigera** (Matter bridge + controller/BR since fw 2.805.6), **Aqara Hub M3/M100/M2**, **SwitchBot Hub 2/Mini**, Meross hub | Bridge speaks Matter over Wi-Fi/Ethernet to the controller and exposes each leaf as a **bridged endpoint**; talks Zigbee/BLE/proprietary down to leaves | bridge-local (no cloud for the Matter path) | Bridge = one commissionable Matter node advertising multiple bridged endpoints (Bridged Device Basic Information cluster per child) | bridge commissioned with a QR/setup code; child devices onboarded in the vendor app, then surface as bridged endpoints | matter-controller | good | Bridges expose only **mapped Matter clusters** — e.g. Hue over Matter = on/off/dim/color, **not** scenes/Entertainment/effects; vendor-specific automations don't cross. Some hubs still want their own cloud/app for firmware & initial setup. |
| **Thread Border Router** (infrastructure, not a device class) | DIY OTBR: RPi + nRF52840 or HA SkyConnect / Connect ZBT-1 running `otbr-agent`; Commercial: HomePod mini/Apple TV 4K, Nest Hub 2/Nest WiFi Pro/Google TV Streamer, eero | Bridges the Thread 802.15.4 mesh ↔ Wi-Fi/Ethernet; runs SRP + mDNS backbone so Thread nodes are discoverable | local (DIY fully local; commercial route Thread locally but are closed to 3rd-party controllers) | N/A — it *is* the discovery backbone (SRP→mDNS advertisement) | N/A — holds the Thread network dataset (network key) commissioned devices receive | radio-coordinator(hardware) | good | Only relevant for the Thread subtype. Run your **own** OTBR (SkyConnect/ZBT-1 + OpenThread) to stay independent of Apple/Google/Amazon; 2 BRs recommended. Same 802.15.4 dongle class the roadmap already earmarks for Zigbee. **lib: otbr-agent (OpenThread); HA ships an OTBR add-on as reference** |

#### Bucket D — Zigbee / Z-Wave (low-power mesh; **hardware-gated** — needs a USB radio coordinator)

| Category | Example brands | Transport | Local vs Cloud | Discovery | Auth & pairing | HomeHub handling | Privacy | Key caveats |
|---|---|---|---|---|---|---|---|---|
| **Zigbee coordinator** (USB dongle) — the hardware gate | Sonoff ZBDongle-E (EFR32MG21) / ZBDongle-P (CC2652P); HA SkyConnect / Connect ZBT-1 (EFR32MG21) & ZBT-2 (EFR32MG24); dresden Conbee II/III; TI CC2652 | USB-serial to host; radio side = IEEE 802.15.4 / Zigbee 2.4 GHz mesh | local | host enumerates a USB CDC-ACM port (/dev/ttyUSB*\|ttyACM*) | coordinator forms the network, holds the Trust Center; AES-128 network key at first form | radio-coordinator(hardware) | excellent | The whole gate: no dongle, no Zigbee (~$20-40). **Chipset dictates the radio lib** (load-bearing). Use a USB-2 extension cable — USB-3 ports emit 2.4 GHz noise. Network is bound to the stick — replacing it means re-pairing unless you back up/restore the network key. |
| Zigbee **stack** (pick one; only one may own the dongle) | native **zigpy** (in-process Python, HA's ZHA under the hood) · **zigbee2mqtt** (Node.js, widest device support, + MQTT broker) · **via existing Home Assistant** (reuses `HomeAssistantProvider`) | zigpy speaks the coordinator's serial protocol; z2m/HA front it with MQTT/REST | local (zigpy) / bridge-local (z2m, HA) | pairing = "permit join" (~60-250s) + factory-reset + Zigbee interview | AES-128 network key + Trust Center link key; Zigbee 3.0 install codes (QR/sticker) | radio-coordinator(hardware); z2m & HA paths = bridge-adapter | excellent | zigpy = pure-Python, fits the appliance, but gives a device graph not a product layer (quirks needed for oddballs). z2m handles quirky Tuya/Aqara best but adds a Node service + broker. **HA path = zero new radio code** (lowest effort). **lib: zigpy + bellows/zigpy-znp/zigpy-deconz · aiomqtt · existing HA provider** |
| Zigbee brands — mains lighting & plugs (best-behaved routers) | IKEA **Tradfri**, **Sonoff** Zigbee, **Third Reality**, **Innr**, **Ledvance** (+ **Hue bulbs** — detailed in Bucket B) | Zigbee 2.4 GHz mesh; mains devices act as routers/repeaters | local | reset-to-pair (Hue: power-cycle/touchlink; IKEA: 4× toggle); interviewed on join | Zigbee 3.0 / Zigbee Light Link; standard network + link keys | radio-coordinator(hardware) | excellent | Pair to a **generic** coordinator with **no vendor hub/cloud** — a Hue bulb needs no Hue Bridge, an IKEA bulb no Dirigera (you lose only vendor-app extras). Populate mains routers first; they are the mesh backbone. **lib: zigpy / z2m** |
| Zigbee brands — battery sensors & quirky vendors | **Aqara** (motion/contact/temp/vibration, cubes, **FP2** mmWave), **Tuya Zigbee** (white-label sensors/valves), **Sengled** bulbs (non-routing) | Zigbee 2.4 GHz; battery devices are **sleepy end devices** (do not route/heal) | local | long-press reset during permit-join; must be awake to interview (several button presses) | standard Zigbee keys; some Aqara ship install codes | radio-coordinator(hardware) | excellent | The trouble tier. **Aqara** often deviates from spec → device-specific quirks handlers; some models drop off non-Aqara coordinators or still want the Aqara hub for full features (verify per model); **newer Aqara also do Thread/Matter** → Bucket C. **Tuya Zigbee** uses non-standard DP encodings (per-device converters, break on firmware). Sengled bulbs are mains but deliberately non-routing. **lib: zigpy + zha-quirks / z2m converters** |
| **Z-Wave coordinator** (USB stick) — the hardware gate | Aeotec Z-Stick Gen5/Gen7 & Z-Pi 7; Zooz ZST10 700 / ZST39 800LR; HomeSeer SmartStick+/G8 | USB-serial to host; radio side = **sub-GHz region-specific** Z-Wave mesh | local | host enumerates a USB CDC-ACM port; stick is the primary controller | controller holds S0 + per-class S2 keys; SmartStart QR on 700/800-series | radio-coordinator(hardware) | excellent | Hardware gate (~$25-50) and **REGION-LOCKED**: frequency is fixed at manufacture (US 908.42 / EU 868.42 / ANZ / etc.) and cannot change — stick **and every device** must match the country. Prefer 700/800-series (S2, SmartStart, 800LR Long Range). Migrate via NVM backup/restore or re-include everything. |
| Z-Wave **stack** | node-zwave-js → **zwave-js-server** (Node.js WebSocket wrapper) ← **zwave-js-server-python** client on the appliance. *Legacy: OpenZWave / python-openzwave (archived, S0-only — do not use for a new build).* | USB stick → node-zwave-js; HomeHub talks WebSocket (ws://host:3000) | bridge-local | inclusion/exclusion pairing; S2 prompts for the 5-digit **DSK PIN** (or SmartStart QR) | S2 (AES-128 CCM, ECDH + DSK PIN) is the modern secure path; S0 legacy | radio-coordinator(hardware) | excellent | De-facto standard, fully local, but **drags in a Node runtime** — no pure-Python equivalent (asymmetry vs Zigbee's zigpy). S2 inclusion is fiddlier (type/scan the DSK); avoid mixing S0 (heavy crypto congests the mesh). **lib: zwave-js-server-python** |
| Z-Wave brands — switches / dimmers / sensors / locks | **Aeotec**, **Zooz**, **Fibaro**, **GE/Jasco** (now Enbrighten/Honeywell), **Inovelli**; **Yale/Kwikset/Schlage** Z-Wave locks | sub-GHz mesh; mains switches/dimmers = always-listening routers, battery sensors/locks = sleepy end nodes | local | per-device inclusion (button/triple-tap); locks & some sensors carry the DSK PIN | S2 recommended (mandatory-in-spirit for locks); S0 fallback for old GE/Jasco | radio-coordinator(hardware) | excellent | All fully local, no vendor cloud once included — a genuine Z-Wave strength. **Door locks MUST be S2-included**; include near the controller then mount (sleepy + finicky). Buy stick **and** every device in the same region/frequency. **lib: via zwave-js-server-python** |

#### Bucket E — Cloud-required → unsupported by design (contradicts the privacy stance)

| Category | Example brands | Transport | Local vs Cloud | Discovery | Auth & pairing | HomeHub handling | Privacy | Key caveats |
|---|---|---|---|---|---|---|---|---|
| Doorbells / cameras / alarm | **Ring** | Cloud REST + push; no documented local API | cloud-required | cloud account only | Amazon/Ring OAuth + 2FA | unsupported-by-design(cloud) | poor(cloud) | Every event & video round-trips Amazon's cloud; no local control path. Excluded on principle — steer families to an ONVIF/RTSP doorbell (Bucket A). **lib: ring-doorbell (unofficial)** |
| Thermostats / cameras / doorbells | **Google Nest** (via SDM API) | Cloud REST (Smart Device Management) + Pub/Sub events; camera streams via cloud-brokered WebRTC/RTSP tokens | cloud-required | Google Cloud project + Device Access | Google OAuth + a **paid ($5 one-time) Device Access** registration + GCP Pub/Sub | unsupported-by-design(cloud) | poor(cloud) | Cloud-only, gated behind a paid developer reg and periodic OAuth reconsent; Nest removed the old local Works-with-Nest paths. **lib: google-nest-sdm (unofficial)** |
| Battery cameras / doorbells | **Arlo** | Cloud REST + SSE/MQTT; streams brokered by Arlo cloud | cloud-required | cloud account only | Arlo account + 2FA (often needs IMAP OTP scraping) | unsupported-by-design(cloud) | poor(cloud) | Cloud-only; battery cams have no persistent local stream; the unofficial lib breaks on every auth change. **lib: pyaarlo (unofficial, fragile)** |
| Thermostats | **Ecobee** | Cloud REST (api.ecobee.com) | cloud-required | cloud account only | Ecobee developer app key + PIN-authorize OAuth | unsupported-by-design(cloud) | poor(cloud) | No local API; native API is cloud. Ecobee is a **HomeKit** accessory, so a HomeKit-controller path could be local (verify — aiohomekit). **lib: python-ecobee-api** |
| Cameras / plugs / bulbs / locks / sensors | **Wyze** | Cloud REST + cloud video; some cams RTSP only on special firmware | cloud-required | cloud account only | Wyze account + 2FA; API key now required | unsupported-by-design(cloud) | poor(cloud) | Cloud-first budget gear; the unofficial API is repeatedly locked down. Old RTSP-firmware cams were local but discontinued/unofficial. `docker-wyze-bridge` re-auths via cloud. **lib: wyzeapy (unofficial)** |
| Energy monitors / plugs / EV charger | **Emporia** (Vue Gen2/3, plugs, EV charger) | Cloud REST (AWS Cognito) | cloud-required (stock) | cloud account enumeration | Emporia cloud account (Cognito) | unsupported-by-design(cloud) | poor(cloud) | No supported LAN API. Local **only after reflashing the ESP32 to ESPHome** (voids the app, varies by hw rev) → then it's a Bucket A device (verify firmware maturity). **lib: pyemvue (cloud); aioesphomeapi after reflash** |
| Plugs / bulbs / garage / valves | **Meross** (MSS/MSL/MSG series) | Stock: cloud **MQTT (Meross's own broker) + HTTPS**. Local: reverse-engineered self-hosted-MQTT reflash hack | cloud-required (stock) / fragile bridge-local (MQTT-repoint hack) | cloud enumeration; local-MQTT uses the AP setup flow | Meross account (email+password); local-MQTT injects broker creds at setup | unsupported-by-design(cloud) — MQTT-repoint hack = fragile bridge-adapter | conditional | `meross-iot` is **cloud-oriented** (logs into Meross for MQTT creds), not a clean LAN client. True local means repointing the device's MQTT to your own broker — fiddly, firmware-fragile. **Newer Meross Matter models → Bucket C** (the clean local path). **lib: meross-iot** |
| LED strips / bulbs / lamps / sensors (**per-SKU lottery**) | **Govee** (H61xx strips, H60xx bulbs, Glide, Lyra, Immersion) | Default **cloud REST** (rate-limited). **LAN** UDP multicast 239.255.255.250 (4001 scan / 4002 recv / 4003 device) on a subset. **BLE GATT** on many models | cloud-required (default) / semi-local (LAN subset) / local (BLE subset) | LAN models answer a UDP multicast scan (app toggle first); BLE scan; else cloud | LAN: toggle in Govee Home app; cloud: Govee developer API key; BLE: per-model | unsupported-by-design(cloud) by default; **LAN-toggle models → Bucket A**; **BLE models → Bucket F** | conditional | Per-SKU lottery — verify locality per model, not per brand. LAN API is deliberately minimal (on/off/brightness/color/colorTemp only — no scenes/music/DIY, those stay cloud). Some newer Govee are Wi-Fi **Matter** → Bucket C. **lib: cloud govee-api-laggat; LAN pure-Python is thin (community skews Rust/Node — verify); BLE govee-led-wez** |
| Robot vacuums | **Roborock** (S/Q series, Xiaomi-lineage) | Local **TCP 58867** (AES-128-ECB, key derived from the cloud local_key) after a one-time cloud login; MQTT/cloud fallback | **semi-local** (cloud key → local) | cloud login → rriot token + per-device local_key, then local TCP | Roborock account email-code login once to fetch local_key | unsupported-by-design(cloud) by default; **semi-local adapter feasible (verify)** like Tuya | conditional | On-LAN control possible after the one-time key pull; falls back to cloud when local is unreachable. **Map/camera data stays cloud-bound**; newer A01/B01-protocol models lean harder on cloud. `local_roborock_server` self-hosts (verify effort). **lib: python-roborock** |
| Robot vacuums | **iRobot Roomba / Braava** (600/800/900/i/j series) | Local **MQTT-over-TLS :8883** on the vacuum; provisioned via cloud | **semi-local** (older) / cloud-drift (newer j-series) | local mDNS/UDP once you hold the BLID + robot password | extract BLID + password via the `getpassword` handshake (robot must be app-provisioned first); **one local connection slot** | unsupported-by-design(cloud) by default; **older models local-adapter feasible (verify per model)** | conditional | If HomeHub holds the single local slot, the official app falls back to cloud. **Newer j-series and all mapping/AI features are cloud-tied**; older 900-series are genuinely local after password extraction. **lib: Roomba980-Python (dorita980 is Node)** |
| Cloud cameras & video doorbells (**class**) | Ring, Nest Doorbell, Arlo, Blink, Wyze, cloud-Eufy | Cloud-brokered WebRTC/HLS + cloud event/recording storage | cloud-required | vendor cloud account only | vendor account + 2FA | unsupported-by-design(cloud) | poor(cloud) | The whole category is antithetical to the appliance — your doorstep video on someone else's server. Excluded by design; steer to ONVIF/RTSP (Bucket A). **Eufy's "local" claims have been contradicted by past cloud-upload incidents (verify per model).** **lib: per-brand unofficial (all fragile)** |

#### Bucket F — BLE-local (appliance BLE radio; local on-site, no remote without a hub/cloud)

| Category | Example brands | Transport | Local vs Cloud | Discovery | Auth & pairing | HomeHub handling | Privacy | Key caveats |
|---|---|---|---|---|---|---|---|---|
| Pushers / curtains / blinds / sensors / lock | **SwitchBot** (Bot, Curtain, Blind Tilt, Meter, Contact/Motion, Lock) | BLE GATT local (reverse-engineered) + BLE **advertisement** broadcasts for sensors; optional SwitchBot Hub/cloud for remote | local (BLE); cloud/hub only for away-from-home | BLE scan (advertisement exposes device type + sensor readings) | unencrypted models: none. **Encrypted (Lock): key-id + encryption-key fetched once from the SwitchBot account API**, then local | BLE-local | good | True local BLE control + passive sensor reads, no hub. ~10 m range → hub must be near devices or use BLE proxies; no remote without vendor cloud/hub. **Hub 2 / Hub Mini expose devices as a Matter bridge** → Bucket C. **lib: PySwitchbot** |
| Smart locks | **August / Yale** (Assure, August WiFi/Smart Lock, Yale Unity) | BLE GATT local (encrypted session) for lock/unlock/status; cloud or the plug-in WiFi/Connect bridge for remote | local (BLE) on-site; cloud away | BLE scan | requires an **offline key + slot** obtained once from the August/Yale account (via `yalexs`), then all lock ops are local BLE | BLE-local (semi-local onboarding: one-time cloud key pull) | good | After the key pull, unlocking is fully local over BLE — no cloud in the critical path (good for a lock). Range → hub near the door; the offline key can change on re-provision; **August→Yale rename has repeatedly broken auth**. Safety-critical — validate reliability. **lib: yalexs-ble (local); yalexs (cloud)** |
| Cheap BLE sensors & budget locks | Xiaomi/Mijia **LYWSD03MMC**, Qingping, no-name contact/temp, budget BLE locks | BLE advertisements (broadcast telemetry) or BLE GATT; Xiaomi support custom **ATC/pvvx** firmware for open broadcasts | local (BLE) | BLE passive scan | usually none for read-only sensors; encrypted Xiaomi need a **bindkey** (Mi app / custom firmware) | BLE-local | good (sensors) / conditional (budget locks) | Excellent privacy for passive sensors (listen-only), but a zoo of ad-hoc payload formats — each needs a parser. One BLE radio saturates with many devices. **Audit budget BLE locks before trusting.** **lib: bleak (raw) + xiaomi-ble / Theengs decoder** |
| *(cross-ref)* Govee BLE-capable models | see the Govee row in Bucket E | BLE GATT | local (BLE subset) | BLE scan | per-model | BLE-local (LAN models → Bucket A) | conditional | Per-SKU; BLE protocol differs across product lines — treat as a per-model allowlist. |

## 9. Security & privacy design

Direct control is a strict *tightening* of the posture `smart-home.md` set. The
same three guarantees, now with no third party in the control path at all.

- **Egress-lock interaction — LAN-only, per adapter.** `installer/egress.sh`
  pins hub/ollama/voice egress to `192.168.0.0/16` via per-service systemd
  eBPF. Every adapter's control and state traffic is LAN-local, so it rides the
  lock with **no cloud carve-out**. `require_lan_url()` (private-IP / `.local`
  only, public rejected) generalizes to a per-adapter guard: a local-IP adapter
  validates the device address is LAN; a bridge adapter validates the bridge
  address is LAN; a Matter/radio adapter reaches devices over the LAN/mesh, not
  the internet. A device that tries to phone home is *both* unsupported by
  design *and* blocked by the lock — belt and suspenders.
- **Local device keys in the encrypted secret store.** Every secret a direct
  adapter needs — Nanoleaf/DIRIGERA/Bond bearer tokens, ESPHome Noise PSK, the
  Lutron **TLS cert+key PEM pair**, Broadlink session keys, Tapo/TP-Link
  accounts, Tuya per-device keys, Roborock `local_key`, BLE-lock offline keys,
  the Matter fabric root CA, Zigbee/Z-Wave network keys — lands in
  `secrets_store.py` (Fernet, 0600 key), one namespace per ecosystem, exactly
  as the HA long-lived token does today. HTTP is write-only there; nothing reads
  a secret back over the API. The Matter fabric root and the radio network keys
  are the *most* sensitive — losing them orphans the fabric/mesh — and must be
  backed up **separately from the DB**, the same rule the secret store and the
  gateway provider key already follow.
- **No cloud path in normal operation.** The control plane never touches the
  internet. There is exactly **one** documented near-exception and one future
  exception:
  - **Semi-local provisioning window (near-exception).** The `conditional`
    brands (Tuya, Roborock, older Roomba, encrypted BLE locks) need a *one-time*
    egress to the vendor cloud to harvest a local key, after which control is
    fully local. This does **not** fit behind a permanently-sealed lock the way
    HA did; it needs its own deliberate, disclosed, time-boxed provisioning
    mode — open egress to the specific vendor host, harvest, re-seal — surfaced
    on the privacy dashboard as a distinct event (§11). It is opt-in per device,
    never a standing exception.
  - **Locked-phone push (future exception).** The single sanctioned cloud path
    stays the `PushBridge` stub — APNs/FCM to a locked/off-LAN phone, physically
    impossible on-LAN. It egresses through the **gateway** (the sanctioned,
    unlocked egress service), never through the hub, and is unrelated to device
    control.
- **Cloud-only brands unsupported by design.** Bucket E (Ring, Nest, Arlo,
  Ecobee, Wyze, stock Emporia/Meross, most Govee, cloud cameras as a class) is
  excluded as a first-party target — their control and often their video
  round-trip a vendor cloud, which contradicts the appliance. Families are
  steered to ONVIF/RTSP cameras and to reflashing Emporia/Sonoff. Such a device
  remains reachable only *indirectly* through HA if a household insists on
  running it (§10) — never through a HomeHub-shipped adapter.
- **Permission model unchanged.** The store's per-entity `role:` / `user:`
  grants (default deny, user-over-role precedence) apply to direct devices
  identically. Reads open to any approved device; control needs admin or an
  explicit grant — the seed of scoped per-user device permissions.

## 10. The coverage tradeoff vs Home Assistant

This is stated plainly because it is the crux of the decision. Home Assistant
offers **~2000 integrations**; going direct offers a **curated subset** — Matter,
Zigbee/Z-Wave, and a handful of clean local-API brands (Buckets A–D, F). That
is the honest shape of it:

- **Cloud-only brands are unsupported *by design*.** Everything in Bucket E is
  out — not "not yet", but excluded on principle. HA supports them because HA
  will talk to a vendor cloud; HomeHub won't. That is a feature of the stance,
  not a gap to close.
- **Bridged/Matter coverage lags native integrations.** A Matter-bridged device
  exposes only mapped clusters (Hue over Matter = on/off/dim/color, not scenes
  or Entertainment); HA's native Hue integration exposes more. Breadth is
  genuinely narrower.
- **HA stays available as an *optional, indirect* fallback — complementary, not
  either/or.** `HomeAssistantProvider` survives in the tree. A household that
  already runs HA on the LAN can keep it connected as one provider among many,
  and reach the long tail (including, at their own discretion, cloud brands)
  through it — while HomeHub's own direct adapters handle the curated core with
  full isolation. HomeHub does not *depend* on HA and does not *ship* it; it
  simply doesn't forbid it.

The trade, in one line: **narrower coverage in exchange for real isolation, no
dependency treadmill, and device traffic that never leaves the LAN.** For a
privacy-first family appliance that is the right side of the trade — coverage
you can fully reason about beats coverage you can't lock down.

## 11. Caveats & hard problems

The honest list of what makes direct control hard. None of these are
blockers; several shape the phasing and the UX.

- **Reverse-engineered local APIs break on firmware updates.** Many Bucket A/E
  protocols are undocumented (WiZ, Magic Home, Yeelight, Tapo/KLAP, Tuya). A
  vendor OTA can change the wire format overnight and break the lib — TP-Link's
  2023 AES→KLAP switch and Tuya's v3.4/3.5 crypto bump are the cautionary
  tales. Mitigation: prefer documented protocols (Shelly, LIFX, WLED, ESPHome,
  Hue CLIP, Lutron LEAP, Matter), pin the curated set deliberately, and treat
  the reverse-engineered ones as best-effort.
- **Semi-local cloud-key rotation.** Tuya/Roborock/Roomba/BLE-lock keys can
  rotate when a device is re-paired or the vendor account changes, silently
  breaking local control until a fresh provisioning window is run. The
  provisioning flow must be re-runnable and must detect a stale key.
- **Per-vendor local encryption.** No two ecosystems agree: Tuya AES over TCP,
  Tapo KLAP handshake, Magic Home byte frames, Matter CASE, Zigbee/Z-Wave AES
  network keys, Lutron mutual-TLS. Each adapter carries its own crypto — there
  is no shared local-security layer to reuse.
- **Thread border-router hardware.** Matter-over-Thread needs an OTBR; commercial
  BRs (HomePod/Apple TV/Nest) are closed to third-party controllers, so HomeHub
  must run its own (802.15.4 dongle + OpenThread), ideally two for mesh
  resilience. That is hardware and a systemd service, not a pip install.
- **Zigbee/Z-Wave USB radio.** No dongle, no mesh. Z-Wave is additionally
  **region-locked** (frequency fixed at manufacture) and drags in a Node
  runtime (zwave-js-server) with no pure-Python equivalent. The coordinator
  holds the network key — replacing the stick means re-pairing unless the key
  is backed up/restored.
- **State polling vs push.** Some adapters push (Shelly Gen2+ WebSocket, Hue
  SSE, Lutron/Bond/DIRIGERA events, ESPHome native stream, Matter subscriptions)
  and give a live Home tab for free; others only poll (legacy Kasa, WiZ, Magic
  Home). The registry must model both, and the live-state roadmap item is
  satisfiable *without* HA on the push-capable adapters.
- **DHCP / IP changes.** Devices get new IPs on lease renewal. The registry must
  pin identity to a **stable device ID** (MAC / Shelly ID / Matter node ID /
  serial) and re-resolve the current IP via **mDNS**, never cache a bare IP as
  identity. A device added by manual IP needs a re-discovery path.
- **Offline / unreachable devices.** A powered-off or off-network device must
  render from the cache (the store already does wholesale-replace caching) as
  last-known + "unreachable", not vanish or hang a request — same graceful "not
  set up" discipline the routes already have.
- **Commissioning UX for non-technical families.** Matter's scan-a-QR /
  type-an-11-digit-code per device, Zigbee "permit join", Z-Wave DSK PINs, the
  Yeelight LAN-toggle, the semi-local provisioning window — all are clunkier
  than pasting one HA token. The Home tab's connect/onboarding flow must guide a
  non-technical parent through each pairing model per §7, with clear failure
  messages (e.g. "turn on LAN Control in the Yeelight app first").
- **Keeping the adapter set maintained.** The whole point of leaving HA is to
  *own* a small surface — but "small" still means each shipped adapter is a
  standing maintenance commitment (firmware drift, lib churn). The curated set
  must stay small enough to actually maintain; adding an adapter is a deliberate
  scoping decision, not a reflex.

## 12. Recommended phasing (options — not yet decided)

Presented as **options to scope**, not a committed plan. The ordering is by
value-per-effort and by how cleanly each fits the existing seam; the actual
sequence and cut line are undecided pending scoping (§13).

- **Phase 1 (option) — Local-IP adapters + discovery.** Start with the two
  cleanest, highest-coverage targets: **Shelly** (`aioshelly`, documented, push
  on Gen2+) and **TP-Link Kasa/Tapo** (`python-kasa`). Extend `discovery.py`
  from advertise to **browse** (mDNS + a couple of UDP-broadcast probes) and
  stand up the **device registry** + per-adapter dispatch. This proves the whole
  architecture end-to-end against real hardware with zero extra dependencies.
  Candidates to fold in cheaply once the seam works: LIFX, WLED, WiZ, Elgato,
  ESPHome.
- **Phase 2 (option) — First-party bridges.** Add `bridge-adapter` providers for
  **Hue** (CLIP v2 + SSE push) and **Lutron Caséta** (LEAP mutual-TLS). These
  bring the first push-capable live state without HA and exercise the secret
  store's non-token custody (the Lutron cert+key pair).
- **Phase 3 (option) — Matter controller.** Stand up a **Matter controller sidecar**
  sidecar (own systemd unit, LAN WebSocket) and a `MatterProvider`. Unlocks the
  standards-based, local-by-design long tail (Wi-Fi Matter first — no border
  router needed). Adds BLE-commissioning UX and fabric-root custody.
- **Phase 4 (option) — Zigbee / Z-Wave (hardware).** The hardware-gated tier:
  a Zigbee coordinator (zigpy in-process) and/or a region-locked Z-Wave stick
  (zwave-js-server + Node). Highest privacy transport, highest setup cost.
  Matter-over-Thread's OTBR belongs here too (shares the 802.15.4 dongle class).
- **Ongoing (any phase) — semi-local provisioning mode** for the `conditional`
  brands, and BLE-local (Bucket F) for locks/sensors, each scoped on demand.

**The phasing is un-decided.** It is captured here so scoping has a starting
shape, not because any phase is committed.

## 13. Open questions for scoping

Decisions the user will make before/while building. The first group is
strategic; the second is the per-brand technical uncertainty carried forward
from the catalog research as **"to confirm during scoping."**

Strategic:

1. **Cut line for v1 coverage** — which buckets/brands are must-have (a strong
   core is Shelly + TP-Link + Hue + a Matter path), and where does the curated
   set stop?
2. **Keep `HomeAssistantProvider` as an optional fallback, or drop it entirely?**
   (§10 assumes "keep, optional"; confirm.)
3. **Matter now or later** — is the Matter controller sidecar + BLE commissioning
   worth Phase-1/2 investment, or deferred until the local-IP core is proven?
4. **Zigbee/Z-Wave hardware commitment** — ship a recommended dongle, or treat
   the radio tier as a later add-on? Which Zigbee stack (zigpy vs z2m)?
5. **Semi-local provisioning window** — approve the disclosed, time-boxed
   one-time-egress mode for `conditional` brands, or exclude them too for a
   zero-exception stance?
6. **Commissioning UX ownership** — how much guided onboarding does the Home tab
   own for the clunkier pairing models (Matter QR, Zigbee permit-join, DSK PIN)?

Per-brand, to confirm during scoping (from the catalog research):

- **python-kasa Tapo/Kasa coverage per hardware/firmware revision:** which Kasa
  units have migrated from legacy XOR to KLAP (needing TP-Link account creds),
  and whether a firmware bump breaks the lib on a given rev. (verify per hw/fw)
- **Govee:** the live per-model list of SKUs exposing the opt-in LAN UDP API, and
  whether any maintained pure-Python Govee LAN client exists (community LAN
  tooling skews Rust/Node). (verify)
- **Yeelight** "LAN Control"/Developer Mode toggle availability and location
  under the newer Xiaomi Home app, per model and region (control silently fails
  without it). (verify)
- **Ecobee** local control via a HomeKit-controller path (e.g. aiohomekit) —
  Ecobee is a HomeKit accessory but its native/documented API is cloud-only.
  (verify)
- **Emporia Vue** local operation after an ESP32 ESPHome reflash — maturity and
  which hardware revisions can be reflashed. (verify per hw rev)
- **Sonoff DIY-mode LAN libraries** (pysonofflan / sonoff-lan) maintenance
  status — aging, and DIY mode is being locked down on some newer SKUs. (verify)
- **Roborock** semi-local adapter: effort to self-host (`local_roborock_server`)
  and which newer A01/B01-protocol models retain local control vs force cloud
  (map/camera data stays cloud-bound regardless). (verify)
- **iRobot Roomba** local viability per model — older 900-series are genuinely
  local after BLID+password extraction; newer j-series and all mapping/AI
  features drift to cloud. (verify per model)
- **Eufy** per-model cloud-upload behavior for cameras/doorbells — past incidents
  contradicted its own "local storage" marketing. (verify per model)
- **Aqara:** which specific models still require the Aqara hub for full features
  vs pair cleanly to a generic Zigbee coordinator (and which newer models are
  Thread/Matter). (verify per model)
- **Tuya/Magic Home** reverse-engineered protocol versioning — OTA can bump the
  Tuya protocol (v3.4/3.5 crypto) and break tinytuya, and per-device local keys
  rotate on re-pair. (verify per firmware)
- **go2rtc/WebRTC** integration effort for low-latency live view of ONVIF/RTSP
  cameras (snapshots are the easy first step; RTSP decode needs an external
  transcoder, not pure-Python). (verify)
- **Exact Matter controller choice (`python-matter-server` vs a matter.js-based server) + version** to
  standardize on, and the long-term status of any Python client for it — the
  ecosystem is mid-migration and versions move quickly. (verify version
  specifics)

## 14. Glossary

- **Adapter** — a concrete `SmartHomeProvider` implementation for one ecosystem
  (Shelly, Hue, Matter, Zigbee…). Behind the shared async contract.
- **Device registry** — the normalized, adapter-tagged inventory of discovered
  and configured devices; the new centre of gravity, persisted via
  `smarthome_store`.
- **Normalized action** — the small dict (`{"action": …, "params": …}`) the UI
  and future voice/LLM layer emit; per-adapter dispatch maps it to a concrete
  protocol call.
- **Semi-local** — LAN/BLE control that needs a *one-time* cloud key/password
  pull to bootstrap, then stays local (Tuya, Roborock, older Roomba, encrypted
  BLE locks).
- **Provisioning window** — the disclosed, time-boxed, opt-in egress used to
  harvest a semi-local key, after which the egress lock re-seals.
- **CASE** — Certificate Authenticated Session Establishment; Matter's encrypted
  operational session.
- **NOC** — Node Operational Certificate; the per-device credential the Matter
  controller (fabric root) issues at commissioning.
- **OTBR** — OpenThread Border Router; bridges the Thread 802.15.4 mesh to the
  LAN and runs the SRP→mDNS discovery backbone.
- **Coordinator** — the USB radio stick that forms and owns a Zigbee or Z-Wave
  mesh and holds its network key.
- **DSK PIN** — the 5-digit Device Specific Key PIN used for Z-Wave S2 secure
  inclusion.
- **KLAP** — TP-Link's newer AES-128-CBC + HMAC local handshake (Tapo, and
  migrated Kasa) that derives a local session from account credentials.
- **Push vs poll** — whether an adapter streams state changes (WebSocket/SSE/
  events/subscriptions) or must be periodically re-read.

## Changelog

- **2026-07-18** — Initial version. Records the pivot from the HA-centric
  skeleton (`smart-home.md`) to direct device control: rationale (isolation,
  no treadmill, LAN-only privacy), the protocol-fragmentation challenge, how the
  `SmartHomeProvider` seam evolves into a multi-adapter registry, the transport/
  discovery/auth taxonomies, the full six-bucket verified device catalog, the
  security/egress-lock design, the honest coverage tradeoff vs HA, hard-problem
  caveats, option-based phasing (undecided), and open scoping questions.
