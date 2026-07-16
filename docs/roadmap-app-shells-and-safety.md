# HomeHub Roadmap — App Shells (now) + Future Enhancements: Safety, Wellbeing, Ease‑of‑Use & Find‑My‑Device

Status: **feasibility‑verified July 2026.**
Companion doc: the hybrid‑cloud + smart‑home roadmap (cloud AI providers, Home Assistant device layer).

## Positioning & delivery model (decided)
- **Phase 1 — App Shells → PWA: BUILDING NOW.** The free, in‑browser‑installable app. Reach the hub in a browser and install it to the phone home screen directly. No store, no backend.
- **Phases 2–4 — Safety, Emergency, Wellbeing, Find‑My‑Device: FUTURE ENHANCEMENTS, "PRO" TIER.** Delivered through the **native companion app on the App Store / Play Store** (native is required anyway for lock‑screen + reliable push). Several of these will be **simplified** and will likely need a **proper backend service** (push relay, telephony/SIP, escalation state, cross‑device sync) rather than the pure on‑appliance model. **Not scheduled yet — this document is their capture.**

This doc scopes and **phases** the wave. Every feature was checked against the real product (local‑first FastAPI hub + local LLM + local voice + optional Home Assistant, offline‑hardened) and **feasibility‑verified** — the hard OS/regulatory limits below are load‑bearing and explain why the Pro features need native + a backend.

---

## 0. Four realities that shape everything (verified)

