# Future Enhancements — Voice & Beyond

This document captures **every proposed and potential enhancement** for the Home
LLM Hub so that nothing is lost as the product grows. It is a living backlog, not
a commitment: each item lists a short description, the rationale/value, a rough
effort estimate (**S / M / L**), its dependencies, and the phase it belongs to.

It is organized around the **voice roadmap** that motivated it, but also captures
the platform, productization, and infrastructure ideas that surfaced along the
way. For shipped features see the [README](../README.md); for licensing and
appliance shipping see [`productizing.md`](productizing.md); for the trust model
see [`privacy.md`](privacy.md).

> **Effort key.** **S** ≈ hours to a day; **M** ≈ a few days to a week;
> **L** ≈ multiple weeks, new subsystem, or a hardware/architecture change.

---

## 1. The voice architecture (context for everything below)

The voice capability runs as a **separate local "voice service"**, deliberately
**outside Ollama**, so chat inference and speech never compete for the model
runtime. Execution is **strictly sequential** — transcribe → Qwen → speak, never
concurrent.

```
   Browsers (phones / laptops)
        |  getUserMedia + MediaRecorder (mic capture)
        v
   HOME HUB  :80 / :8090
        |                                   \
        |  (a) chat path                      (b) voice path
        v                                       v
   GATEWAY :8080                           VOICE SERVICE :8100
        |                                    |              |
        v                                    v              v
   OLLAMA 127.0.0.1:11434              faster-whisper   kokoro-onnx
        |                                (STT, base)    (TTS, Kokoro-82M,
        v                                                af_sarah voice)
   Qwen2.5-7B (chat)
```

**Talk loop:** mic capture → `POST /api/voice/transcribe` → text dropped into
chat → existing chat send to Qwen → on response, optional auto
`POST /api/voice/speak` → play audio. A **read-aloud** button is available on any
message or note.

**Chosen stack (verified commercial-safe, all local, English-only in Phase 1):**

| Role | Component | Model | License |
| ---- | --------- | ----- | ------- |
| STT  | `faster-whisper` | `base` | **MIT** |
| TTS  | `kokoro-onnx` | Kokoro-82M, voice `af_sarah` | **Apache-2.0** |

**Rejected — do NOT recommend as commercial-safe:** Piper (relicensed to
**GPL-3.0** as of Oct 2025), Coqui / XTTS (**CPML**, non-commercial), eSpeak-NG
(**GPL**), Moonshine non-English (Community License, non-commercial).

**Resource reality on the reference box (Ryzen 5 3600 / 16 GB, CPU-only):**

- End-to-end latency **~10–20 s per spoken turn** (sequential). Great for
  kids-learning and voice notes; **not** real-time conversation.
- RAM peak **~9–10 GB**. `OLLAMA_MAX_LOADED_MODELS=1` stays unchanged.

> **Commercial-ship caveat (see §6, "espeak-free G2P").** Kokoro's English
> grapheme-to-phoneme step may fall back to **espeak-ng (GPL)** via
> `espeakng-loader` for out-of-dictionary words. This must be validated and an
> espeak-free path confirmed **before** any commercial distribution.

### Phase plan at a glance

| Phase | Theme | Headline items |
| ----- | ----- | -------------- |
| **P1** | Voice foundation (this build) | mic capture + transcribe + read-aloud |
| **P2** | Learning & productivity depth | kids-tutor prompts, voice-note → summarize, multilingual |
| **P3** | Advanced & assistive | pronunciation/phoneme scoring, streaming STT |
| **Cross-cutting** | Infra / platform / shipping | GPU upgrade, wake-word, native apps, offline robustness, license auditing, espeak-free G2P, deferred cloud tier |

---

## 2. Phase 1 — Voice foundation (this build)

