# HomeHub — Feature Tree & Status

Last updated: **2026‑07‑18**

**Legend:** 🟢 Completed & verified · 🟡 Partial (built, one step remains) · 🟠 Pending (agreed/required, not built) · 🔵 Future enhancement (Pro / companion‑app tier)

```
🏠 HomeHub — privacy‑first family AI appliance
│
├── 🏗️ Core Platform & Infrastructure
│   ├── 🟢 Ollama LLM runtime (localhost‑only, never WAN‑exposed)
│   ├── 🟢 API gateway :8080 (API keys, rate limits, OpenAI + Anthropic /v1/messages APIs)
│   ├── 🟢 Home Hub portal :80 (family SPA)
│   ├── 🟢 Voice service :8100 (faster‑whisper STT + Kokoro TTS)
│   ├── 🟢 Image Studio :7860 (FastSD CPU, runs in image mode)
│   ├── 🟢 systemd boot persistence + auto‑restart (hub, ollama, gateway, voice)
│   ├── 🟢 AI ↔ Image mode exclusivity (16 GB RAM guard) + mode scripts
│   ├── 🟢 Resource guards (job serialization, free‑memory check, thread caps, nice)
│   ├── 🟡 mDNS name homehub.local (built & verified — single sudo command to activate: sudo bash installer/enable-platform.sh)
│   ├── 🟡 Local HTTPS :443 (built & verified — same single sudo command: sudo bash installer/enable-platform.sh)
│   ├── 🟢 Privacy hardening (external telemetry OFF, model libs offline, verified 0 phone‑home)
│   ├── 🟡 Egress firewall allowlist (built & verified — per‑service systemd eBPF lock; activate: sudo installer/egress.sh lock; see docs/design/platform-activation.md)
│   ├── 🟢 Tests: offline pytest suites for gateway + hub (93 tests, tmp‑sqlite, no network)
│   └── 🟢 Off‑box backup to GitHub (code, config, docs — no models/secrets)
│
├── 👨‍👩‍👧 Family & Access
│   ├── 🟢 Device‑bound passwordless auth (httponly cookie, sha256‑hashed tokens)
│   ├── 🟢 Roles (admin / member / guest) + granular privileges
│   ├── 🟢 Admin device approval & revocation UI
│   ├── 🟢 Per‑user API keys (BYO‑key, shown once, hashed at rest)
│   └── 🟢 Encrypted‑at‑rest secret store (Fernet, write‑only API, hint‑masked — docs/design/secret-store.md)
│
├── 🤖 AI Assistant (100 % local — no internet needed)
│   ├── 🟢 Chat (qwen2.5‑7b) with conversations & streaming
│   ├── 🟢 Vision / photo captions (moondream)
│   ├── 🟢 Semantic search embeddings (nomic‑embed‑text)
│   ├── 🟢 Voice chat (mic → STT → LLM → spoken replies)
│   └── 🟡 Cloud AI providers (shipped — needs an API key + service restart to go live; docs/design/cloud-providers.md)
│
├── 🗂️ Household Content
│   ├── 🟢 Notes (shared noticeboard)
│   ├── 🟢 Checklists
│   ├── 🟢 Files & Photos (upload, shared/private, semantic + photo search)
│   └── 🟢 Shared family calendar & chores (recurrence + rotation; live after next hub restart — docs/design/family-calendar.md; NL entry/ICS still 🔵)
│
├── 🎨 Image Generation
│   ├── 🟢 FLUX.1‑schnell GGUF — default mode (commercial‑safe Apache‑2.0, TV‑quality)
│   ├── 🟢 sd‑turbo OpenVINO — fast drafts (non‑commercial; not for shipping)
│   ├── 🟢 Source‑built stable‑diffusion.cpp backend (.so, GLIBCXX‑matched)
│   ├── 🟢 Hub‑native gallery + images ribbon (multi‑select, ‹ › pager, click‑to‑centre)
│   ├── 🟢 Processing ops: Image→Image · Remove background · Upscale (Real‑ESRGAN) · Send to Studio
│   ├── 🟢 Drag‑drop segments + action buttons + init‑image staging pane
│   ├── 🟠 Kids‑safety / NSFW filter on generated images (MANDATORY before kids use)
│   └── 🟠 Watermark / third‑party‑IP output check (same QA gate as above)
│
├── 🎬 Studio → Games Pipeline
│   ├── 🟢 Import from Image Studio / upload assets
│   ├── 🟢 Rive .riv upload + vendored Rive runtime (no CDN)
│   ├── 🟢 Auto‑animate (procedural motion WEBP) + Remove animation
│   ├── 🟢 Ready‑state manifest consumed by the games
│   └── 🔵 Direct FLUX → Studio → game‑asset automation
│
├── 🧠 Model Management (admin)
│   ├── 🟢 State machine per model (start / suspend / resume / shutdown)
│   ├── 🟢 Metrics (requests & token histograms, 24 h)
│   ├── 🟢 Resources (disk always; RAM/CPU live; per‑model, per‑service, aggregate)
│   ├── 🟢 Auto‑detect (Ollama reconcile + GGUF folder import + Scan)
│   ├── 🟢 Add‑models catalog (purpose, min RAM, licence badges)
│   ├── 🟢 Download‑state gating (Download → downloading… → installed → Start)
│   └── 🟢 Licence vetting for bundled models (commercial‑safe set)
│
├── 📱 Apps & Reach
│   ├── 🟢 PWA (manifest, offline service worker, icons, install banner)
│   │   ├── 🟢 iOS: Add to Home Screen works today (plain HTTP)
│   │   └── 🟡 Android/desktop full install + offline (auto‑unlocks when HTTPS enabled)
│   ├── 🟡 Friendly URLs (http://homehub.local live; llm.home via router = blocked by Airtel)
│   ├── 🔵 Native iOS/Android shells (Capacitor) — lock‑screen, push, store distribution
│   ├── 🔵 Desktop shell (Tauri) + CI signing pipeline
│   └── 🔵 Proper backend service for Pro features (push relay, telephony, sync)
│
├── 🚨 Safety & Emergency  (scoped → docs/roadmap-app-shells-and-safety.md)
│   ├── 🔵 Wake‑name + "help" → call priority contact (appliance mic + SIP/cellular)
│   ├── 🔵 Escalation engine + family notification fabric (APNs/FCM)
│   ├── 🔵 Hardware panic button · duress phrase · audit log
│   ├── 🔵 Fall detection (mmWave radar) · "are you OK?" inactivity check‑ins
│   ├── 🔵 Fire/smoke · CO · gas · water‑leak telemetry (certified sensors via HA)
│   ├── 🔵 Intrusion alarm · child door/wandering · pool alerts
│   └── 🔵 Medical ID card + location/context on alert
│
├── 💚 Wellbeing & Ease‑of‑Use  (scoped, Pro tier)
│   ├── 🔵 Medication / hydration reminders + adherence
│   ├── 🔵 Elderly daily check‑ins & routine monitoring
│   ├── 🔵 Room intercom / announcements · "call home"
│   ├── 🔵 Comfort & air‑quality dashboards (HA sensors)
│   ├── 🔵 Kids bedtime & hub screen‑time · mood journaling
│   └── 🔵 Accessibility modes (voice‑only, large‑text, high‑contrast) & guided onboarding
│
├── 🔍 Find‑My‑Device  (scoped, Pro tier)
│   ├── 🔵 LAN device inventory (mDNS/ARP/DHCP) + "make it beep/flash" via HA
│   ├── 🔵 BLE tag finder + "where are my keys?" voice query
│   ├── 🔵 Room‑level presence (ESPresense nodes)
│   └── 🔵 Ring family phone (HA Companion critical push)
│
└── 🏡 Smart Home  (hybrid local‑first — skeleton shipped → docs/design/smart-home.md)
    ├── 🟡 Home Assistant integration (provider adapter + REST states/actions + LAN‑only guard; needs an HA link + hub restart)
    ├── 🟡 Home tab (status, connect flow, rooms, device on/off; live after restart)
    ├── 🟡 Per‑user device permissions (per‑entity grant model + store + checks; assignment UI still 🔵)
    ├── 🔵 Live state push (HA WebSocket) + voice + LLM control ("dim the living room to 30 %")
    └── 🔵 Cloud‑by‑exception push bridge (APNs/FCM to locked phones — lands with native shells)
```

