# HomeHub — Release‑Readiness Assessment

Status: **living document · last updated 2026‑07‑18.**
Companion docs: the feature tree (`docs/feature-tree.md`), platform activation (`docs/design/platform-activation.md`), secret store (`docs/design/secret-store.md`), cloud providers (`docs/design/cloud-providers.md`), smart home (`docs/design/smart-home.md`), and the Pro‑tier roadmap (`docs/roadmap-app-shells-and-safety.md`).

This is the launch‑readiness ledger — the short list of things that stand between "built and tested on the bench" and "a family (ours, then others) actually using it." Every blocker below is an **actionable item with an owner‑able next step and a Status column**, cross‑referenced to the feature‑tree status it maps to. Keep it current: as items clear, flip the Status and note it in the Changelog.

---

## Verdict

**The appliance is functionally complete and well‑tested.** 93 offline tests pass (home‑hub 69 + qwen‑stack 24 — tmp‑sqlite, no network). What gates a launch is **not** missing features; it is a small, well‑bounded set of **safety, privacy‑enforcement, and data‑durability** items.

- **For our own household:** launch is **days away** — it needs the kids image filter built and the go‑live activation run.
- **For a commercial launch to other families:** the same three hard gates **plus** key‑backup/recovery, onboarding polish, licensing wiring, and legal/privacy groundwork.

The distance to launch is short and the path is known. This doc keeps it honest.

---

## Two launch types (different bars)

The word "launch" means two very different things here, with two different checklists. Don't conflate them.

| Launch type | Who | Bar |
|---|---|---|
| **Personal / family use** | Our own home | Kids image filter built + go‑live activation (restart + sudo + egress lock). **Close.** |
| **Commercial launch** | Other families, paid | The personal bar **plus** key backup/recovery, onboarding polish, licensing wiring, and legal/liability + privacy‑claim enforcement. |

The Hard Release Blockers apply to **both** (no child should use it without them). The Should‑Clear and Commercial sections are what separate a personal go‑live from selling it to strangers.

---

## Hard release blockers (must clear before any child uses it)

These are non‑negotiable. All **open** unless noted.

| # | Blocker | Feature‑tree ref | Why it gates | Next step | Status |
|---|---|---|---|---|---|
| **1** | **Kids‑safety image gate** — NSFW + watermark / third‑party‑IP screen on generated images **before children see them** | 🟠 Kids‑safety / NSFW filter + 🟠 Watermark / IP output check (Image Generation) | **The #1 blocker.** A family product that generates images shown to kids cannot ship without a QA gate on that output. This is the only 🟠 (unbuilt) work on the critical path. | Build the filter (NSFW classifier + watermark/IP screen) inline on the generate path, before the image reaches the gallery/child. See `MEMORY.md → kids-app-nsfw-filter`. | ☐ Open |
| **2** | **Go‑live activation** — bring the merged code live and **enforce** the privacy promise | 🟡 mDNS · 🟡 HTTPS :443 · 🟡 Egress firewall (Core Platform); `docs/design/platform-activation.md` | Until the egress lock is on, "nothing leaves your home" is **UNENFORCED** — a privacy‑credibility gate, not a nicety. The restart also lands the merged calendar / secret‑store / cloud‑providers / smart‑home code. | (a) Restart hub + gateway. (b) `sudo bash installer/enable-platform.sh` (HTTPS :443 + reboot‑proof `homehub.local`). (c) `sudo installer/egress.sh lock` (LAN‑only egress). (d) Verify with `installer/verify-platform.sh` (no sudo). | ☐ Open |
| **3** | **Encryption‑key backup / recovery** — an off‑box copy (or re‑enter flow) for the two Fernet keys | Cross‑cutting; `docs/design/secret-store.md`, `docs/design/cloud-providers.md` | The hub `data/secret.key` and gateway `data/provider.key` (0600, correctly gitignored) are the **only** things that can decrypt stored secrets / provider keys. **Disk death = unrecoverable secrets** — there is deliberately no recovery path in code. Data‑durability gate introduced with the secret store. | Document an off‑box backup of **both** key files (backed up *separately* from their DBs, per each design doc) **or** a documented re‑enter flow for the handful of stored secrets. Ties to blocker #5. | ☐ Open |

> **Why these three and nothing else is "hard":** #1 protects children, #2 makes the headline privacy claim true and turns on the shipped‑but‑dormant code, #3 stops a single disk failure from silently destroying stored credentials. Everything below is polish, not a gate to first use.

---

## Should‑clear for a polished launch (not hard blockers)

Recommended before a wider or paid rollout; a personal go‑live can precede these. All **open**.