1. **No third‑party app can silently do the "emergency" primitives.** On both iOS and Android there is **no public API** to trigger the OS **Emergency SOS** flow, read/write the OS **Medical ID**, or **auto‑dial 911/112**. The hub can **detect, alert, escalate, and coach** — it cannot silently call emergency services. (Auto‑dial 911 *is* technically possible via a certified **E911 VoIP** number, e.g. Twilio ~$0.75/no./mo with a registered service address — but the blocker is **regulatory/liability**, so ship it as call‑a‑human by default.)
2. **Reaching a LOCKED or off‑LAN phone requires Apple APNs / Google FCM** — an unavoidable, disclosed **cloud‑by‑exception**. A PWA gets **only** OS push banners (no widgets, no lock‑screen controls, no Live Activities). Real lock‑screen presence needs **thin native shells**. *Shortcut:* the **Home Assistant Companion app already holds Apple's vetted critical‑alerts entitlement** and supports `critical:1` / Android alarm channel — reuse it for "ring‑on‑silent" instead of building a push stack.
3. **Always‑listening wake‑word + the actual call origination belong on the HomeHub appliance, not phones.** (iOS technically allows background mic via the `audio` background mode, and Android via a mic‑typed foreground service — but both are battery‑hungry, fragile to task‑swipe/OEM killers, and can't start from background. The appliance mic is the right home for reliability + privacy.) Phones are **alert receivers + assistive one‑tap dialers**.
4. **Life‑safety detectors stay independently certified.** Smoke (UL217/EN14604), CO (UL2034/EN50291), gas — must alarm **standalone**; HomeHub is **supplementary** telemetry + escalation. Same for drowning/gas consumer sensors (supplemental to barriers/supervision, never a guarantee) and wearable fall detection / Medical ID (closed — complement, don't replace).

> Design rule everywhere: **local‑first, cloud‑by‑exception, fail‑safe.** If cloud/LLM/internet is down, plain templated alerts + the on‑appliance siren/TTS still fire. A safety feature that fails *silently* is worse than none.

---

## Phasing at a glance

| Phase | Theme | Why here | Rough effort |
|---|---|---|---|
| **1 — NEXT** | **App Shells** (PWA → thin native iOS/Android) | Unlocks install, lock‑screen, and APNs/FCM push — a **prerequisite** for reliably alerting phones in every later phase | M → L |
| **2** | **Safety & Emergency core** | Highest safety‑per‑effort; mostly on‑appliance | L–XL |
| **3** | **Wellbeing & ease‑of‑use** | Leverages existing local voice+LLM; low regulatory risk | M–L |
| **4** | **Find‑my‑device** | Nice‑to‑have; tiered by honesty | M–L |

*(The separate hybrid roadmap's **cloud AI providers** (✅ now shipped — `docs/design/cloud-providers.md`, awaiting a key + restart) and **Home Assistant device layer** slot in parallel — HA in particular is a dependency for much of Phase 2/3 sensing.)*

---

## Phase 1 — App Shells (the next feature)

Today the hub is a browser SPA at `http://homehub.local`. Goal: make it an **installed app** and unlock the phone surfaces later phases need.

| Step | What | Needs | Notes / limits |
|---|---|---|---|
| **1a. PWA** *(do first — days, high impact)* | Installable hub: home‑screen icon, standalone window, offline shell, badge | Web **manifest** + **service worker** + icon set | Works on Android Chrome + desktop; iOS needs **Add to Home Screen** (16.4+). PWA lock‑screen surface = **push banners only** (rides APNs/FCM). A local HTTPS cert later unlocks more PWA APIs |
| **1b. Thin native iOS + Android shells** | WebView wrappers of the same SPA that add lock‑screen widgets/controls, foreground‑service live status, actionable push, geofence, Siri/Assistant, SOS control | **Capacitor** (or native); Apple + Google dev accounts; **APNs/FCM** integration on the hub | Required for anything on the lock screen. **Android foreground‑service ongoing notification with a LAN websocket** = the single best cloud‑free live lock‑screen surface |
| **1c. Desktop shell** *(optional)* | Windows/Mac/Linux app (tray, auto‑launch, notifications) | **Tauri** (lighter than Electron) + code signing | Only if you want desktop presence |
| **1d. CI signing pipeline** | Automated build + sign + notarize for store/native binaries | GitHub Actions + Apple notarization + Windows/Android certs | Needed once you distribute native/store builds |

**Recommendation:** ship **1a (PWA) immediately** — it's small and pairs perfectly with the `homehub.local` mDNS name. Then **1b native shells** as the gateway to lock‑screen + reliable push, since Phases 2–4 all depend on reaching phones.

**Lock‑screen surface cheat‑sheet (native only; PWA = banners only):**
- **iOS:** Lock‑Screen widgets (WidgetKit), **Control Center / lock‑screen bottom buttons** (Controls API, iOS 18), Live Activities + Dynamic Island (iPhone 14 Pro+), Action Button (15 Pro+), Siri Shortcuts, Focus filters, actionable push. Anything sensitive triggers Face ID.
- **Android:** foreground‑service ongoing notification (best live channel), Quick Settings tile, App Shortcuts/Assistant, geofence, **lock‑screen widgets only on Android 16 QPR2+** (Pixel‑first, tiny installed base for years).

---

## Phase 2 — Safety, Accident & Emergency

Built as a **local‑first layered system**. Two shared cores first, then detectors + responses.

### 2.0 Shared cores (build first)
| Feature | What | Needs | Where/Effort |
|---|---|---|---|
| **Emergency Escalation Engine** | Rules + state machine: trigger → ack window → local siren/lights/TTS → family tier 1 → tier 2 → cellular → optional cloud; ack/cancel/snooze; **tamper‑evident audit log** | New `safety` module on the hub; SQLite schema (events/contacts/policies/ack‑tokens); subscribes to HA state webhooks; LLM only for phrasing summaries | home‑hub / L |
| **Family Notification Fabric** | The delivery layer: (1) hub speaker TTS + on‑screen, (2) LAN websocket/SSE to a foregrounded phone, (3) **APNs/FCM push** to locked phones, (4) **cellular SMS/voice** via on‑board modem | Native shells + APNs/FCM (Phase 1b); optional **self‑hosted `ntfy`/UnifiedPush** for Android local push; optional LTE modem for off‑internet | hybrid / L |

### 2.1 Emergency "wake‑name + help → call" (the feature you asked for)
Architecture (feasible version): **wake word + call origination on the appliance; phones are receivers.**
| Feature | What | Needs | Where/Effort |
|---|---|---|---|
| Wake‑name + "help" detection | Always‑listening keyword spotter on the hub mic | **openWakeWord** (free, offline — fits privacy stance) or **Porcupine** (higher accuracy, paid custom words); VAD; systemd service | home‑hub / M |
| Post‑wake intent parse | Capture "call Mom"/"help", resolve target | Existing **faster‑whisper** STT + a **constrained grammar** (not open LLM, for speed/determinism) | home‑hub / M |
| Confirm + duress guard | Countdown "calling Mom, say cancel" (emergency skips confirm) | Kokoro TTS + cancel phrase/button; per‑intent tunable | home‑hub / S |
| Priority contact directory + escalation | Ordered contacts, quiet‑hours, try‑next‑on‑no‑answer, **admin‑only edit** | Portal UI + `hub.db` schema; RBAC | home‑hub / S |
| Outbound call + TTS relay | Appliance calls the contact and speaks the alert | **SIP/VoIP trunk** (Twilio/Telnyx/Plivo; ~$1–2/mo no. + ~$0.014/min) with `<Say>` + two‑way `<Dial>`; or **self‑hosted Asterisk/FreeSWITCH** | hybrid / L |
| Two‑way audio relay | Live conversation appliance ↔ callee | SIP bridge + **echo cancellation** (open‑room AEC); voicemail detection | hybrid / L |
| Cellular/GSM fallback | Call works when internet is down | LTE modem (SIM7600/EG25‑G) + SIM + antenna | home‑hub / XL |
| Loud push + handoff to phones | Blast critical push so a person can call back / one‑tap dial | APNs critical‑alert **(reuse HA Companion entitlement)** / FCM; phone `tel:`/CallKit (foreground, unlocked) | hybrid / M |
| Hardware panic button | Zero‑speech trigger (most reliable) | Zigbee/Thread/BLE button via HA; wall or pendant | home‑hub / M |
| 911/112 policy | Route "call emergency" | Default: **call a human/monitoring service, coach the user**; optional certified **E911 VoIP** number (registered address) — liability decision | hybrid / L |
| Kids‑safe + offline‑mode + audit | Kid help‑phrase → parent first; rate‑limit; self‑test + audible "path is up/down" | RBAC, cooldowns, health checks, append‑only log | home‑hub / M–S |

### 2.2 Detection & response (via Home Assistant sensors)
| Feature | Sensor / tech | Notes |
|---|---|---|
| **Whole‑home emergency broadcast** | Hub speaker + Kokoro TTS + smart lights/siren (HA), hazard‑colored | **No phone needed**; keep a battery siren |
| **Fire / smoke** | Certified **Matter/Thread** (Sensereo, Heiman UL217) or Zigbee (Frient); or relay off existing UL alarms | Supplementary to standalone certified alarms |
| **Carbon monoxide** | Certified CO/combo (UL2034/EN50291) + optional gas shutoff | Announcement matters most (CO is invisible) |
| **Combustible gas / propane** | Zigbee/Wi‑Fi gas sensors + optional motorized shutoff | Lower‑assurance; professional shutoff install |
| **Water leak + auto‑shutoff** | Zigbee/Matter leak sensors + motorized main valve (Moen Flo‑class) | High property‑damage value; monitor battery |
| **Intrusion (local alarm panel)** | Door/window/glass‑break/PIR via HA **Alarmo**; siren+lights+snapshot | Self‑monitored, **not** UL central‑monitoring; PIN/duress disarm |
| **Duress / silent panic** | Distinct disarm PIN or hidden voice phrase → silent family alert | Genuinely silent; pair with a discreet button |
| **Fall detection** | **mmWave radar** (Aqara FP2 / Vayyar‑class) — camera‑free, works in dark | Radar = privacy‑preserving; wearables/watch fall detect can only *complement* (closed SOS) |
| **"Are you OK?" inactivity watch** | HA motion/door/radar + per‑person baseline → TTS check → escalate | For living‑alone; not a medical PERS; needs enough sensors |
| **Child wandering / door‑open** | Contact sensors on exits/gates + time‑of‑day context | Tracking outside home needs a wearable/cellular tag |
| **Pool / drowning** | Gate contact + outdoor radar + immersion alarm | **Supplemental** to fences/supervision — never "prevents drowning" |
| **Temp extremes / pipe‑freeze / fridge fail** | Temp/humidity + probe sensors → alert + optional HVAC | Respect device safety ratings on actuation |
| **Power/UPS + medical‑equipment** | UPS + NUT + power‑loss sensor; cellular for off‑internet alert | Size UPS for the alert window |
| **Camera / doorbell + two‑way** | ONVIF/RTSP + **Frigate** local NVR (on‑appliance inference) | **Never** in bathrooms/bedrooms; local‑only hardware |
| **Medical ID card** | Encrypted per‑resident profile; LAN page + printable QR; read aloud on alert | Can't write OS Medical ID; encrypt + RBAC |
| **Location/context on alert** | Room + last‑known + snapshot + LLM summary attached to escalations | Off‑LAN live location needs native app + push; time‑boxed, consented |
| **External hazard/weather** | HA + NWS/AQI public feeds → announce/prep home | **Cloud‑by‑exception** (public feeds only); toggleable |

---

## Phase 3 — Wellbeing & Ease‑of‑Use

Leads with what the appliance does uniquely well **and fully offline** (local voice + LLM). Delivery to an *absent* phone inherits the APNs/FCM ceiling.

| Feature | What | Where/Effort |
|---|---|---|
| **Reminder engine (foundation)** | Scheduler → best channel per person/urgency (hub TTS → LAN → push) | hybrid / L |
| Medication reminders & adherence | Spoken/on‑screen prompts + "I took it"; **not a medical device** disclaimer | hybrid / M |
| Hydration & movement nudges | Gentle chimes; quiet‑hours governor | home‑hub / S |
| Elderly daily check‑in | TTS "are you OK?" + "I'm OK" button/voice; non‑response → caregiver | hybrid / M |
| Routine monitoring | Learn normal rhythm (HA sensors) → flag deviations | hybrid / L |
| Comfort / air‑quality tiles | Temp/humidity/CO₂/PM2.5 (HA sensors) + plain‑language guidance | hybrid / S |
| Sleep & kids' bedtime wind‑down | HA dim + calming TTS + countdown | home‑hub / S–M |
| Kids screen‑time (on hub) | Manage time **on the hub**; can't touch OS Screen Time / Family Link | home‑hub / M |
| Mood check‑in & journaling | Emoji + voice/text journal, LLM reflects; **not a therapist** guardrails + crisis signposting | home‑hub / S |
| Gentle voice nudges & **room intercom/broadcast** | LLM‑phrased announcements to hub + HA speakers; push‑to‑talk | home‑hub–hybrid / S–M |
| **"Call home" / two‑way intercom to a phone** | Phone↔hub WebRTC (app open); ringing a **locked** phone needs native CallKit/ConnectionService + VoIP push | hybrid / XL |
| Shared family calendar + chores | ✅ v1 **built** (recurrence + rotation, `docs/design/family-calendar.md`); still open: NL entry via LLM, **read‑only ICS** import (2‑way sync = cloud opt‑in) | hybrid / M |
| **Accessibility** (large‑text, high‑contrast, voice‑only) | Per‑profile theming + full hands‑free loop (STT+TTS+LLM) | pwa/home‑hub / S–M |
| Simple onboarding | Guided first‑run wizard + LLM setup assistant; IP fallback if mDNS flaky | pwa / M |

---

## Phase 4 — Find‑My‑Device (three honesty‑ordered tiers)

| Tier | Feature | Needs | Reality |
|---|---|---|---|
| **1 (pure‑local)** | **LAN device inventory** (mDNS browse + ARP/ping + DHCP) | Extend the zeroconf already in `discovery.py` to *browse*, not just advertise | Shows **presence/identity only** — never physical location |
| **1** | **"Make it beep/flash"** for controllable devices | HA entity services (`light flash`, `media_player` chime) | Only devices with a light/speaker |
| **1** | **DIY BLE beacon finder** (keys/wallet) + **"where is X?" voice** | ~$3–10 BLE tags + a **USB BLE dongle** on the hub (BlueZ/bleak) + existing voice/LLM | Proximity (near/far), ~1 room per scanner |
| **2 (some hardware)** | **ESPresense room‑level** ("which room") | 1× ESP32 (~$5) per room + MQTT/HA | Room‑level, needs per‑home calibration |
| **2** | **Ring my phone** (even on silent) | **HA Companion app** `notify.mobile_app` with `critical:1`/alarm channel (reuses its entitlement) | Rides APNs/FCM (cloud‑by‑exception); can't trigger native Find My |
| **3 (limited)** | Coarse Wi‑Fi‑AP room hint / UWB precision / Find My deep‑link | Prosumer AP local API / UWB phones+accessory / just UI links | Find My has **no API** — user must tap; UWB is foreground‑only, phone‑native |

---

## Cross‑cutting needs (all phases)
- **Auth/RBAC:** new privileges (`safety_admin`, `devices_control`, `medical_read`, `cloud_ai`…); guests never see medical/location/camera.
- **Encrypted‑at‑rest store** — ✅ **built** (Fernet store on the hub, `docs/design/secret-store.md`); medical IDs/contacts/HA tokens can now use it.
- **Egress allowlist** — ✅ **built** (per‑service systemd eBPF lock, `docs/design/platform-activation.md`; sudo activation pending): APNs/FCM, SIP/VoIP, public weather feeds each become a **disclosed** exception on a privacy dashboard.
- **Home Assistant** is the sensor/actuator dependency for most of Phase 2/3.
- **Native shells + APNs/FCM** (Phase 1b) gate reliable phone alerting everywhere.
- **Kids‑safety filter** (still owed) applies to anything voiced to children.

## Likely hardware
Thread/Matter border router + **Zigbee dongle** (SkyConnect/ZBT‑1); certified smoke/CO/gas/leak sensors; **mmWave radar** (fall/occupancy); Zigbee panic button; motorized water (+ optional gas) shutoff valve; **USB BLE dongle** + BLE tags; ESP32 boards for room‑level; **UPS**; optional **LTE modem + SIM** for off‑internet emergency escalation; ONVIF cameras + (optional) a small GPU for local Frigate.

## Open decisions before building
1. **Phase 1:** ship PWA now, and do we build native shells with **Capacitor** (recommended) — and reuse the **Home Assistant Companion app** for critical push to avoid our own APNs stack?
2. **Emergency calling:** call‑a‑human default (recommended) vs invest in certified **E911**? Cloud **SIP trunk** vs on‑board **cellular** vs both?
3. **Smart‑home:** confirm **Home Assistant** as the sensor layer (assumed throughout).
4. **Privacy exceptions:** approve the disclosed cloud‑by‑exception set (APNs/FCM, SIP, weather) with kids‑always‑local.
5. Which **safety features** are must‑have v1 (fall + panic + fire/CO + leak + check‑in is a strong core).
