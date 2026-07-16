# HomeHub — Feature Tree & Status

Last updated: **2026‑07‑16**

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
│   ├── 🟡 mDNS name homehub.local (works now; systemd unit ready — sudo enable pending)
│   ├── 🟡 Local HTTPS :443 (CA + cert generated & TLS‑verified; unit ready — sudo enable pending)
│   ├── 🟢 Privacy hardening (external telemetry OFF, model libs offline, verified 0 phone‑home)
│   ├── 🟠 Egress firewall allowlist (belt‑and‑suspenders lock; needs sudo)
│   └── 🟢 Off‑box backup to GitHub (code, config, docs — no models/secrets)
│
├── 👨‍👩‍👧 Family & Access
│   ├── 🟢 Device‑bound passwordless auth (httponly cookie, sha256‑hashed tokens)
│   ├── 🟢 Roles (admin / member / guest) + granular privileges
│   ├── 🟢 Admin device approval & revocation UI
│   ├── 🟢 Per‑user API keys (BYO‑key, shown once, hashed at rest)
│   └── 🔵 Encrypted‑at‑rest secret store (needed for cloud providers / HA tokens)
│
├── 🤖 AI Assistant (100 % local — no internet needed)
│   ├── 🟢 Chat (qwen2.5‑7b) with conversations & streaming
│   ├── 🟢 Vision / photo captions (moondream)
│   ├── 🟢 Semantic search embeddings (nomic‑embed‑text)
│   ├── 🟢 Voice chat (mic → STT → LLM → spoken replies)
│   └── 🔵 Cloud AI providers (Anthropic/OpenAI API keys, budgets, per‑user gating)
│
├── 🗂️ Household Content
│   ├── 🟢 Notes (shared noticeboard)
│   ├── 🟢 Checklists
│   ├── 🟢 Files & Photos (upload, shared/private, semantic + photo search)
│   └── 🔵 Shared family calendar & chores (NL entry, ICS import)
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
└── 🏡 Smart Home  (scoped → hybrid roadmap)
    ├── 🔵 Home Assistant integration (device registry, scenes, automations)
    ├── 🔵 Home tab (rooms, live states, per‑user device permissions)
    └── 🔵 Voice + LLM control ("dim the living room to 30 %")
```

## Snapshot by the numbers
| Status | Count | Meaning |
|---|---|---|
| 🟢 Completed | **38** | Built, verified, in production on the appliance |
| 🟡 Partial | **4** | Built & tested; one activation step remains (2× sudo enable, HTTPS‑gated PWA install, router name) |
| 🟠 Pending | **3** | Agreed/required, not yet built — **kids NSFW filter is the critical one** |
| 🔵 Future (Pro) | **30+** | Scoped & feasibility‑verified; companion‑app / backend tier |

## The short "what's next" list
1. **Kids‑safety/NSFW image filter** 🟠 — mandatory before children see generated art.
2. Two sudo one‑liners 🟡 — enable `home-hub-https.service` (unlocks full Android/desktop PWA) and `homehub-mdns.service` (makes `homehub.local` survive reboots).
3. **Egress firewall allowlist** 🟠 — completes the privacy hardening.
4. Then the Pro track per `roadmap-app-shells-and-safety.md` (native shells first).