| # | Item | Feature‑tree ref | What it is | Status |
|---|---|---|---|---|
| **4** | **First‑run / onboarding smoothness** | 🔵 Simple onboarding / guided first‑run (Wellbeing); Family & Access | Admin bootstrap + device‑approval flow that a **non‑technical family admin** can complete unaided. The auth + approval machinery is 🟢 built; this is UX polish over it. | ☐ Open |
| **5** | **Restore runbook** | 🟢 Off‑box backup to GitHub (Core Platform) | GitHub restores **code**; **data** (`home-hub/data/hub.db`, uploads) and the **keys** need their own documented restore path. Ties to blocker #3. | ☐ Open |
| **6** | **Licensing / commercial wiring** | Not on the tree (untracked `third_party/v0.1.25/vanaheim-*`) | The vanaheim License‑store SDK (`vanaheim-catalog` / `-dart` / `-swift` / `-ts`, currently untracked) gates **monetization, not function**. A free/beta launch can ship without it. | ☐ Open |
| **7** | **Cloud‑provider polish** | 🟡 Cloud AI providers (AI Assistant); `docs/design/cloud-providers.md` | Only needed if shipping cloud AI on day one. Local‑first is the default and makes this **optional** — the code is built and tested, awaiting a key + restart. | ☐ Open |

---

## Not gating (post‑launch, Pro tier)

Explicitly **out of scope** for launch — scoped and feasibility‑verified, delivered later via the companion‑app / backend tier. These are 🔵 on the feature tree and captured in `docs/roadmap-app-shells-and-safety.md`.

- **Native app shells** (Capacitor iOS/Android; desktop Tauri) — unlock lock‑screen + reliable push.
- **Safety & emergency** — wake‑name → call, escalation engine, panic button, fall/fire/CO/leak telemetry.
- **Wellbeing** — reminders, elderly check‑ins, intercom, mood journaling.
- **Find‑my‑device** — LAN inventory, BLE tag finder, room‑level presence, ring‑my‑phone.
- **Smart‑home direct‑control build‑out** — live WebSocket state, voice/LLM control, per‑user permission UI (skeleton 🟡 shipped, `docs/design/smart-home.md`).

None of these blocks a v1 launch of the appliance as it stands.

---

## Commercial & legal notes

Relevant only to the **commercial** launch type; a personal go‑live doesn't need them, but they must be settled before selling to other families.

- **Bundled‑model licensing — already vetted commercial‑safe.** FLUX.1‑schnell is Apache‑2.0 (the default image mode); the bundled set is commercial‑safe. **Caveat:** `sd-turbo` is **non‑commercial** — drafts only, and its output **must not ship as product output**. (Feature tree: 🟢 Licence vetting for bundled models.)
- **Privacy claims must be ENFORCED before they are advertised.** Do not market "nothing leaves your home" until the egress lock is on (blocker #2). App‑level hardening is verified 0 phone‑home, but the firewall lock is the belt‑and‑suspenders that makes the claim defensible.
- **Safety / emergency features (future) carry liability.** When those Pro features ship, ship them with **clear disclaimers** and an explicit "**not a replacement for certified alarms / 911**" stance — mirrors the four load‑bearing realities in the Pro roadmap (no silent 911, certified detectors stay standalone).
- **If commercial, minimum paperwork:** a basic **data‑handling / privacy policy**, a **warranty / liability disclaimer**, and a **support / update channel**.

---

## Recommended sequence

The first three are the **true release gates**; do them in order. The rest are commercial‑only follow‑ons.

1. **Kids filter** (blocker #1) — build the NSFW + watermark/IP gate.
2. **Activate** (blocker #2) — restart → `sudo enable-platform.sh` → `sudo egress.sh lock` → `verify-platform.sh`.
3. **Key‑backup runbook** (blocker #3, ties #5) — document the off‑box backup / re‑enter flow for `secret.key` + `provider.key`.
4. **Onboarding polish** (#4).
5. **(Commercial only)** licensing wiring (#6) + legal/privacy docs.

---

## Definition of "launch ready"

**Personal (own household):**
- ☐ Kids image filter **live** (blocker #1)
- ☐ HTTPS / mDNS / egress **activated** (blocker #2)
- ☐ Key‑backup **done** (blocker #3)
- ☐ A **smoke pass of every tab** (chat, image, calendar, notes, files, home, models…)

**Commercial (other families, paid):** all of the above, **plus**
- ☐ Onboarding polish (#4)
- ☐ Restore runbook (#5)
- ☐ Licensing wired (#6)
- ☐ Legal / privacy docs (privacy policy + liability disclaimer + support channel)

---

## Changelog

- **2026‑07‑18** — Initial release‑readiness assessment. Verdict: functionally complete + 93 offline tests; launch gated by three hard blockers (kids image gate 🟠, go‑live activation 🟡, encryption‑key backup). Two launch types defined (personal vs commercial); should‑clear, not‑gating, commercial/legal notes, recommended sequence, and launch‑ready definitions captured. All blockers open.
