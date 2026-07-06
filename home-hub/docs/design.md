# Home LLM Hub — Design & Logic (Authoritative)

This is the authoritative design document for the **Home LLM Hub** appliance
(`llm.home`), including the **voice layer** (speech-to-text + text-to-speech)
that lets family members talk to the local LLM and have replies read aloud.

It describes *what the system is*, *how data flows*, *who is allowed to do
what*, and the *honest resource/latency reality* of running everything locally
on a Ryzen 5 3600 / 16 GB CPU box. It is meant to be accurate, not aspirational:
where something is a future phase or a caveat, it is called out as such.

Scope of the Hub in this design: the Hub is a **client** of three local
upstreams it does **not** own or modify — the **gateway** (`:8080`), **Ollama**
(`:11434`), and the **voice service** (`:8100`). The voice service is a separate
local process the operator runs; **the Hub does not build or run it**, it only
integrates with its HTTP contract.

---

## 1. Architecture

Everything runs on **one host**, on the home LAN. Browsers (phones, tablets,
laptops) talk to the Hub over plain HTTP. The Hub fans out over **loopback** to
the gateway, Ollama, and the voice service. **No cloud, ever.** There is no
cloud fallback by design (privacy-first); if a local service is down, the
feature degrades or disables, it does not phone home.

```
                         HOME LAN  (trusted WiFi)
   +---------------------------------------------------------------------+
   |  Browsers: phones / tablets / laptops                               |
   |  getUserMedia + MediaRecorder (mic)  ·  <audio> playback            |
   |  hub_device cookie (httponly)  ·  X-Hub-CSRF: 1 on writes           |
   +-----------------------------------+---------------------------------+
                                       | HTTP  (LAN, plain)
                                       | http://llm.home  (:80)
                                       | or  http://<LAN_IP>:8090
                                       v
   +---------------------------------------------------------------------+
   |                       HOME LLM HUB  (FastAPI / uvicorn)             |
   |              llm.home:80   (rootless via setcap)  or  0.0.0.0:8090  |
   |                                                                     |
   |   /api/session/*   device auth (TOFU), claim admin, logout         |
   |   /api/conversations/* , /messages   chat (SSE streaming)          |
   |   /api/notes , /api/checklists , /api/files , /api/search          |
   |   /api/keys , /api/admin/*                                          |
   |   /api/voice/transcribe , /api/voice/speak , /api/voice/health     |
   |                                                                     |
   |   SQLite (devices, conversations, notes, checklists, file index,   |
   |           per-user key id+prefix)  ·  data/uploads                 |
   +--------+---------------------------+-----------------+--------------+
            |                           |                 |
            | Bearer HUB_GATEWAY_KEY    | embeddings /    | multipart audio /
            | /v1/chat/completions      | vision caption  | JSON text
            | (SSE)                     | (no auth)       | (no auth; loopback)
            v                           v                 v
   +---------------------+   +---------------------+   +-----------------------+
   | QWEN STACK GATEWAY  |   |   OLLAMA            |   |  VOICE SERVICE        |
   | 127.0.0.1:8080      |   |   127.0.0.1:11434  |   |  127.0.0.1:8100       |
   | OpenAI-compatible   |   |   loopback only    |   |  (separate local proc;|
   | key auth + routing  |   |                    |   |   the Hub is a CLIENT) |
   +----------+----------+   |  nomic-embed-text  |   |                       |
              |             |  moondream (vision) |   |  faster-whisper "base"|
              v             +----------+----------+   |    (STT, MIT)         |
   +---------------------+              |             |  kokoro-onnx /        |
   |   OLLAMA            |<-------------+             |    Kokoro-82M         |
   |   127.0.0.1:11434  |                            |    (TTS, Apache-2.0)  |
   |   Qwen2.5-7B (Q4)  |   chat inference            |  default voice af_sarah|
   |   Apache-2.0        |                            +-----------------------+
   +---------------------+

   Network-facing services: the Hub (and the gateway, on its own port).
   Ollama and the voice service are LOOPBACK-ONLY and have no auth of their own
   — they must NEVER be exposed to the LAN/WAN. The Hub is their only client.
```