| # | Enhancement | Description | Value | Effort | Dependencies | Phase |
| - | ----------- | ----------- | ----- | ------ | ------------ | ----- |
| 1.1 | **Mic capture + transcribe** | Browser mic capture (`getUserMedia` + `MediaRecorder`) posts audio to `POST /api/voice/transcribe`; faster-whisper `base` returns text that flows into the chat box. | Hands-busy and accessibility-friendly input; the entry point to the whole voice roadmap. | M | Voice service :8100, faster-whisper, browser mic permission (HTTPS or localhost). | **P1** |
| 1.2 | **Read-aloud button** | A button on any chat message or note calls `POST /api/voice/speak` (kokoro-onnx, `af_sarah`) and plays the returned audio. | Turns the Hub into a reader for kids, low-vision users, and multitaskers; pairs with chat replies. | S | Voice service :8100, kokoro-onnx. | **P1** |
| 1.3 | **Auto-speak chat replies (opt-in)** | After a Qwen response, optionally auto-POST `/api/voice/speak` and play it, completing the hands-free talk loop. | Closes the conversational loop without a manual tap; the "talk to the Hub" experience. | S | 1.1, 1.2; a per-user toggle. | **P1** |

---

## 3. Phase 2 — Learning & productivity depth

| # | Enhancement | Description | Value | Effort | Dependencies | Phase |
| - | ----------- | ----------- | ----- | ------ | ------------ | ----- |
| 2.1 | **Multilingual voices + language selector** | Expose **Kokoro's 8 languages** as a TTS voice/language selector; use **Whisper auto-detect** (or a manual override) for STT so non-English speech transcribes correctly. | Opens the Hub to non-English households and language learners; the single biggest reach expander. | M | Kokoro multi-language voices, Whisper language detection, UI selector + per-user/per-conversation preference, espeak-free G2P audit (§6) per language. | **P2** |
| 2.2 | **Kids-tutor prompts** | Curated system-prompt presets that make Qwen behave as a patient, age-appropriate tutor (gentle tone, step-by-step, no answer-dumping). | The flagship learning use case; differentiates the box for families. | S | Prompt library; ties into 2.3 selectors. | **P2** |
| 2.3 | **Grade-level + subject selectors** | UI selectors for grade/level and subject that parameterize the tutor prompt (vocabulary, difficulty, examples). | Makes tutoring relevant per child and per topic; reduces prompt-fiddling for parents. | M | 2.2; small schema for selector state. | **P2** |
| 2.4 | **Per-kid session history in the DB** | Persist tutoring sessions keyed to a child/profile so progress and prior context survive across sessions. | Continuity ("remember where we left off"); foundation for the progress view (2.5). | M | New tables (e.g. `kid_profiles`, `tutor_sessions`) alongside existing `conversations`/`messages`; profile concept distinct from device/user. | **P2** |
| 2.5 | **Parent / teacher progress view** | A read-only dashboard summarizing each child's activity, topics covered, time spent, and trouble spots, visible to a parent/teacher role. | Parental oversight and motivation; a concrete selling point for the learning angle. | M | 2.4 data; a new privilege/role (e.g. `tutor_view`) layered on the existing role/privilege system. | **P2** |
| 2.6 | **Voice-note → transcribe → LLM summarize → note** | Record a voice note → faster-whisper transcript → Qwen summarizes/cleans it → saved as a Note. | High-utility productivity flow that reuses STT + chat + Notes; useful well beyond kids. | M | 1.1 (STT), chat path, Notes feature; a "voice note" entry point in the Notes UI. | **P2** |
| 2.7 | **Household routine learning (the core moat)** | Track patterns across conversations, voice notes, checklists, and tutoring sessions to learn family members' interests, habits, and routines; use those insights to personalize Qwen responses (e.g. suggest study time from historical patterns, auto-populate checklists from repeated tasks). **All data is local-only and user-visible/editable in settings.** This is the unified flywheel that the scattered enablers (2.4 session history, 2.5 progress view, 2.6 voice notes) feed into — not an independent feature. | **The only durable moat** (see market study §1, §7): retention compounds as the Hub learns the household, and a future acquirer sees something no one else has. **Intentionally built from day one** so the data accrues early. | L | 2.4 (session history), 2.6 (voice notes), checklists/Notes, chat path; a local-only insights store with a user-facing review/delete UI in settings; explicit privacy posture (see [`privacy.md`](privacy.md)). | **P2 (cross-cutting moat)** |

---

## 4. Phase 3 — Advanced & assistive