## Snapshot by the numbers
| Status | Count | Meaning |
|---|---|---|
| 🟢 Completed | **43** | Built, verified, in production on the appliance (calendar + secret store live after the next hub restart) |
| 🟡 Partial | **9** | Built & tested; one step remains (sudo enable‑platform, egress lock, cloud API key + restart, HTTPS‑gated PWA install, router name, **smart‑home: link HA + restart**) |
| 🟠 Pending | **2** | Agreed/required, not yet built — **kids NSFW filter is the critical one** |
| 🔵 Future (Pro) | **24+** | Scoped & feasibility‑verified; companion‑app / backend tier |

## The short "what's next" list
1. **Kids‑safety/NSFW image filter** 🟠 — mandatory before children see generated art.
2. One sudo command 🟡 — `sudo bash installer/enable-platform.sh` activates HTTPS :443 (full Android/desktop PWA) + reboot‑proof `homehub.local`; then optionally `sudo installer/egress.sh lock` for the LAN‑only egress lock. Verify with `installer/verify-platform.sh` (no sudo).
3. Restart hub + gateway 🟡 — brings the merged calendar, secret store, cloud‑provider and **Smart Home** code live; add a provider API key (+ per‑key opt‑in) only if cloud AI is wanted.
4. Smart Home 🟡 — skeleton shipped (Home tab + Home Assistant adapter, `docs/design/smart-home.md`); after the restart, link an HA instance from the Home tab. Next build‑out: live WebSocket state, voice/LLM control, per‑user device‑permission UI.
5. Then the Pro track per `roadmap-app-shells-and-safety.md` (native shells first).