### Why the Hub fronts everything

- The browser never talks to Ollama, the gateway, or the voice service
  directly. The Hub is the **single network-facing application** that holds the
  gateway key, enforces per-user authorization, and proxies to the loopback
  upstreams. This keeps Ollama and the voice service — neither of which has
  authentication — safely on `127.0.0.1`.
- The Hub adds **identity, privilege, and CSRF** on top of upstreams that have
  none (Ollama, voice) or only coarse key auth (gateway).

---

## 2. Components, responsibilities & ports

| Component | Bind | Auth | Responsibility |
|-----------|------|------|----------------|
| **Browser client** | n/a | device cookie + CSRF header | UI, mic capture (`getUserMedia`+`MediaRecorder`), `<audio>` playback, SSE rendering |
| **Home LLM Hub** | `0.0.0.0:8090` (or `:80` via setcap) | passwordless device-bound + privileges | App server: sessions, chat, notes, checklists, files/photos, keys, admin, **voice proxy** |
| **Qwen Stack gateway** | `127.0.0.1:8080` | gateway API key (Bearer) | OpenAI-compatible `/v1/chat/completions`; mints/revokes per-user keys |
| **Ollama** | `127.0.0.1:11434` | none (loopback) | Runs Qwen2.5-7B (chat, via gateway), `nomic-embed-text` (embeddings), `moondream` (vision captions) |
| **Voice service** | `127.0.0.1:8100` | none (loopback) | `faster-whisper` STT + `kokoro-onnx` TTS; **separate process, not built here** |

The Hub's own code map (FastAPI routers under `app/`):

| File | Routes | Notes |
|------|--------|-------|
| `app/main.py` | `/`, `/healthz`, mounts routers + error handlers | App factory |
| `app/routes_session.py` | `/api/me`, `/api/session/{register,claim,logout}` | TOFU device identity |
| `app/routes_chat.py` | `/api/conversations*`, `/messages` | SSE streaming to gateway |
| `app/routes_notes.py`, `routes_checklists.py` | notes / checklists CRUD | per-user ownership |
| `app/routes_files.py`, `indexer.py` | files/photos + `/api/search` | embeddings + vision captions |
| `app/routes_keys.py`, `routes_admin.py` | per-user keys, device admin | gateway key mint/revoke |
| **`app/routes_voice.py`** | **`/api/voice/transcribe`, `/speak`, `/health`** | **proxy to `VOICE_URL`** |
| `app/auth.py` | device tokens, CSRF, privilege deps | fail-closed |
| `app/integration.py` | httpx clients to gateway + Ollama | the Hub as a client |
| `app/config.py` | env + defaults | `VOICE_URL`, `VOICE_DEFAULT_VOICE`, `MAX_VOICE_BYTES` |

### Voice endpoints (as implemented in `app/routes_voice.py`)

All three require an **approved device with the `chat` privilege**; the two
POSTs additionally require the `X-Hub-CSRF: 1` header. The Hub never exposes the
voice service's URL or raw errors to the browser.

```
POST /api/voice/transcribe   multipart/form-data field "audio"
                             -> {"text": str, "language": str}
                             (proxied to  POST VOICE_URL/transcribe)

POST /api/voice/speak        JSON {"text": str, "voice"?: str, "speed"?: float}
                             -> audio/wav bytes
                             (proxied to  POST VOICE_URL/speak)

GET  /api/voice/health       -> {"available": bool, "stt": str?, "tts": str?, "voice": str?}
                             (proxied to  GET  VOICE_URL/healthz; never leaks
                              the upstream URL or error text — failure => {"available": false})
```