| # | Enhancement | Description | Value | Effort | Dependencies | Phase |
| - | ----------- | ----------- | ----- | ------ | ------------ | ----- |
| 3.1 | **Pronunciation / phoneme scoring** | Use a phoneme-recognition model (e.g. **Allosaurus** or a **wav2vec**-based scorer) to compare a learner's spoken phonemes against the target and give pronunciation feedback. | The premium language-learning feature; turns the tutor from "explains" into "coaches speech". | L | New model in the voice service, alignment/scoring logic, per-language phoneme inventories, license check on the chosen model, GPU strongly preferred for throughput. | **P3** |
| 3.2 | **Streaming STT** | Stream partial transcripts as the user speaks (chunked / incremental decoding) instead of waiting for the full utterance. | Cuts *perceived* latency dramatically even on CPU; makes voice feel responsive without new hardware. | M | faster-whisper streaming/VAD chunking, a streaming transport (WebSocket/SSE) on :8100, browser-side incremental capture. | **P3** |
| 3.3 | **Video surveillance & AI "watch"** | Connect home cameras (RTSP/ONVIF) for live view + recording, and use AI to *understand* footage: motion/event-triggered keyframe analysis via the vision model (who/what is at the door; person/package/pet detection), **natural-language event search** ("when was someone at the gate?"), a reviewable event timeline, and configurable alerts/notifications. **All footage stays local — the privacy promise applies in full.** | A flagship smart-home/security capability and a strong AI-native differentiator: *search and understand* footage, not just record it. High engagement; a natural pillar of the smart-home direction and the home-suite vision. | XL | Camera integration (RTSP/ONVIF — **integrate Frigate / go2rtc rather than rebuild an NVR**); motion/object detection; the vision model for scene understanding; embeddings for event search; video storage/retention + privacy/redaction controls; alerting. **Real-time multi-camera analysis needs a GPU (5.1)** — on the CPU box, do motion-triggered or periodic-keyframe analysis only (not every frame). Privacy- and storage-critical. | **P3 / smart-home** |

---

## 5. Cross-cutting — Infrastructure, platform & UX levers

These are not bound to a single phase; several can land early and pay off across
all of them.

| # | Enhancement | Description | Value | Effort | Dependencies | Phase |
| - | ----------- | ----------- | ----- | ------ | ------------ | ----- |
| 5.1 | **GPU upgrade path** | Document and support an optional GPU that runs Whisper, Kokoro, and Qwen with hardware acceleration. **Collapses end-to-end latency from ~10–20 s to ~2–4 s per turn.** | **The single biggest UX lever.** Turns "voice notes / kids learning" into near-real-time conversation; unblocks streaming STT and pronunciation scoring at scale. | L | GPU hardware; CUDA/ONNX-GPU builds of faster-whisper, kokoro-onnx, and Ollama; revised RAM/VRAM sizing; `OLLAMA_MAX_LOADED_MODELS` retuning. | Cross-cutting (enables P2/P3) |
| 5.2 | **Wake-word / hands-free** | An always-listening wake-word ("Hey Hub") starts capture without touching the screen, enabling fully hands-free turns. | Natural for kids, kitchens, and accessibility; removes the tap that breaks the hands-free illusion. | M | A local wake-word engine (license-audited), continuous mic handling and privacy UX (clear on/off, local-only), browser/native capture constraints. | Cross-cutting (best after P1, pairs with 5.4) |
| 5.3 | **Native admin & user apps** | Native mobile/desktop apps over the *same* HTTP API: smoother device-approval (push + QR pairing), native camera capture, offline-friendly note/checklist caches with sync, and native mic capture for voice. | Better-than-browser ergonomics for the recurring flows; stronger session model (device-binding, short-TTL tokens). See [`productizing.md` §4](productizing.md). | L | Stable HTTP API contract; push infrastructure; app-store/build pipeline; voice items (1.1/1.2) for native mic. | Cross-cutting (future phase) |
| 5.4 | **Offline robustness (healthz-gated controls, no cloud fallback)** | Voice controls are **gated on a `/healthz` check** of the voice service; if STT/TTS is down, the UI disables/greys the controls and explains why. **No cloud STT/TTS fallback — ever — to preserve the privacy promise.** | Honest, predictable degradation; upholds "your data never leaves your home" (see [`privacy.md`](privacy.md)) instead of silently shipping audio to a third party. | S | A `/healthz` endpoint on :8100; UI gating; explicit product decision **not** to add cloud fallback. | Cross-cutting (land with P1) |
| 5.5 | **Per-voice license auditing** | If bundling **more** Kokoro (or other) voices, audit each voice's license/attribution individually before shipping, and record it in the NOTICES file. | Prevents a non-commercial or restrictively-licensed voice from silently contaminating a shippable product. | S | A per-voice license inventory; ties into the [`productizing.md`](productizing.md) NOTICES discipline. | Cross-cutting (before any voice-bundle ship) |
| 5.6 | **Espeak-free English G2P path** | Validate and ship a grapheme-to-phoneme path for Kokoro that does **not** depend on **espeak-ng (GPL)** for out-of-dictionary words (the current `espeakng-loader` fallback is GPL). | **Hard gate for commercial distribution** of English TTS — without it, the GPL fallback may attach to the shipped product. | M | A pure-data/permissive G2P (e.g. an MIT/Apache dictionary + rules) or a phonemizer with a permissive license; test coverage on OOV words across the vocabulary. | Cross-cutting (**before commercial ship**) |
| 5.7 | **Deferred cloud-provider "home tier"** | A **deferred / explicitly-not-now** option to offer an optional hosted/cloud tier for households that want remote access or heavier models. | Captures the idea so it isn't forgotten; kept deferred to protect the **privacy-first, fully-local** identity of the product. | L | A clear privacy/consent boundary, separate from the local-only default; revisits the "no cloud fallback" stance (5.4) deliberately, not by accident. | Deferred (post-roadmap) |

