# Appliance — Models & System Requirements

Authoritative reference for what runs on the local LLM appliance, the per‑model
footprint, and the hardware it needs. Figures below were **measured on the live
box** (AMD Ryzen 5 3600, 16 GB), not estimated.

---

## 1. The stack at a glance

```
browsers (phones/laptops, LAN)
   │  http://llm.home  (or http://<LAN_IP>:8090)
   ▼
Home Hub  :8090/:80   ── chat · notes · checklists · file/photo search · voice · API keys
   ├──► Gateway :8080  (API‑key auth) ──► Ollama 127.0.0.1:11434 ──► Qwen2.5‑7B · moondream · nomic‑embed
   └──► Voice service 127.0.0.1:8100  ──► faster‑whisper (STT) + Kokoro (TTS)
```

Everything runs **locally, CPU‑only** (the GeForce GT 730 is not usable for ML).
All bundled models are **commercially licensed** (Apache‑2.0 / MIT).

---

## 2. Models in use

| Model | Role | Runtime | Disk | RAM when active | License |
|---|---|---|---|---|---|
| **Qwen2.5‑7B‑Instruct** (Q4_K_M) | Chat / reasoning | Ollama → gateway | 4.7 GB | ~5–5.5 GB | Apache‑2.0 |
| **moondream** | Vision (photo captions for search) | Ollama | 1.7 GB | ~1.8–2 GB | Apache‑2.0 |
| **nomic‑embed‑text** | Embeddings (file/photo semantic search) | Ollama | 0.27 GB | ~0.3–0.5 GB | Apache‑2.0 |
| **faster‑whisper `base`** | Speech → text | Voice service | 0.14 GB | ~0.2–0.5 GB | MIT |
| **Kokoro‑82M** (kokoro‑onnx) | Text → speech (voice `af_sarah`) | Voice service | 0.34 GB | ~0.3–0.5 GB | Apache‑2.0 |

**Total model size on disk:** ~7.2 GB (plus ~3–4 GB of Python virtualenvs).

---

## 3. How the models load (important)

- **Ollama loads models on demand, one at a time** — `OLLAMA_MAX_LOADED_MODELS=1`.
  So **Qwen *or* moondream *or* nomic** is resident at any moment, never all
  three. Switching (e.g. a photo upload needing moondream while Qwen is loaded)
  triggers a reload (~a few seconds to ~a minute cold).
- **Idle unload:** `OLLAMA_KEEP_ALIVE=30m` — after ~30 min idle the model is
  evicted from RAM; the next request reloads it. (At rest, `ollama ps` is empty
  and Ollama's own process uses only ~40 MB.)
- **The voice service keeps both models resident** (loaded at startup) — measured
  ~**781 MB** total (faster‑whisper + Kokoro + onnxruntime + Python).
- **Execution is sequential** for voice (transcribe → chat → speak), never
  concurrent — that is what keeps a 16 GB box stable.

---

## 4. Live footprint (measured snapshot)

```
Ollama models loaded : none at rest (load on demand)
Per‑service RAM      : ollama ~40 MB · gateway ~53 MB · home‑hub ~67 MB · voice‑svc ~781 MB
System memory        : 15 GiB total · ~4.4 used · ~10 GiB available · ~8.3 GiB cache
```

**Peak while actively chatting + speaking** (sequential):
Qwen ~5.5 GB + voice‑svc ~0.8 GB + services ~0.2 GB ≈ **~6.5 GB of models/apps
resident**, leaving ~7–9 GB headroom. Comfortable on 16 GB. (If a photo caption
runs, moondream ~2 GB replaces Qwen in Ollama's single slot — it does not stack
on top of it.)

---

## 5. System requirements

| | This box | Minimum to run the appliance | Recommended |
|---|---|---|---|
| **CPU** | Ryzen 5 3600 (6c/12t, AVX2) | x86‑64 **with AVX2**, 4 cores | 6+ cores |
| **RAM** | 16 GB | **16 GB** (8 GB only with a smaller chat model, e.g. Qwen2.5‑3B) | 16–32 GB |
| **Disk free** | 111 GB | ~12 GB (models + venvs) | 20 GB+ |
| **GPU** | none (unused) | none required | optional — collapses voice latency ~10–20 s → ~2–4 s and chat to 30–60 tok/s |
| **OS** | Ubuntu/Linux | Linux x86‑64 | Linux x86‑64 |
| **Network** | home WiFi/LAN | LAN only (never WAN‑expose Ollama :11434) | LAN + the `llm.home` DNS installer |

**Performance on this box (CPU‑only):** Qwen2.5‑7B chat ≈ **6–9 tokens/sec**;
voice round‑trip (speak → answer → speak) ≈ **10–20 s/turn**. Fine for chat,
learning, and voice notes; not real‑time. A GPU is the single biggest speed
lever (see `home-hub/docs/future_enhancements.md`).

---

## 6. Services & ports

| Service | Bind | Purpose | Boot‑persistence |
|---|---|---|---|
| Ollama | `127.0.0.1:11434` | Model runtime (localhost only — no auth) | `install-appliance.sh` → systemd |
| Gateway | `0.0.0.0:8080` | API‑key auth in front of Ollama | systemd `qwen-gateway` |
| Home Hub | `0.0.0.0:8090` (or `:80` as `llm.home`) | Family portal | systemd `home-hub` |
| Voice service | `127.0.0.1:8100` | faster‑whisper + Kokoro | systemd `voice-svc` |

---

## 7. Check live status anytime

```bash
# what's pulled vs loaded right now
OLLAMA_HOST=127.0.0.1:11434 ~/.local/bin/ollama list
OLLAMA_HOST=127.0.0.1:11434 ~/.local/bin/ollama ps

# memory + per-service RAM
free -h
ps -eo rss,args --sort=-rss | grep -E 'ollama serve|--port 80(80|90)|--port 8100' | grep -v grep

# health of each layer
curl -s 127.0.0.1:11434/api/version          # ollama
curl -s 127.0.0.1:8080/healthz               # gateway
curl -s 127.0.0.1:8090/healthz               # hub
curl -s 127.0.0.1:8100/healthz               # voice service
```

---

## 8. Productizing notes (other hardware)

- **AVX2 is required** by the speech/embedding runtimes (ctranslate2 / onnxruntime)
  — true of essentially any CPU since ~2015, but worth checking on very low‑end
  mini‑PCs / ARM SBCs (ARM needs different wheels).
- **8 GB devices** can run voice + embeddings + a **smaller** chat model
  (Qwen2.5‑3B/1.5B) but not the 7B comfortably. Drop `OLLAMA_MAX_LOADED_MODELS`
  to 1 (already set) and avoid concurrent requests.
- **Commercial licensing:** every bundled model is Apache‑2.0 or MIT — safe to
  ship with attribution. One pre‑ship flag: Kokoro's English G2P can fall back to
  eSpeak‑NG (GPL) for rare words (see `home-hub/docs/design.md`).

> Companion doc: **`docs/specialized-models-and-agents.md`** — lightweight,
> domain‑specific models (coding, tutoring, health, 3D/architecture) and an
> honest assessment of whether they add value over the general model on this box.