Limits & defaults the Hub enforces before proxying:
- `MAX_VOICE_BYTES` (default **25 MB**) caps uploaded audio; empty audio is `400`.
- `speak` defaults `voice` to `VOICE_DEFAULT_VOICE` (`af_sarah`) when omitted,
  and validates `speed` is numeric.

### The voice service contract (the Hub depends on, does NOT implement)

```
GET  /healthz     -> {"status":"ok","stt":"faster-whisper:base",
                      "tts":"kokoro-onnx","voice":"af_sarah"}
POST /transcribe  -> multipart "audio" (webm/ogg/wav/mp4), optional form
                     "language"  ->  {"text": str, "language": str}
POST /speak       -> JSON {"text": str, "voice"?: str, "speed"?: float}
                     ->  audio/wav bytes
```

---

## 3. Voice data flow

### 3a. Talk loop (mic -> transcribe -> chat -> Qwen -> speak -> play)

The talk loop **reuses the existing chat path**. Voice is a front-end on the
same `POST /api/conversations/{id}/messages` flow — transcription produces text
that is sent as a normal chat message; synthesis happens after the reply lands.
Execution is **strictly sequential** end to end (see §6).

```
 Browser                         Hub                    Gateway/Qwen   Voice svc
   |                              |                         |             |
   | (1) tap mic; MediaRecorder   |                         |             |
   |     captures webm/ogg blob   |                         |             |
   |----------------------------->|                         |             |
   | (2) POST /api/voice/transcribe (multipart audio)       |             |
   |                              |--(3) POST /transcribe -------------->  |
   |                              |        (faster-whisper base, CPU)      |
   |                              |<-------------- {"text","language"} ----|
   |<-- {"text, language"} -------|                         |             |
   |                              |                         |             |
   | (4) place text in composer;  |                         |             |
   |     send as a chat message   |                         |             |
   | POST /conversations/{id}/messages (stream:true)        |             |
   |----------------------------->|--(5) /v1/chat/completions (SSE)----->  |
   |                              |        Qwen2.5-7B generates            |
   |<==== SSE deltas (assistant streamed token-by-token) ===|             |
   |                              |  (assistant msg persisted to SQLite)   |
   |                              |                         |             |
   | (6) IF "speak replies" toggle is ON and this message   |             |
   |     came from the mic (nextMsgFromVoice):              |             |
   | POST /api/voice/speak {text: <assistant reply>}        |             |
   |                              |--(7) POST /speak -------------------->  |
   |                              |        (kokoro-onnx, af_sarah, CPU)    |
   |                              |<------------------ audio/wav ----------|
   |<-------- audio/wav ----------|                         |             |
   | (8) play via <audio id=voice-audio>                    |             |
```

Front-end state backing this (already present in `app/static/app.js` /
`templates/index.html`):
- `voiceAvailable` — set from `GET /api/voice/health`; gates the whole UI. If
  the voice service is down, the mic button and read-aloud controls stay hidden.
- `recorder` / `recStream` — the active `MediaRecorder` and `getUserMedia`
  stream.
- `nextMsgFromVoice` — marks that the message currently being sent originated
  from the mic, so auto-speak only fires for spoken turns (not typed ones).
- The "speak replies" toggle (`#speak-replies-toggle`) controls step (6).
- `#voice-audio` is the hidden `<audio>` element used for playback.

### 3b. Read-aloud (any message / note)

Independent of the talk loop: a **read-aloud** control on any assistant message
(and, by extension, any note body) calls `POST /api/voice/speak` with that
text and plays the returned WAV. No mic involved; this is pure TTS. It is gated
on `voiceAvailable` the same way.

```
 Browser                         Hub                      Voice svc
   |  tap "read aloud" on a message/note                     |
   | POST /api/voice/speak {text}                            |
   |----------------------------->|--- POST /speak --------->|
   |                              |<-------- audio/wav -------|
   |<-------- audio/wav ----------|                          |
   |  play via <audio>            |                          |
```

### 3c. Voice note -> summarize (FUTURE, Phase 2)