---

## 6. Pre-commercial-ship checklist (license & privacy gates)

Before the voice capability is shipped in a **commercial** product, these gates
must be cleared (they recur throughout the table above):

- [ ] **Espeak-free English G2P** validated — no GPL `espeak-ng` in the shipped
      path (5.6). This is the one most likely to be missed.
- [ ] **Per-voice license audit** for every bundled voice, recorded in NOTICES
      (5.5).
- [ ] **STT/TTS model licenses** re-verified at ship time — faster-whisper
      (**MIT**), Kokoro-82M (**Apache-2.0**) — and the **rejected** set kept out:
      Piper (GPL-3.0), Coqui/XTTS (CPML), eSpeak-NG (GPL), Moonshine non-English
      (non-commercial).
- [ ] **No cloud fallback** confirmed in code **and shipped product** — the voice
      service `/healthz` gates all voice controls; installers (R1) and apps (R2)
      carry **zero** cloud-fallback code path for STT/TTS; docs explicitly state the
      local-only privacy guarantee; a **code review confirms this before any
      commercial release**. Privacy promise intact (5.4). *Mandatory gate — must not
      be bypassed during productization.*
- [ ] Any **Phase 3** model (Allosaurus / wav2vec, 3.1) license-checked before
      bundling.

---

## 7. Quick index of all proposals

| ID | Proposal | Effort | Phase |
| -- | -------- | ------ | ----- |
| 1.1 | Mic capture + transcribe | M | P1 |
| 1.2 | Read-aloud button | S | P1 |
| 1.3 | Auto-speak chat replies (opt-in) | S | P1 |
| 2.1 | Multilingual voices + language selector | M | P2 |
| 2.2 | Kids-tutor prompts | S | P2 |
| 2.3 | Grade-level + subject selectors | M | P2 |
| 2.4 | Per-kid session history in the DB | M | P2 |
| 2.5 | Parent / teacher progress view | M | P2 |
| 2.6 | Voice-note → transcribe → summarize → note | M | P2 |
| 2.7 | Household routine learning (the core moat) | L | P2 (cross-cutting moat) |
| 3.1 | Pronunciation / phoneme scoring | L | P3 |
| 3.2 | Streaming STT | M | P3 |
| 3.3 | Video surveillance & AI "watch" | XL | P3 / smart-home |
| 5.1 | GPU upgrade path | L | Cross-cutting |
| 5.2 | Wake-word / hands-free | M | Cross-cutting |
| 5.3 | Native admin & user apps | L | Cross-cutting |
| 5.4 | Offline robustness (healthz-gated, no cloud fallback) | S | Cross-cutting |
| 5.5 | Per-voice license auditing | S | Cross-cutting |
| 5.6 | Espeak-free English G2P path | M | Cross-cutting (pre-ship) |
| 5.7 | Deferred cloud-provider "home tier" | L | Deferred |

