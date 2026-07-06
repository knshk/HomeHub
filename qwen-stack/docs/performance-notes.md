# Performance Notes (CPU-only, 16 GB box)

Practical expectations for running **Qwen2.5-7B-Instruct (Q4)** through Ollama
on a CPU-only machine, plus the context-length settings that most often bite
people. Reference hardware: **AMD Ryzen 5 3600** (6 cores / 12 threads), **16 GB
RAM**, no GPU.

---

## 1. Expected throughput

On an **AMD Ryzen 5 3600, CPU-only**, generating with **Qwen2.5-7B at Q4_K_M**,
expect roughly:

| Metric                         | Ballpark on Ryzen 5 3600 (CPU, 7B Q4)         |
| ------------------------------ | --------------------------------------------- |
| **Generation (output) speed**  | **~6-9 tokens/sec**                           |
| Prompt (input) processing      | Faster than generation, but still seconds for long prompts; scales with prompt length |
| Time-to-first-token            | Dominated by prompt length + any model (re)load |
| Practical "feel"               | Usable for chat and short tasks; noticeably slower than a GPU or a cloud frontier model |

What moves the number:

- **More RAM bandwidth / faster DDR4 helps** — 7B CPU inference is largely
  memory-bandwidth bound, so dual-channel, higher-MHz RAM matters more than raw
  core count past a point.
- **Thread count.** Let Ollama use the physical cores (≈6); oversubscribing all
  12 SMT threads often does not help and can hurt.
- **Quantization.** Q4_K_M is the sweet spot for 16 GB. Higher precision (Q5/Q6/
  Q8) is slower and uses more RAM; lower (Q3/Q2) is faster but degrades quality.
- **Concurrency.** Each parallel request multiplies the KV-cache (and effective
  RAM) by its context. On a 16 GB box keep `OLLAMA_NUM_PARALLEL=2` and
  `OLLAMA_MAX_LOADED_MODELS=2` — do not let parallelism exhaust memory.

These are CPU-only figures. They set the user's expectation that local Qwen is a
**capable but slower** alternative to a paid cloud model (see
[`BYOK-integration.md`](BYOK-integration.md) for fallback patterns).

---

## 2. `num_ctx` guidance and the default-context truncation trap

`num_ctx` is the model's **context window** (how many tokens of prompt + history
the model actually sees). This is the single most common source of "the model
ignored half my prompt" confusion.

### The trap

**Ollama applies a default context window (commonly 2048 tokens) unless you
explicitly raise `num_ctx`.** If your prompt + conversation exceeds that
default, **Ollama silently truncates the oldest tokens** — the model never sees
them. Qwen2.5 supports a large context, but **that capacity is wasted if you
leave `num_ctx` at the small default.** Symptoms: the model "forgets" the
system prompt or earlier turns, or ignores the top of a long document, with no
error.

### How to set it

Set `num_ctx` to comfortably cover your largest expected prompt + the reply you
want, and no larger (bigger context costs RAM and slows prompt processing — see
section 3).

- **Ollama API / Modelfile** — pass it in `options`:
  ```bash
  curl http://127.0.0.1:11434/api/chat -d '{
    "model": "qwen2.5:7b-instruct-q4_K_M",
    "messages": [{"role":"user","content":"..."}],
    "options": { "num_ctx": 8192 }
  }'
  ```
  Or bake it into a Modelfile: `PARAMETER num_ctx 8192`.
- **Through this gateway / OpenAI-style clients:** the OpenAI `/v1` schema has
  no `num_ctx` field. Set the effective context on the **Ollama side** — either
  via a Modelfile that pins `num_ctx`, or via `OLLAMA_CONTEXT_LENGTH` in
  Ollama's environment — so every request the gateway forwards inherits the
  larger window. Do not assume a long OpenAI `max_tokens` raises the context; it
  does not.

### Rule of thumb

- Short chat / tools: 2k-4k is fine.
- Summarizing or Q&A over a document: size `num_ctx` to fit the document, e.g.
  8k. Verify the document actually fits — count tokens; don't trust that it "looked
  short".
- Never rely on the default for anything longer than a couple of paragraphs.

---

## 3. RAM and KV-cache cost of larger context on a 16 GB box

Raising `num_ctx` is not free. Total memory is roughly:

```
RAM ≈ model weights  +  KV cache  +  runtime/OS overhead
```