Not built yet; captured here as the intended flow. A longer recording is
transcribed via the same `/api/voice/transcribe`, then the transcript is sent
to Qwen with a "summarize this" prompt, and the summary is saved as a Note via
the existing `/api/notes` path. No new voice endpoint is required — it composes
transcribe + chat + notes. Phase 2 also adds kids-tutor prompts and multilingual
voice selection.

---

## 4. Auth & privacy

The voice layer inherits the Hub's existing security model **unchanged** — it
adds no new identity or trust concept.

- **Privilege gating.** Every voice route depends on
  `auth.require_privilege("chat")`: the device must be **approved** (not
  `pending`) and hold the **`chat`** privilege. Guests get `chat` by default;
  pending devices get `403`. There is intentionally **no separate `voice`
  privilege** — voice is just another way to chat, so it rides the same gate.
- **CSRF.** `transcribe` and `speak` are POSTs, so the custom header
  `X-Hub-CSRF: 1` is required (enforced inside `require_privilege`). Browsers
  cannot set custom headers on cross-site requests; combined with
  `SameSite=Lax` cookies this defeats CSRF. Missing header => `403`.
- **Device identity.** Same passwordless, device-bound cookie (`hub_device`,
  httponly, 40-hex token stored only as a sha256 hash). New devices self-
  register as `pending`/`guest` and need admin approval.
- **Audio is NOT persisted (Phase 1).** Recorded audio is streamed
  browser -> Hub -> voice service and back; the Hub **does not write audio to
  disk or SQLite**. The upload is read into memory, size-checked against
  `MAX_VOICE_BYTES`, forwarded, and discarded. Transcribed text only persists
  if/when the user sends it as a chat message (then it lives in the
  `messages` table like any other chat text). Synthesized WAV is streamed
  through and not stored.
- **Everything local; no cloud.** STT and TTS run on this host. There is **no
  cloud STT/TTS fallback** — by design. If the voice service is unreachable,
  `/api/voice/health` returns `{"available": false}` and the UI hides voice
  controls (offline-robust via healthz gating). The Hub never leaks the voice
  service URL or its internal errors to the client.
- **Loopback upstreams.** The voice service, like Ollama, has **no auth of its
  own** and must stay on `127.0.0.1`. The Hub is its only client; never bind it
  to the LAN.
- **Plain HTTP on the LAN.** As with the rest of the Hub, traffic (including the
  recorded audio and the device cookie) is unencrypted on the wire. This is the
  trusted-LAN threat model; add TLS (and flip `COOKIE_SECURE` on) if you need
  wire confidentiality.

---

## 5. Chosen stack & why

Selected for **commercial-safe licensing**, **all-local CPU operation**, and
**English-first** (Phase 1). All choices verified against the licensing reality
below.

### Speech-to-text: `faster-whisper`, model `base`

- **License: MIT** (the `faster-whisper` library; the Whisper weights are MIT
  too). Cleanly commercial-usable, no copyleft, no non-commercial clause.
- **Why `base`:** a pragmatic accuracy/latency trade-off on a CPU box. Larger
  Whisper models are noticeably more accurate but multiply transcription time;
  `base` keeps a spoken turn inside the ~10–20 s budget on the Ryzen 5 3600.
- CTranslate2-backed, runs well on CPU, supports the `webm/ogg/wav/mp4` blobs
  `MediaRecorder` produces.

### Text-to-speech: `kokoro-onnx` running **Kokoro-82M**, voice `af_sarah`

- **License: Apache-2.0** (Kokoro-82M weights). Commercial-usable with the usual
  Apache attribution requirement. `kokoro-onnx` runs the model via ONNX Runtime
  on CPU.
- **Why Kokoro:** small (82M params), fast enough on CPU for short replies,
  natural-sounding, and — critically — **Apache-2.0**, unlike most other
  high-quality open TTS. Default English voice **`af_sarah`** (overridable per
  request via the `voice` field, default set by `VOICE_DEFAULT_VOICE`).