---

## Product Roadmap & Distribution (vision)

This section captures the **founder's go-to-market and platform vision** for the
Home LLM Hub — how it gets installed, how people reach it from every device, and
where the product line goes (smart-home, learning, and a dedicated secure
hardware hub). It complements the voice-centric backlog above and the appliance
shipping notes in [`productizing.md`](productizing.md).

> **Effort key (extended).** This section uses **S** (hours–day) / **M** (days–week)
> / **L** (multiple weeks / new subsystem) as above, plus **XL** for a *quarter-or-more*
> effort that spans multiple platforms, a release/signing pipeline, or a hardware
> program. Each item below lists **description**, **rationale/value**, **rough
> effort**, **dependencies**, and **target phase**.

### R1. One-click installers (Mac / Windows / Linux) — two flavors from one codebase

**Description.** Ship downloadable, double-click installers for the three desktop
OSes so a non-technical user can stand up a HomeHub without touching a terminal,
Docker, or a compose file. Two flavors are offered from the **same codebase and
the same installer build**, differentiated only by a feature flag / install-time
choice:

- **Type A — "UI / integration only."** Installs **just the hub backend (FastAPI)
  + the web UI** (and the gateway). The user **brings their own LLM** — either a
  cloud API key (OpenAI/Anthropic/etc.) or a local runtime they already run — and
  wires it in through a provider/endpoint setting. No model weights are bundled,
  so the download stays small and there is no GPL/weights-license exposure on the
  installer itself.
- **Type B — "all-in-one."** Same installer, but during setup it **prompts:
  "Download the local LLMs too?"** If the user says **yes**, the installer then
  **fetches and installs Ollama and the default model set** (Qwen2.5-7B and the
  other commercially-cleared weights from [`productizing.md`](productizing.md)) and
  configures the hub to point at the local `127.0.0.1:11434` runtime. If they say
  **no**, it behaves exactly like Type A. The two flavors are therefore **one build
  with an optional, opt-in model-download step**, not two separate products.

**Packaging approach (per OS).**

