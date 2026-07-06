# Positioning and Compliance

This document covers how to describe the project honestly ("built & tested with
Claude" without implying endorsement), exactly which responsibilities move to
the user under BYOK versus which stay with you as the operator of the
self-hosted Qwen backend, the licensing status of the Qwen2.5 models you might
serve, and a short, adaptable, non-legal-advice disclaimer.

---

## 1. Honest positioning: "built & tested with Claude"

It is accurate and fine to say this project was **built and tested with the help
of Claude** (Anthropic's AI assistant). It is **not** accurate — and you must
not imply — that Anthropic endorses, sponsors, certifies, partners with, or is
affiliated with this project, or that the project ships Claude or Anthropic
technology.

**Accurate phrasings (use these):**

- "Built and tested with the help of Claude (Anthropic's AI assistant)."
- "Developed with assistance from Claude. Not affiliated with or endorsed by
  Anthropic."
- "This product serves a local Qwen2.5 model. The Anthropic-compatible endpoint
  is a compatibility *shim*; it does not use Claude or Anthropic's API."

**Misleading phrasings (do not use these):**

- "Powered by Claude" / "Claude inside" (the backend is Qwen, not Claude).
- "Anthropic-approved", "Anthropic partner", "Certified by Anthropic", or any
  use of Anthropic/Claude logos that suggests sponsorship.
- "Claude-compatible API" presented as if Claude is answering — the `/v1/messages`
  shim only mimics the *request/response shape* for local Qwen.

**Trademark hygiene:** "Claude" and "Anthropic" are Anthropic's marks; "Qwen"
is Alibaba Cloud's. Use them only nominatively (to truthfully refer to the
tools/models), never as your product name or in a way that suggests an
official relationship. When you describe the Anthropic compatibility layer,
call it a "shim" or "compatibility endpoint", and be explicit that the model
answering is Qwen2.5-7B running locally.

---

## 2. BYOK vs self-hosted Qwen: the responsibility split

Two very different trust models coexist in this product. Get the split right and
communicate it to your users.

### What SHIFTS to the user under BYOK (their Claude / OpenAI key)

When a request runs on the user's own cloud key (`sk-...` / `sk-ant-...`):

| Responsibility            | Who                        | Why                                                                 |
| ------------------------- | -------------------------- | ------------------------------------------------------------------- |
| **Billing / cost**        | **User**                   | The call is metered against the user's account.                     |
| **Provider ToS / AUP**    | **User**                   | They accepted OpenAI/Anthropic terms; their account, their conduct. |
| **Model licensing**       | **Provider (via user's account)** | The cloud provider licenses its own model to the user.       |
| **Model uptime / SLA**    | **Cloud provider**         | You don't run their model; you can't promise their availability.    |
| **Their data to the cloud** | **User + cloud provider** | The user's prompts leave to a third party they chose.               |
| **Key secrecy**           | **Shared**                 | User must supply a valid key; you must store/transmit it safely.    |

You still must **handle the user's BYO key securely** (don't log it, encrypt at
rest, transmit over TLS, scope it minimally) — that obligation never disappears.
But the *commercial and policy* relationship for that call is between the user
and their chosen cloud provider.

### What REMAINS with the vendor (you) for self-hosted Qwen

When a request runs on **Local Qwen** (your gateway + your Ollama + your
hardware), you own essentially everything:

| Responsibility               | Who (you)  | What it means concretely                                                                                       |
| ---------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------- |
| **Uptime / availability**    | **You**    | Your box, your Ollama, your gateway. If it's down, the local backend is down. No third party to blame.         |
| **Data handling**            | **You**    | Prompts and outputs flow through *your* gateway and are logged in *your* SQLite `usage_log`. You are the data controller. State your retention/privacy posture; secure the DB. |
| **MODEL LICENSE COMPLIANCE** | **You**    | You are distributing/serving the model. You must comply with its license (Apache-2.0 for 7B; **not** commercial-safe for 3B — see below). This does not transfer to the user. |
| **Abuse**                    | **You**    | Misuse runs on your hardware under your control. You set and enforce acceptable use, rate limits, and key revocation (`adminctl revoke-key`). |
| **Gateway security**         | **You**    | Auth, fail-closed key checks, TLS termination, firewalling Ollama to loopback, scoping the gateway to your LAN subnet, patching Ollama CVEs. See section 4. |

The blunt version: **for BYOK the user carries the cloud provider's terms and
bill; for self-hosted Qwen, you carry the model license, the uptime, the data,
the abuse surface, and the security of the front door.** None of the
self-hosted obligations can be pushed onto the user just because they pressed
"use the free local model".

---

## 3. Qwen2.5 model licensing status (from the research)

> Run models via Ollama? **Ollama is MIT-licensed and does not change the
> underlying model's license.** Each model's own license applies independently.
> Verify the model license yourself; Ollama's MIT terms do not supersede it.

| Model                         | License                          | Commercial use                                                                                   |
| ----------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------ |
| **Qwen2.5-7B-Instruct** (this stack's default) | **Apache License 2.0**           | **Permitted, unrestricted** — fine for a paid SaaS. (Meet Apache-2.0 attribution; see below.)    |
| Qwen2.5-0.5B-Instruct         | Apache-2.0                       | Permitted, unrestricted.                                                                          |
| Qwen2.5-1.5B                  | Apache-2.0                       | Permitted, unrestricted. (Good small commercial pick.)                                            |
| **Qwen2.5-3B / 3B-Instruct**  | **Qwen Research License**        | **NON-COMMERCIAL ONLY.** Commercial use requires a separate license from Alibaba Cloud. **Do not deploy 3B in a commercial product.** |
| Qwen2.5-14B-Instruct          | Apache-2.0                       | Permitted, unrestricted.                                                                          |
| Qwen2.5-32B-Instruct          | Apache-2.0                       | Permitted, unrestricted.                                                                          |
| Qwen2.5-72B-Instruct          | Qwen License Agreement           | Permitted up to **100M monthly active users**; above 100M MAU requires a separate Alibaba license. |
| Llama-3.2-3B (alternative)    | Llama 3.2 Community License (NOT Apache-2.0) | Commercial use capped at **700M MAU**; above requires a Meta license; includes a competitor-restriction clause. For small commercial models, prefer Qwen2.5-1.5B (Apache-2.0, no caps). |

**Bottom line for this stack:** the default **Qwen2.5-7B-Instruct is
Apache-2.0 and commercial-use safe**, including in a paid SaaS. **Qwen2.5-3B is
research/non-commercial only — it is NOT commercial-safe.** Do not advertise or
deploy 3B commercially without a separate license from Alibaba Cloud. Avoid
relying on 72B above 100M MAU without Alibaba's separate license.

### Apache-2.0 attribution obligations (for the 7B/14B/32B you serve)

When using Apache-2.0 Qwen models commercially, you must:

1. **Include a copy of the Apache-2.0 license** in your distribution.
2. **Include the NOTICE file** with attribution, if one was present in the
   original.
3. **Document any modifications** you made to the model.
4. **Retain all copyright notices** from Alibaba Cloud.

For derivative works / fine-tuning: Apache-2.0 derivatives must include the
Apache-2.0 license; Qwen Research License derivatives must display
**"Built with Qwen"** or **"Improved using Qwen"** attribution.

---

## 4. Gateway security is your responsibility (self-hosted)

These obligations sit squarely with you (the operator) and are summarized here
because they are part of "what remains with the vendor":

- **Ollama has no built-in auth.** All its endpoints — including model
  delete/pull — are unauthenticated by default. This gateway is the mandatory
  auth layer. Never expose Ollama directly.
- **Patch Ollama.** CVE-2026-7482 ("Bleeding Llama", CVSS 9.3) allowed
  unauthenticated remote memory extraction (prompts, API keys, tokens) and
  affected ~300k deployments; patched in **v0.17.1+**. Also keep current for
  CVE-2025-63389 (auth bypass) and CVE-2025-51471 (token theft via malicious
  `WWW-Authenticate`). Check `ollama --version`.
- **Bind Ollama to loopback:** `OLLAMA_HOST=127.0.0.1:11434`. The gateway
  reaches it over `127.0.0.1`; nothing else should.
- **Firewall the gateway to your LAN subnet** (UFW; specific ALLOW overrides
  default DENY):
  ```bash
  sudo ufw deny in 8080
  sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp   # your subnet
  sudo ufw status numbered
  ```
  Find your subnet with `ip addr show`.
- **Never expose to the public internet**, even behind a firewall. Use a VPN
  (Tailscale/WireGuard) or SSH tunnel for remote access.
- **Rate-limit and revoke.** Per-key RPM limits and `adminctl revoke-key`
  contain abuse; monitor `usage_log` and proxy logs for anomalies.
- **Protect the admin surface.** Keep `ADMIN_TOKEN` strong; the admin UI and
  `adminctl` can mint and revoke keys.

---

## 5. Disclaimer (adapt this — not legal advice)

> **Disclaimer.** This software was built and tested with the assistance of
> Claude, an AI assistant from Anthropic. It is an independent project and is
> **not affiliated with, sponsored by, certified by, or endorsed by Anthropic**.
> The Anthropic-compatible endpoint is a compatibility *shim* for a locally
> hosted Qwen2.5 model and does **not** use Claude or Anthropic's services.
>
> Models are served via Ollama (MIT-licensed), which does not alter the
> underlying model licenses. **Qwen2.5-7B-Instruct is licensed under Apache-2.0
> and permits commercial use** subject to Apache-2.0 attribution requirements.
> **Some Qwen2.5 variants (notably the 3B models) are released under a research
> license that prohibits commercial use** without a separate license from
> Alibaba Cloud; do not deploy those commercially. You are responsible for
> confirming and complying with the license of every model you run, for the
> security and uptime of this self-hosted deployment, for the handling and
> retention of data passing through it, and for preventing abuse.
>
> When you use your own third-party API keys ("bring your own key") for Claude,
> OpenAI, or any other provider, those requests are governed by that provider's
> terms of service and billed to your account; that relationship is between you
> and that provider.
>
> This document is provided for general informational purposes only and is
> **not legal advice**. License terms and applicable law change; consult a
> qualified attorney for your specific situation before relying on any model or
> service commercially.

---

## Quick checklist

- [ ] Marketing says "built/tested **with** Claude", never "powered by Claude"
      or "Anthropic-endorsed".
- [ ] Default model is **Qwen2.5-7B-Instruct (Apache-2.0)** for commercial use.
- [ ] **3B is never used in a commercial deployment** (research/non-commercial
      license).
- [ ] Apache-2.0 license copy, NOTICE (if any), modification notes, and
      copyright notices are shipped.
- [ ] Ollama is patched, loopback-bound, and the gateway is firewalled to the
      LAN subnet; not exposed to the internet.
- [ ] Users understand: BYOK = their bill + provider ToS; Local Qwen = your
      license/uptime/data/abuse/security.
- [ ] A disclaimer (adapted from section 5) is shown to users.