### Why the rejected options are NOT recommended as commercial-safe

| Option | Status | Reason rejected |
|--------|--------|-----------------|
| **Piper** | **GPL-3.0** (since Oct 2025) | Copyleft; relicensed away from a permissive license — unsafe to bundle in a closed commercial product. |
| **Coqui / XTTS** | **CPML** | Coqui Public Model License is **non-commercial** — disqualifying. |
| **eSpeak-NG** | **GPL** | Copyleft. (Note the indirect-use caveat in §7.) |
| **Moonshine (non-English)** | **Community License (non-commercial)** | Non-English Moonshine is under a non-commercial community license. |

These are listed explicitly so nobody later mistakes them for "open == safe."
Open weights are **not** the same as a commercial-usable license.

---

## 6. Sequential execution, resource budget & honest latency

**Hard rule: voice runs SEQUENTIALLY, never concurrently.** A spoken turn is
`transcribe -> Qwen chat -> speak`, one stage at a time. We do **not** overlap
STT, LLM inference, and TTS — on a 6-core CPU with one 7B model loaded, doing so
would thrash and make latency *worse*, not better. The chat model load policy is
unchanged: **`OLLAMA_MAX_LOADED_MODELS=1`** stays as-is; voice does not add a
second always-loaded LLM.

### Honest latency (Ryzen 5 3600 / 16 GB, CPU only)

- **Realistic end-to-end: ~10–20 s per spoken turn**, dominated by Qwen2.5-7B
  generation on CPU, plus a few seconds each for `faster-whisper base`
  transcription and Kokoro synthesis.
- This is **not real-time** and is not pitched as such. It is good for
  **kids-learning, voice notes, and read-aloud**, where a short wait is fine.
  It is **not** suitable for live conversational back-and-forth.

### Resource budget

- **RAM peak ~9–10 GB** with Qwen2.5-7B (Q4) loaded plus the STT/TTS models and
  the Hub. On a 16 GB box that leaves headroom but is the dominant consumer;
  do not also run a second large model.
- The voice service is its own process with its own RSS; the sequential rule
  keeps **peak** CPU demand to one heavy stage at a time even though the
  processes coexist.
- Generous httpx read timeouts (300 s) are set on the Hub's voice and gateway
  clients precisely because CPU stages are slow — a timeout here means "the box
  is busy," not "real-time failed."

### The GPU upgrade path

A GPU collapses the budget to roughly **~2–4 s/turn** (Qwen on GPU dominates the
win; STT/TTS also accelerate). This is the single highest-leverage future
change and is noted as the path to near-real-time, but Phase 1 is honest CPU
numbers.

---

## 7. The eSpeak-NG (GPL) G2P consideration — pre-ship flag

**Flagged for commercial shipping, not a Phase 1 blocker for personal use.**

Kokoro's English grapheme-to-phoneme (G2P) step can fall back to **espeak-ng**
(via `espeakng-loader`) for **out-of-dictionary words** — words not covered by
its built-in lexicon. **espeak-ng is GPL.** Pulling it into a distributed
commercial product raises a copyleft question even though the Kokoro weights
themselves are Apache-2.0.