- **Wrapper choice — Tauri (recommended) vs Electron.** Wrap the existing FastAPI
  backend in a desktop shell. **Tauri** is preferred: it uses the OS native
  WebView (tiny bundle, ~3–10 MB shell vs Electron's ~100 MB+ Chromium), has
  first-class code-signing/updater tooling, and a Rust supervisor process is a
  natural place to spawn/manage the bundled Python backend and Ollama as child
  processes. **Electron** is the fallback if a heavier shared web stack or richer
  Node ecosystem is needed. In both cases the shell's job is identical: launch the
  Python/FastAPI process (PyInstaller/`uv`-frozen or an embedded interpreter),
  health-check it, then load the existing UI from the local server — so the **web
  UI and API are reused verbatim**.
- **Bundling vs optional-download of Ollama + models.** Always **optional-download**,
  never bundle weights into the installer. Bundling multi-GB weights bloats the
  artifact, complicates per-model license/NOTICES discipline, and forces a new
  installer for every model bump. Type B fetches Ollama's official installer and
  pulls models post-install (resumable, with a progress UI and a disk-space
  precheck). This keeps both flavors on one small signed installer.
- **macOS.** `.dmg` / `.pkg`; **Developer ID code-signing + Apple notarization**
  (and stapling) are mandatory or Gatekeeper blocks launch. Universal binary
  (arm64 + x86_64). Ollama installs to its standard location; models land under
  the user's app-support dir.
- **Windows.** `.msi`/NSIS (or MSIX); **Authenticode code-signing** (EV cert
  recommended to avoid SmartScreen warnings). Handle Defender/firewall prompts for
  the local listener; offer "start at login" as an option.
- **Linux.** Provide **.deb** and **.rpm** for the mainstream distros and an
  **AppImage** for everything else (and optionally Flatpak later). No notarization,
  but ship a `.desktop` entry and document the systemd-user unit for "run on login."

**Rationale / value.** The terminal-and-Docker setup is the single biggest
adoption wall. One-click installers turn the Hub from "a project you self-host"
into "an app you install," dramatically widening the addressable audience. The
A/B split lets power users / privacy purists run fully local (Type B) while
cloud-key or BYO-runtime users get a featherweight install (Type A) — **without
forking the codebase**.

**Effort.** **XL** (cross-OS packaging + signing/notarization pipeline + the
optional model-download flow; the wrapper itself is L, the per-OS signing and
release plumbing is what pushes it to XL).

**Dependencies.** A frozen/embeddable build of the FastAPI backend; the wrapper
(Tauri/Electron) shell + child-process supervision; Apple Developer ID + Windows
code-signing certs; an Ollama-install/model-pull orchestration with progress and
disk checks; the existing provider-config setting (for Type A's BYO-LLM); reuses
the mDNS/`llm.local` plumbing from [`productizing.md`](productizing.md).

**Voice positioning (CPU-only by default).** Both flavors ship voice as
**CPU-only**, optimized for **non-real-time** use — voice notes (2.6), read-aloud
(1.2), and the kids-learning/tutoring mode — at the **~10–20 s** end-to-end latency
documented in §1, which is acceptable for those flows. **Real-time conversation is
not promised on CPU**; it requires the GPU path (5.1), which collapses latency to
**~2–4 s**. Neither installer flavor requires a GPU, and **Type A defaults to no
GPU**. This is an intentional trade of convenience for a low entry price (see the
market study verdict: position voice as "learning / notes / search first, not a
real-time assistant").

**Target phase.** **Cross-cutting (productization)** — the gateway to consumer
distribution; lands after the API/UI are stable, ideally alongside the native
clients (R2) and discovery (R3).

### R2. Native client apps (Android, iPhone, iPad, tablets, Mac, Windows, Linux)

**Description.** First-party client apps across phones, tablets, and desktops that
talk to a HomeHub on the LAN (or, later, remotely). The apps are **thin clients
to the existing hub HTTP API** — they render the UI and add native-only ergonomics
(native mic/camera capture, push notifications, biometric unlock, offline note/
checklist caches with sync, QR/device-pairing) but contain **no model or business
logic of their own**; that stays on the hub. This is the consumer-facing sibling
of the "native admin & user apps" item (5.3) above, generalized to every form
factor.

**Architecture recommendation.**

- **Thin client to the hub API.** The hub remains the single source of truth;
  apps are presentation + native-capability layers over the same REST/WebSocket
  endpoints. This keeps the **API as the one contract** and means most app
  screens are reusing already-built endpoints.
- **One cross-platform codebase — options compared:**
  - **PWA-first (recommended starting point).** The UI is already a web app;
    making it an installable PWA (manifest, service worker, offline cache, web push
    where supported) reaches **all platforms immediately at near-zero marginal
    cost**. The limitation is iOS web-push/background and some native-capture gaps.
  - **Flutter.** Best *native* feel and performance from a single Dart codebase,
    excellent on mobile + desktop; cost is a parallel UI rewrite (no reuse of the
    existing web front-end) and a new language/toolchain.
  - **React Native.** Strong mobile story and some code/skill reuse if the web UI
    is React; desktop support is weaker and the bridge adds friction.
  - **Tauri (mobile + desktop).** Lets the **same web UI** be wrapped on desktop
    *and* (increasingly) mobile, maximizing reuse with the R1 installers — attractive
    because it unifies the desktop-installer shell and the app shell.
  - **Recommendation:** go **PWA-first** to cover everything cheaply now, then wrap
    with **Tauri** (shared with R1) for the desktop/app-store presence and
    native-capture needs; reserve **Flutter** only if a fully native mobile
    experience becomes a competitive requirement.

**Rationale / value.** "It works on my phone/tablet/laptop, natively" is table
stakes for a home product. Because the apps **mostly reuse the existing API**, the
incremental cost is UI/packaging rather than new backend work, and native shells
unlock push approvals, biometric login, and reliable mic/camera that the browser
can't always provide.

**Effort.** **L** for the PWA + Tauri-wrapped path (reuses the web UI and API);
**XL** if a separate Flutter/React Native native app is also pursued and shipped
through both app stores.

**Dependencies.** A stable, documented HTTP/WebSocket API contract; PWA manifest/
service-worker work; push infrastructure; app-store / signing pipelines (shared
with R1 for desktop); the LAN-discovery flow (R3) for first-run connection; native
mic ties to voice items 1.1/1.2.

**Target phase.** **Cross-cutting (future phase)** — start the PWA early; native
shells follow the R1 installer pipeline.

### R3. Login-screen network discovery (find HomeHub on the LAN)

**Description.** At the **login prompt** of any client (web/PWA/native), offer a
**"Scan for HomeHub on my network"** option that uses **mDNS / Bonjour / zeroconf**
to auto-discover a HomeHub advertising itself on the LAN (building on the existing
`llm.local` / avahi advertisement in [`productizing.md`](productizing.md)). If the
scan **finds** a hub, the client connects straight to it; if it **finds none**,
the UI **asks the user to type the hub's IP address** manually. Either way, once
connected, the client drops into the **exact same admin-setup / device-claim
workflow that exists today** (admin approval, device binding, short-TTL token) —
discovery only solves "which box do I talk to," not the trust handshake.

**Rationale / value.** Removing "figure out and type an IP" is the difference
between a relative installing the app and giving up. mDNS makes first-run feel
magical on the common case (hub on the same Wi-Fi), while the manual-IP fallback
keeps it working on segmented/guest networks where multicast is blocked. Crucially
it **reuses the unchanged claim/approval flow**, so it adds convenience without
touching the security model.

**Effort.** **M** (a zeroconf browse step in each client + a connection/IP-entry
UI; the hub-side advertisement already largely exists).

**Dependencies.** Hub advertising an mDNS service record (extends avahi/`llm.local`
from [`productizing.md`](productizing.md)); a zeroconf browser in each client
runtime; the existing admin-setup / device-claim workflow (unchanged); graceful
fallback UX when multicast is unavailable.

**Target phase.** **Cross-cutting** — pairs directly with the installers (R1) and
native/PWA clients (R2) as part of the first-run experience.

### R4. Smart-home / Alexa-like integration (later)

**Description.** Extend the Hub from "answers and learning" into a **household
controller**: discover and operate home appliances and devices (lights, plugs,
thermostats, sensors, media) through the hub, and let users *use* those devices by
voice or chat. Concretely: a **Matter** controller for direct device control, a
**Home Assistant bridge** to ride the largest existing integration ecosystem, an
**intents layer** that maps natural language ("turn off the kitchen lights",
"is the garage door open?") to device actions, and **wake-word / voice** front-end
(builds on wake-word 5.2 and the voice stack) so it behaves like a private,
local Alexa. The pitch: **"everything in one place"** — assistant, tutor, notes,
and home control behind one private hub.

**Rationale / value.** Smart-home control is the feature that turns the Hub into
a daily-use household appliance rather than an occasional tool, and a **fully
local, privacy-first** alternative to Alexa/Google Home is a sharp differentiator
for exactly the audience that already chose a local-LLM box. It also compounds the
voice investment.

**Effort.** **XL** (new control subsystem, Matter/HA integration, an intent/NLU
layer, and voice wiring; many moving parts and ongoing device-compatibility work).

**Dependencies.** Matter controller stack and/or a Home Assistant bridge; an
intent/NLU mapping layer (can lean on the LLM for parsing); wake-word (5.2) and
the voice service (§1) for hands-free use; a device/permissions model for who can
control what; clear local-only privacy posture (see [`privacy.md`](privacy.md)).

**Target phase.** **Later (post-core)** — explicitly a follow-on once installers,
clients, discovery, and the learning mode are solid.

### R5. Kids "Start Learning / Start Study" mode

**Description.** A prominent, **one-tap "Start Learning" / "Start Study" mode** for
kids that drops a child straight into a guided, safe tutoring experience. It ties
directly into the tutor work already in the backlog: the kids-tutor prompts (2.2),
grade-level + subject selectors (2.3), per-kid session history (2.4), and the
parent/teacher progress view (2.5), with read-aloud (1.2) and voice (§1) for
younger or pre-literate learners. The mode presents a simplified, distraction-free,
profile-aware UI (pick the child → pick subject/level → start) rather than the
general chat surface.

**Rationale / value.** Learning is the flagship family use case and the clearest
reason a household buys/installs the Hub. A dedicated, kid-friendly entry point
makes that value **immediately legible** ("press this to study") and packages the
scattered tutor features into a single, marketable mode.

**Effort.** **M** (primarily a mode/UI layer plus glue over already-planned tutor
features; larger if it ships ahead of those dependencies).

**Dependencies.** The Phase-2 tutor items 2.2–2.5; voice/read-aloud (1.1/1.2) for
spoken study; kid-profile concept and the parent/teacher role from 2.4/2.5.

**Target phase.** **P2 (learning depth)** — the consumer-facing front door to the
Phase-2 tutoring work.

### R6. Dedicated secure private hardware hub (later)

**Description.** A purpose-built, **LLM-capable mini-appliance** — a small physical
device a household can buy, plug in, and trust — for users who want a **highly
secure, fully private home hub** that runs our software and interacts with our home
solutions (R4 smart-home, R5 learning, voice, notes) out of the box. It is the
hardware embodiment of the appliance vision in [`productizing.md`](productizing.md):
pre-installed, locked-down, local-only, no cloud dependency.

- **Rough form factor / price band.** A fan-quiet **mini-PC / NUC-class** box (or
  SBC-plus-accelerator for the low end). Two tiers are plausible, and they
  **intentionally trade convenience for a low entry price**:
  - **Entry tier (~$200–400, CPU-only / iGPU).** Voice + 7B chat at the §1
    latencies — **~10–20 s per spoken turn, which is acceptable** for tutoring,
    voice notes, and read-aloud (the **non-real-time** use voice is positioned
    around). **No GPU; no real-time conversation promised.**
  - **Real-time tier (~$600–1200, small GPU/NPU).** Adds the accelerator that
    collapses voice latency to **~2–4 s** (ties to the GPU upgrade path, 5.1) and
    runs heavier models, enabling near-real-time conversation.
  This mirrors the installer positioning (R1): **voice ships CPU-only and
  non-real-time by default; GPU is the prerequisite only for real-time voice**, not
  for shipping voice at all.
- **Build vs partner.** **Partner/white-label first** — source an existing
  mini-PC/SBC from an ODM and ship our pre-imaged, signed software stack on it
  (fastest, lowest capital, avoids hardware-certification burden). Move toward a
  **custom-built** device only if volume, margin, or a distinctive secure-element /
  industrial-design requirement justifies the NRE and supply-chain commitment.

**Rationale / value.** Some buyers don't want to install anything or trust their
own machine's security — they want a sealed, private box they can put on a shelf.
A dedicated device is the **highest-trust, highest-margin** expression of the
privacy-first promise and the natural anchor for the smart-home (R4) and learning
(R5) stories. It also lets us guarantee the hardware profile (RAM/GPU) so the UX is
predictable.

**Effort.** **XL** (a hardware program: sourcing/partner selection, a signed/locked
software image, provisioning, support, and possibly secure-element work — even the
white-label path is a quarters-long effort).

**Dependencies.** A mature, signed, auto-updating software stack from the installer
work (R1); the appliance hardening already discussed in
[`productizing.md`](productizing.md); GPU/NPU sizing (5.1) for the real-time tier;
an ODM/manufacturing partner; per-model NOTICES/license discipline for shipped
weights; a support/RMA path.

**Target phase.** **Later (hardware track)** — the culmination of the
productization roadmap, after the software, clients, and integrations are proven.

### Positioning note

Taken together, these items move the Hub along one arc: from a self-hosted project,
to a **one-click app** (R1) reachable from a **native client on every device**
(R2) that **finds the hub by itself** (R3); then up the value curve into **home
control** (R4) and a **flagship kids-learning mode** (R5); and finally into a
**dedicated, private hardware appliance** (R6) for the highest-trust buyers. The
through-line is *privacy-first, local-by-default, everything-in-one-place* — the
same identity that runs through [`privacy.md`](privacy.md) and
[`productizing.md`](productizing.md). For where this fits competitively (against
cloud assistants and DIY self-hosting), the segments it targets, and how the
Type A/Type B and hardware tiers map to willingness-to-pay, see the market study at
[`/home/kanishka/kk_works/LLMs/docs/market-study.md`](../../docs/market-study.md).