- **Model weights (fixed):** Qwen2.5-7B at **Q4_K_M is ~4.5-5 GB** resident.
- **KV cache (grows with context):** the KV cache scales **linearly with
  `num_ctx`** (and with the number of concurrent sequences). Doubling the
  context roughly doubles the KV-cache RAM; a large window (e.g. 16k-32k) can add
  **multiple GB** on top of the weights.
- **Per parallel request:** with `OLLAMA_NUM_PARALLEL=2`, two in-flight requests
  each carry their own KV cache, so effective context memory is multiplied by the
  parallelism.

### Why this matters on 16 GB

Leave headroom for the OS and other processes. A rough budget:

```
16 GB total
  - ~2-3 GB   OS + other apps
  - ~5 GB     7B Q4 weights
  ----------
  ~8 GB       left for KV cache + overhead + parallelism
```

So **large contexts and high parallelism compete for the same ~8 GB.** If you
push `num_ctx` very high *and* run parallel requests, you risk swapping (which
tanks tokens/sec) or OOM (model reloads/kills). Practical guidance:

- Keep `num_ctx` only as large as your workload needs.
- On 16 GB, keep `OLLAMA_NUM_PARALLEL=2`, `OLLAMA_MAX_LOADED_MODELS=2`,
  `OLLAMA_KEEP_ALIVE=5m` (balances reload latency vs. memory pressure).
- Watch RAM with `free -h` / `htop` during real traffic; if you see swap
  activity, lower `num_ctx` or parallelism.
- **Do not run 30B+ models on a 16 GB CPU box** — they won't fit comfortably.

---

## 4. When to use 7B vs a commercially-licensed smaller model

Model size is a quality/speed/RAM trade-off — but **license also constrains the
choice** (see [`positioning-and-compliance.md`](positioning-and-compliance.md)).

| Choose...                          | When                                                                                                  | License note                                              |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| **Qwen2.5-7B-Instruct (Q4)**       | You want the best quality this 16 GB box runs comfortably; reasoning, coding help, longer instructions; ~6-9 tok/s is acceptable. | **Apache-2.0 — commercial-safe.** Default for this stack. |
| **Qwen2.5-1.5B (Apache-2.0)**      | You need higher throughput / lower latency, lighter RAM, simpler tasks (classification, extraction, short replies), or to run more parallel requests. | **Apache-2.0 — commercial-safe.** Good small commercial pick. |
| **Qwen2.5-0.5B (Apache-2.0)**      | Very lightweight/edge tasks where speed beats nuance.                                                  | **Apache-2.0 — commercial-safe.**                         |
| Qwen2.5-14B (Apache-2.0)           | You have more RAM/compute and want better quality than 7B.                                             | Apache-2.0 — commercial-safe, but heavier than 16 GB likes. |

**Do NOT pick Qwen2.5-3B as your "smaller" model for a commercial product.**
Despite being a convenient size, the **3B / 3B-Instruct models are under the Qwen
Research License — non-commercial only.** For a *commercially licensed smaller
model*, use **Qwen2.5-1.5B (Apache-2.0)**, not 3B. (Similarly, prefer
Qwen2.5-1.5B over Llama-3.2-3B, which carries a 700M-MAU commercial cap and a
competitor-restriction clause.)

### Decision shortcut

1. **Commercial product?** Stay on **Apache-2.0** models only: 0.5B, 1.5B, 7B,
   14B, 32B. Never 3B.
2. **Quality-bound and 7B fits?** Use **7B** (this stack's default).
3. **Throughput-bound / RAM-tight / simple tasks?** Drop to **1.5B**
   (Apache-2.0) for several-x faster generation and room for more parallel
   requests.
4. **Need long context?** Raise `num_ctx` deliberately and re-check RAM
   (section 3) — a smaller model leaves more headroom for a bigger window.

---

## Summary

- ~**6-9 tok/s** generating 7B Q4 on a Ryzen 5 3600, CPU-only.
- **Set `num_ctx` explicitly** (on the Ollama side) — the small default
  silently truncates long prompts.
- **KV cache grows linearly with context and parallelism**; on 16 GB, budget ~8
  GB after weights+OS, keep `NUM_PARALLEL=2`, and don't run 30B+ models.
- **7B (Apache-2.0)** for quality; **1.5B (Apache-2.0)** for speed. **Never 3B
  commercially** — it is research/non-commercial licensed.