Action for a commercial ship (capture, don't solve here):
- Audit whether espeak-ng is actually invoked at runtime for your text.
- Pursue an **espeak-free G2P path** (dictionary-only or an alternative
  permissively-licensed phonemizer) before shipping commercially.
- This is a **packaging/licensing** concern, not a functionality one — local
  personal use is unaffected.

Per-voice license auditing (each Kokoro voice's provenance) is a related
pre-ship task to track alongside this.

---

## 8. Configuration / environment

Voice-relevant config lives in `app/config.py` and is overridable via `.env`
(loaded by python-dotenv; the systemd unit reads the same file via
`EnvironmentFile=`).

| Var | Default | Meaning |
|-----|---------|---------|
| `VOICE_URL` | `http://127.0.0.1:8100` | Base URL of the separate local voice service. Keep on loopback. |
| `VOICE_DEFAULT_VOICE` | `af_sarah` | TTS voice used when `speak` omits `voice`. |
| `MAX_VOICE_BYTES` | `26214400` (25 MB) | Max uploaded audio the Hub will proxy to `/transcribe`. |
| `HUB_HOST` / `HUB_PORT` | `0.0.0.0` / `8090` | Hub bind (or `:80` via setcap). Unchanged by voice. |
| `GATEWAY_URL` / `HUB_GATEWAY_KEY` | `127.0.0.1:8080` / (set) | Chat upstream (unchanged). |
| `OLLAMA_URL` | `127.0.0.1:11434` | Embeddings + vision (unchanged). |

No new secret is introduced for voice: the voice service is loopback and
unauthenticated, so there is nothing to put in `.env` beyond its URL.

---

## 9. Coexistence with the existing systemd services

The voice layer adds **no change** to how the Hub is deployed or supervised:

- The Hub runs under **`home-hub.service`** (rootless uvicorn as the project
  owner; `:80` via setcap on the venv python, or `:8090` rootless). That unit is
  unchanged — the voice routes are part of the same FastAPI app, mounted in
  `app/main.py`. No new Hub unit, port, or capability is needed.
- The Hub's unit already declares `After=/Wants=` the gateway and Ollama units.
  The **voice service is operationally optional**: the Hub starts and runs fine
  without it; voice features simply report `available: false` and hide
  themselves. If you run the voice service as its own systemd unit, the Hub does
  **not** need a hard `Requires=` on it — the healthz gate handles absence
  gracefully (offline-robust, no cloud fallback).
- The voice service binds `127.0.0.1:8100` and, like Ollama, must never be
  exposed to the LAN. It is a **separate process the operator runs**; building
  and packaging it is out of scope for the Hub.
- Model-load policy stays put: `OLLAMA_MAX_LOADED_MODELS=1`. Adding voice does
  not change Ollama's footprint (faster-whisper and Kokoro are not Ollama
  models; they live in the voice service).

---

## 10. Phase plan & open proposals

**Phases**
- **P1 (this build):** mic capture + `/api/voice/transcribe` into chat +
  read-aloud (`/api/voice/speak`); English-only; sequential CPU; healthz gating.
- **P2:** kids-tutor prompts; voice-note -> summarize -> save as Note;
  multilingual voices + a voice selector.
- **P3:** pronunciation / phoneme scoring (e.g. allosaurus / wav2vec).

**Open proposals to capture (not commitments)**
- Multilingual voices + UI selector.
- Grade/subject selectors + per-kid progress tracking + a parent view.
- Pronunciation/phoneme scoring.
- Streaming STT (partial transcripts) and, with a GPU, streaming end-to-end.
- GPU upgrade path (collapses latency to ~2–4 s/turn).
- Wake-word / hands-free capture; a native app.
- Continued offline robustness via healthz gating (privacy-first; never a cloud
  fallback).
- Per-voice license auditing and an **espeak-free G2P** path before any
  commercial ship (see §7).

---

## 11. Failure modes (honest)

| Condition | Behavior |
|-----------|----------|
| Voice service down | `/api/voice/health` -> `{"available": false}`; UI hides mic + read-aloud. No cloud fallback. |
| Mic permission denied | Browser blocks `getUserMedia`; voice UI disabled; typed chat unaffected. |
| Audio > `MAX_VOICE_BYTES` | Hub returns `400 too_large` before contacting the voice service. |
| Empty recording | Hub returns `400 bad_request`. |
| Device pending / no `chat` privilege | `403` on all voice routes (fail closed). |
| Missing `X-Hub-CSRF` on POST | `403 csrf_missing`. |
| Voice service errors / timeout | Hub returns `502 voice_error`; upstream URL/error text never leaked to the client. |
| Gateway/Qwen slow | Spoken turn just takes longer (sequential); 300 s read timeout covers slow CPU. |
```
