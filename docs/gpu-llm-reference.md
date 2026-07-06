# LLMs for a 12 GB / 2×12 GB (24 GB) GPU — reference

Tuned for **GGUF via Ollama/llama.cpp** (this project's stack). Sizes assume the
common **Q4_K_M** quant unless noted, moderate context (4–8k), and ~1.5–3 GB of
headroom for the KV cache. Licenses are flagged because this ships in a paid
product. *(Snapshot as of early 2026 — models move fast; re-verify at deploy.)*

## VRAM quick math
| Quant | GB per 1B params | Notes |
|---|---|---|
| Q4_K_M | ~0.55 | the sweet spot |
| Q5_K_M | ~0.65 | slightly better, if it fits |
| Q6_K | ~0.8 | near-lossless-ish |
| Q8_0 | ~1.1 | effectively lossless |
| FP16 | ~2.0 | rarely worth it locally |

Plus **KV cache** (grows with context × layers) — leave headroom or quantize it
(`--kv-cache-type q8_0`). Rough max params ≈ (VRAM for weights) ÷ (GB/1B).
- **12 GB** → ~9–10 GB for weights → ~14B @ Q4 comfortable, 7–9B @ Q6–Q8.
- **24 GB** → ~20–21 GB for weights → ~32–34B @ Q4, 14B @ Q8.

---

## Tier A — single 12 GB
Comfortable: **7–9B at Q6–Q8**, or **12–14B at Q4–Q5**.

| Model | Params | Quant here | License | Use |
|---|---|---|---|---|
| **Qwen2.5-14B / Qwen3-14B** | 14B | Q4_K_M | Apache-2.0 ✅ | best all-round |
| **Phi-4** | 14B | Q4_K_M (~9 GB) | **MIT ✅** | strong reasoning, cleanest license |
| **Gemma 3 12B** | 12B | Q4/Q5 | Gemma terms | multimodal, 128k ctx |
| **Mistral Nemo 12B** | 12B | Q4_K_M | Apache-2.0 ✅ | 128k ctx, multilingual |
| **Qwen2.5-7B / Qwen3-8B** | 7–8B | Q6_K/Q8 | Apache-2.0 ✅ | your current 7B, at full quality |
| **Llama 3.1 8B** | 8B | Q8_0 | Llama license | huge ecosystem/tooling |
| **Granite 3.x 8B** | 8B | Q6/Q8 | Apache-2.0 ✅ | enterprise/RAG |
| **Qwen2.5-Coder-14B** | 14B | Q4_K_M | Apache-2.0 ✅ | coding |
| **DeepSeek-R1-Distill-Qwen-14B** | 14B | Q4_K_M | MIT ✅ | reasoning/chain-of-thought |
| **Qwen2.5-VL-7B / Pixtral 12B / Llama 3.2 11B-Vision** | 7–12B | Q4 | Apache / Apache / Llama | vision-language |

Stretch (tight, quality drops): Mistral Small 24B or Gemma 2/3 27B at **Q3/IQ3** —
possible but not recommended on 12 GB.

---

## Tier B — 2×12 GB = 24 GB (layer-split or tensor-parallel)
Comfortable: **27–34B at Q4–Q5**, or **14B at Q8**, or a **30B MoE**.

| Model | Params | Quant here | License | Use |
|---|---|---|---|---|
| **Qwen2.5-32B / Qwen3-32B** | 32B | Q4_K_M (~20 GB) | Apache-2.0 ✅ | top general model at this tier |
| **Qwen3-30B-A3B (MoE)** | 30B / 3B active | Q4–Q5 | Apache-2.0 ✅ | ~32B quality, **much faster** (3B active) |
| **Mistral Small 3 / 3.2 (24B)** | 24B | Q5/Q6 | Apache-2.0 ✅ | excellent + permissive |
| **Gemma 3 27B** | 27B | Q4/Q5 | Gemma terms | strong multimodal, 128k |
| **Gemma 2 27B** | 27B | Q5_K_M | Gemma terms | solid text |
| **Qwen2.5-Coder-32B** | 32B | Q4_K_M | Apache-2.0 ✅ | best local coder |
| **DeepSeek-R1-Distill-Qwen-32B** | 32B | Q4_K_M | MIT ✅ | best local reasoning at 24 GB |
| **Yi-1.5 34B** | 34B | Q4_K_M | Apache-2.0 ✅ | general |
| **Mixtral 8x7B** | 47B / 13B active | Q3_K_M (tight) | Apache-2.0 ✅ | older but fast MoE |
| **Qwen2.5-VL-32B** | 32B | Q4 | Qwen (Apache for most) | vision at scale |
| **Command-R 35B** | 35B | Q4 | **CC-BY-NC ❌** | great RAG but NON-COMMERCIAL — avoid |
| **Llama 3.3 70B** | 70B | only IQ2_XXS/Q2 (~20–22 GB) | Llama license | *fits but degraded* — not recommended |

### Doesn't fit 24 GB (needs more VRAM)
- **70B @ Q4** (~40 GB) → needs 48 GB (2×24) or slow CPU offload.
- Qwen2.5-72B, Llama 405B, DeepSeek-V3/R1 full (671B MoE) — far beyond.

---

## Licensing cheat-sheet (paid product)
- ✅ **Fully permissive** — Apache-2.0 (Qwen*, Mistral/Nemo/Small/Mixtral/Pixtral, Yi, Granite) · MIT (Phi, DeepSeek-R1 distills).
- ✅ **Commercial with terms** — Llama (<700 M MAU, show "Built with Llama") · Gemma (prohibited-use policy) · OpenRAIL (StarCoder2 — use restrictions).
- ⚠️ **Restricted / avoid for product** — Qwen2.5-**3B** (research/NC) · **Codestral** (MNPL, no commercial hosting) · **Command-R/R+** (CC-BY-NC).
- Re-check every model's card at ship time.

## Top picks by job
- **Default family chat (commercial-safe):** Qwen3-14B (12 GB) → Qwen3-32B or Mistral Small 24B (24 GB).
- **Cleanest license:** Phi-4 (MIT).
- **Coding:** Qwen2.5-Coder-14B → -32B.
- **Reasoning:** DeepSeek-R1-Distill-Qwen-14B → -32B.
- **Vision:** Qwen2.5-VL-7B / Pixtral 12B → Gemma 3 27B / Qwen2.5-VL-32B.
- **Speed at 24 GB:** Qwen3-30B-A3B (MoE).

## Serving two GPUs
- **Ollama** (this stack): auto-splits across GPUs; set `OLLAMA_SCHED_SPREAD=1` to spread layers. Just `ollama pull` a 32B and it uses both. Add it in the Home Hub **Models** tab like any other.
- **llama.cpp:** `-ngl 999 --tensor-split 1,1` (or `-ts`).
- **vLLM:** `--tensor-parallel-size 2` (fastest serving; wants even attention-head counts; best with NVLink but works over PCIe).
- **ExLlamaV2 / TabbyAPI:** fast EXL2 quants, good on dual consumer cards.
- **KV-cache quant** (`--kv-cache-type q8_0`) frees VRAM for a bigger model or longer context.
- Reality: 2×12 GB without NVLink adds inter-GPU latency — throughput is a bit below a single 24 GB card, but **capacity** (fitting a 32B) is the point.

---

# CPU-only LLMs (no GPU) — 16 GB now, up to 32 GB

For the current box (**Ryzen 5 3600, DDR4 ~45 GB/s, no usable GPU**) and a cheap
RAM bump to 32 GB. CPU inference is **memory-bandwidth bound**: speed ≈ bandwidth ÷
size of the weights read per token. Two rules that decide everything:
1. **Q4_K_M is the CPU sweet spot** (best size/quality/speed).
2. **MoE models are the CPU superpower** — they only read the *active* experts per
   token, so a 30B MoE runs at ~7B speed while giving ~30B quality. Catch: the
   **whole** model must fit in RAM.

### Rough generation speed on your Ryzen 5 3600 (Q4)
| Model | Weights read/token | tok/s (approx) | Feel |
|---|---|---|---|
| 3–4B dense | 3–4B | ~12–18 | snappy |
| 7–8B dense | 7–8B | ~6–9 | comfortable (your current 7B) |
| 14B dense | 14B | ~3.5–5 | usable, a bit slow |
| **30B-A3B MoE** | ~3B active | ~8–12 | fast for the quality ⭐ |
| 47B MoE (Mixtral) | ~13B active | ~3–4 | workable |
| 32B dense | 32B | ~1.5–2.5 | batch/patient only |
*(Prompt/prefill is faster — it's compute-bound and parallel across your 6 cores.)*

## 16 GB RAM (current) — leave room for the OS + hub services
| Model | Size | Quant | License | Notes |
|---|---|---|---|---|
| Qwen3-4B · Llama 3.2 3B · Gemma 3 4B · Phi-3.5-mini | 3–4B | Q4–Q6 | Apache/Llama/Gemma/MIT | snappy; great for tools/agents |
| **Qwen2.5-7B / Qwen3-8B** | 7–8B | Q4_K_M | Apache-2.0 ✅ | your current default — the sweet spot |
| Llama 3.1 8B · Ministral 8B · Granite 3 8B | 8B | Q4 | Llama / Apache ✅ | solid alternatives |
| Qwen2.5-14B · **Phi-4 14B** | 14B | Q4_K_M (~8.5 GB) | Apache / **MIT ✅** | fits if you stop other services; ~4 tok/s |
| Qwen2.5-Coder-7B | 7B | Q4 | Apache ✅ | coding |
| Vision: moondream · Qwen2.5-VL-3B · Gemma 3 4B | 3–4B | Q4 | Apache/Gemma | slow but fine for captions |
Doesn't fit well at 16 GB: 30B MoE (~18 GB), 32B dense.

## 32 GB RAM (slight upgrade) — recommended; unlocks MoE
| Model | Size | Quant | License | Notes |
|---|---|---|---|---|
| **Qwen3-30B-A3B (MoE)** | 30B / 3B active | Q4_K_M (~18 GB) | Apache-2.0 ✅ | ⭐ **star pick** — ~30B quality at ~7B speed |
| Qwen2.5-14B / Qwen3-14B / Phi-4 | 14B | Q5–Q8 | Apache / MIT ✅ | higher-quality dense, comfortable |
| Mixtral 8x7B (MoE) | 47B / 13B active | Q4 (~26 GB) | Apache-2.0 ✅ | fast MoE, good general/RAG |
| Qwen2.5-32B / Qwen3-32B (dense) | 32B | Q4 (~19 GB) | Apache-2.0 ✅ | best quality but ~2 tok/s (patient) |
| Qwen2.5-Coder-14B | 14B | Q4–Q6 | Apache ✅ | coding |
| DeepSeek-R1-Distill-Qwen-7B/8B | 7–8B | Q4 | MIT ✅ | reasoning (thinks a lot → slower *effective* speed) |
| Vision: Qwen2.5-VL-7B · Gemma 3 12B | 7–12B | Q4 | Apache/Gemma | usable |

**Best upgrade for this appliance:** **32 GB + Qwen3-30B-A3B** — the single biggest
CPU quality jump. Much smarter than the current 7B at roughly the same speed, and it
drops straight into Ollama + the Home Hub Models tab.

## CPU tuning tips
- Threads = **physical cores (6)**, `OLLAMA_NUM_PARALLEL=1`; keep context modest (4–8k) to limit KV cache.
- Quantize the KV cache (`--kv-cache-type q8_0`) for more headroom.
- Faster RAM (DDR4-3600) with **both channels populated** = directly more tok/s (it's bandwidth-bound).
- **MoE > dense** for the CPU speed/quality tradeoff.
- Reasoning models are token-heavy → they *feel* slower on CPU; save them for quality-critical, non-interactive tasks.
