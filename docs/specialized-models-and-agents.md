# Specialized Models vs. a Strong General Model on This Appliance

**Audience:** the operator of this privacy-first, fully-local, commercial (bring-your-own-key) appliance.
**Box:** AMD Ryzen 5 3600 (6c/12t), 16 GB RAM, **CPU-only** (no usable GPU).
**Already running** (all Apache-2.0/MIT) via Ollama + a small FastAPI hub: Qwen2.5-7B-Instruct (general), moondream (vision), nomic-embed-text (embeddings), faster-whisper (STT), Kokoro (TTS).

## The honest principle

For almost every domain on this box, **a strong general model (Qwen2.5-7B-Instruct) + good prompting + tools/RAG beats chasing tiny specialized models.** There are a few real exceptions, but they are narrower than the hype suggests.

Three hard constraints make this especially true here:

1. **One model loaded at a time.** The appliance runs `OLLAMA_MAX_LOADED_MODELS=1`. A second 7B model is not a co-resident — it is a **20–30 s swap** (unload primary, load specialist, run, often swap back). On an interactive tutor/assistant this swap latency usually destroys more value than the specialist adds.
2. **16 GB RAM, CPU-only.** Two 7B models co-resident (~14 GB before the OS, voice services, and embeddings) blows the safety margin. So "run both" is not realistically on the table; it's always a swap.
3. **CPU memory-bandwidth limit.** 7B models run ~6–9 tok/s; 3B models ~6–8 tok/s. Smaller specialists are **not meaningfully faster** here (they're bandwidth-bound too) while being less capable on anything outside their niche.

So the bar a specialized model must clear is high: it must deliver a **large, domain-specific accuracy gain that prompting + RAG + tools cannot recover**, and be worth a model swap. Most don't clear it.

## Summary table

| Domain | Recommended LOCAL approach | Honest verdict |
|---|---|---|
| **Coding & learning to code** | Qwen2.5-7B-Instruct + code execution + error feedback + doc RAG | **General model wins.** Coder-7B adds <1% on HumanEval but loses ~15–20% on math/reasoning and still costs a swap. Don't add it. |
| **Teaching / tutoring (kids, subjects)** | Qwen2.5-7B-Instruct + Socratic system prompt + RAG over curated textbooks + voice I/O | **General model + prompting/RAG wins.** No viable sub-3B "education" model. Qwen2.5-Math gains 8–15% on math *but* costs a 20–30 s swap per query — not worth it for interactive tutoring. |
| **Exercise & health / nutrition** | Qwen2.5-7B-Instruct + health system prompt + **non-LLM tools** (logging, reminders, evidence links) + disclaimers | **General model wins; add no medical model.** Open "medical" 7–8B models give false authority, raise liability, and still hallucinate. See disclaimer below. |
| **3D modelling, architecture & design** | Qwen2.5-7B-Instruct as a **scripting/parametric assistant** (OpenSCAD, CadQuery, Blender Python); moondream for captioning only | **No image/3D *generation* on this box.** Generation models are GPU-only here and many are license-restricted. General model writing CAD/parametric scripts is the only realistic local play. |

---

## 1. Coding & learning to code / design

**Lightweight commercial-safe options that exist**

- **Qwen2.5-Coder** (7B, 1.5B): Apache-2.0 (commercial-safe). **Note: Qwen2.5-Coder-3B is Qwen-Research — NOT commercial.** Do not ship the 3B.
- **Granite-Code** (IBM, all sizes): Apache-2.0 (commercial-safe; worth an external license re-check before shipping, low risk).
- **Codestral Mamba**: Apache-2.0 (commercial-safe). Original **Codestral 22B: Mistral Non-Production License — NOT a fit** (requires a commercial deal, and 22B is too big for this box anyway).
- **CodeGemma**: Gemma license (permissive, responsible-use clause). Usable but verify the clause against your commercial terms.
- **StarCoder2**: BigCode OpenRAIL with a non-commercial-leaning clause — **treat as not-a-fit** unless legal signs off.

**Do they have value on this box?**

No, not as a second model. The data is blunt: Qwen2.5-Coder-7B scores **84.1% HumanEval vs. Qwen2.5-7B-Instruct's 84.8%** — the general model is actually *ahead* — and the general model wins **5 of 7** shared benchmarks, including **math (75.5% vs 46.6%)**. The Coder model trades broad reasoning for no real coding edge here, and you'd pay a 20–30 s swap for it. The commercial-safe small Coder is the **1.5B**, which is weaker than your 7B general model on real tasks.

For **learning to code / teaching to code**, specialization is actively the wrong instinct. Effective teaching needs (1) an iterative loop — run code → show the real error → explain *why* it broke, (2) conversational, explanatory tone (learners prefer this over completion-only models), and (3) RAG over the language/library docs. Those are **scaffolding**, not model weights. Qwen-7B-Instruct + a code-execution tool + explicit error feedback beats any small Coder model that lacks them.

**Recommended pick:** **General model wins** — keep Qwen2.5-7B-Instruct. Add **no** Coder model.

**Practical pattern**

- **Tool-use:** give the model a sandboxed `run_code` tool. Feed stdout/stderr back verbatim so it (and the learner) sees the actual error.
- **RAG:** embed the relevant language/library docs with `nomic-embed-text`; retrieve on the learner's question.
- **Prompting:** "You are a patient coding tutor. Run the student's code, show the error, explain the cause, then ask a guiding question before giving the fix."
- **Only-if exception:** if coding ever becomes ~80%+ of the workload, the honest move is to swap the *primary* to Qwen2.5-Coder-7B and escalate occasional reasoning/math tasks to the user's BYO remote key — not to run two models locally.

---

## 2. Teaching subjects / tutoring

**Lightweight commercial-safe options that exist**

- **Qwen2.5-Math-7B**: Apache-2.0 (commercial-safe). Real, and **+8–15% on GSM8K/MATH** over the general 7B.
- **Sub-3B "education-tuned" community models:** none with a credible, peer-reviewed advantage over Qwen2.5-7B + good prompting. Skip.
- **Proprietary education tutors** (Google/OpenAI education offerings): not open, not commercially redistributable here — **not a fit**.

**Do they have value on this box?**

Qwen2.5-Math is the closest thing to a justified specialist, but it still **doesn't clear the bar for interactive tutoring**, for three honest reasons:

1. **Swap latency kills interactivity.** With `MAX_LOADED_MODELS=1`, every math turn that needs the specialist is a 20–30 s reload (and another to swap back for the next conversational turn). That breaks the tutoring rhythm.
2. **Prompting + RAG recovers most of the gain.** Chain-of-thought prompting + a calculator/code tool + worked-example RAG recovers roughly half to two-thirds of that 8–15% — with **zero** swap cost.
3. **Tutoring quality is dominated by Socratic scaffolding**, not raw math accuracy. A model that's 10% more accurate but just hands over answers tutors *worse* than a slightly-less-accurate model that asks guiding questions.

So Qwen2.5-Math is a legitimate model, but on *this* box, for *interactive* tutoring, the swap cost makes it net-negative. (If you ever run a *batch, non-interactive* math-grading job, swapping to it once is fine — that's the narrow case where it pays off.)

**Recommended pick:** **General model + prompting/RAG wins.** Keep Qwen2.5-7B-Instruct as the single core.

**Practical pattern**

- **Socratic system prompt:** "You are a patient tutor. Ask guiding questions before revealing answers. Check for misconceptions. Adapt to the student's level."
- **RAG:** embed curated textbooks/problem sets with `nomic-embed-text`; retrieve on the student query so answers are grounded and on-curriculum.
- **Tool-use:** wire a calculator / `run_code` tool for arithmetic and step-checking — this is where most "math errors" actually get fixed, no specialist needed.
- **Per-child session history** so the tutor remembers level and prior misconceptions.
- **Voice I/O** (faster-whisper + Kokoro) for younger learners — invest here over a second model; note CPU voice latency is ~10–20 s/turn, so design turns to tolerate it.

---

## 3. Exercise & health / nutrition

**Lightweight commercial-safe options that exist**

- **BioMistral-7B**, **OpenBioLLM-8B**: claimed Apache-2.0 (verify each model card before shipping; low but real license risk).
- **MedGemma** (Google): strong medical model but **license not confirmed here** — verify Gemma/other terms before any use.
- **Meditron-7B**: OpenRAIL/research — **NOT a commercial fit.**

**Do they have value on this box?**

**No — and adding one would be a mistake**, even setting licensing aside. A medical-tuned 7–8B model gives users a **false sense of authority** while still hallucinating, raises **liability and regulatory exposure** (FDA medical-device classification, EU AI Act "high-risk"), and **cannot personalize** anyway: no LLM can reliably infer someone's circadian phase, chronotype, or insulin sensitivity from chat text. The "authority" is exactly the risk. The genuinely useful health features are **not LLM features at all** — logging, reminders, and links to evidence.

**Recommended pick:** **General model wins; add no medical model.** Qwen2.5-7B-Instruct with a health-aware system prompt, wrapped in disclaimers, plus non-LLM tools.

**Practical pattern**

- **Non-LLM tools do the real work:** food/activity logging, reminders, trend charts, and curated links to reputable evidence.
- **Prompting:** "Provide general educational wellness information only. You are not a medical professional. Never diagnose, never prescribe, never give personalized medical advice. Recommend consulting a qualified clinician."
- **Carry disclaimers** on every health-flavored response (see below), and keep health out of any "authoritative answer" UI affordance.

---

## 4. 3D modelling, architecture & design

**Lightweight commercial-safe options that exist (for *generation*)**

- Image/3D **generation** models (Stable Diffusion / FLUX families, mesh generators) are **GPU-only in practice** — minutes-to-unusable per image on this CPU — and several carry **non-commercial or restricted** licenses. On this box they are **NOT a fit**; flag any such request as out of scope for local generation.
- The appliance has **no image/3D generation infrastructure**, and that's the correct call. `moondream` is for **captioning/understanding only**, not generation.

**Do they have value on this box?**

For pixel/mesh **generation**, no — there is no realistic CPU-only, commercial-safe path. Don't chase it locally; if a user needs generation, that's a BYO-remote-key task, not a local-appliance task.

What *is* realistic locally is using the **general model as a parametric/scripting design assistant**: it writes and edits code for deterministic CAD/3D tools that run fine on CPU.

**Recommended pick:** **General model wins** as a CAD/parametric **scripting** assistant. No generation model.

**Practical pattern**

- **Scripting/tool-use:** have Qwen2.5-7B-Instruct generate and iterate on **OpenSCAD**, **CadQuery (Python)**, or **Blender Python** scripts; the deterministic tool renders the geometry, CPU-only.
- **RAG:** index the CAD tool's docs/API so generated scripts use real, current calls.
- **Vision feedback (optional):** render a view and let `moondream` caption/sanity-check it — understanding, not generation.

---

## Health & nutrition disclaimer (carry this verbatim)

> **This appliance is not a medical device and does not provide medical advice.** Health and nutrition responses are **general educational information only**. They are not diagnosis, treatment, or personalized medical guidance, and must not be relied on as such. The model **cannot** assess your individual physiology (e.g., circadian phase, chronotype, insulin sensitivity) from a conversation. Evidence in areas such as **meal timing is weak and contested** — any effect is **small (~2–3%)** and is dwarfed by sleep, exercise, stress, and genetics in most people. **Always consult a qualified healthcare professional** before making health, diet, or exercise changes. Do not use this appliance for emergencies.

Show a short version of this on every health-flavored turn, and never present a health answer through an "authoritative" UI element.

---

## What I'd actually run on this box (bottom line)

- **Primary, always loaded:** **Qwen2.5-7B-Instruct** (Apache-2.0) — the single core for chat, reasoning, tutoring, coding help, and CAD scripting.
- **On-demand swap (already in the design):** **moondream** for vision/captioning — swapped, never co-resident.
- **Supporting services:** `nomic-embed-text` (RAG), faster-whisper (STT), Kokoro (TTS).
- **Add no second LLM.** Skip Qwen2.5-Coder (no real coding gain, loses reasoning), skip Qwen2.5-Math (8–15% gain doesn't survive the 20–30 s swap in interactive use), skip every medical model (false authority + liability), skip all image/3D generation models (GPU-only and/or restricted-license here).
- **Spend the effort on scaffolding, not weights:** Socratic/health/coding **system prompts**, **RAG** over curated docs/textbooks, **tools** (`run_code`, calculator, logging/reminders, CAD renderers), per-user session history, and voice I/O.
- **Narrow, honest exceptions:** if the workload becomes ~80%+ coding, swap the *primary* to Qwen2.5-Coder-7B (don't dual-load); for occasional *batch, non-interactive* math grading, a one-time swap to Qwen2.5-Math is fine. Neither changes the everyday single-model setup.

The skepticism is correct: **a strong general model + good prompting + tools/RAG wins here.** The few genuine specialists that exist (Qwen2.5-Math, Qwen2.5-Coder) are real but don't survive this box's one-model-at-a-time, CPU-only constraints for interactive use.
