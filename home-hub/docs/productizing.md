# Productizing — Shipping the Home LLM Hub as an Appliance

This document is for anyone who wants to turn the Home LLM Hub from "a thing I
run at home" into a small **home AI appliance** they hand to other households:
a quiet box that, once plugged into the family Wi-Fi, serves `http://llm.home`
with private chat, notes, checklists, and file/photo search — fully offline.

It covers the one-shot installer, the **commercial license status of every
bundled model** (read this carefully — one popular vision model is *not*
commercially usable), and where the product roadmap goes next.

---

## 1. The shape of the appliance

The appliance is the same software described in the [README](../README.md),
pre-installed on a small always-on machine (a 16 GB CPU box is the reference
target — see sizing below):

```
  [ family Wi-Fi ] --- http://llm.home --- [ nginx :80 ]
                                                |
                                       [ Home LLM Hub :8090 (rootless) ]
                                                |
                                  [ gateway :8080 ] --- [ Ollama :11434 ]
                                                            |
                               Qwen2.5-7B  +  moondream  +  nomic-embed-text
```

Two layers do the install:

1. **The Hub itself** runs **rootless** on `0.0.0.0:8090`. No privileges, no
   systemd required to function. This is what the family actually uses.
2. **`deploy/install-llmhome.sh`** (one-time, `sudo`) promotes it to a friendly
   `http://llm.home` on port 80, for the whole LAN.

### `install-llmhome.sh`

Run once per appliance:

```bash
sudo bash deploy/install-llmhome.sh <LAN_IP> 8090
#   e.g.  sudo bash deploy/install-llmhome.sh 192.168.1.42 8090
```

What it does (idempotent — safe to re-run; fully reversible):

- **DNS for the LAN.** Installs/configures **dnsmasq** with a single
  `address=/llm.home/<LAN_IP>` line so every device resolves `llm.home` to the
  box. It frees port 53 surgically by disabling only the `systemd-resolved`
  **stub listener** (`DNSStubListener=no` drop-in), which preserves your existing
  upstream DNS resolution.
- **Port 80 via reverse proxy.** Installs **nginx** to listen on 80 and proxy to
  the rootless Hub at `127.0.0.1:8090`. nginx already carries
  `CAP_NET_BIND_SERVICE`, so the Python app needs **zero** elevated privileges.
  This beats `setcap` on the interpreter (breaks on every Python upgrade) and
  `authbind` (extra indirection): the proxy approach is production-standard and
  survives system updates.
- **Zero-config fallback.** Installs **avahi** (mDNS) so `http://llm.local`
  works automatically even if LAN DNS is unavailable — no router changes needed
  for that path.

Manual step the installer **cannot** do for the customer: point the **router's
DHCP DNS** at `<LAN_IP>` so all Wi-Fi clients use the box for `llm.home`. Until
that's set, only the box resolves the name (the `llm.local` mDNS fallback still
works). Document this prominently. Also tell Android users to set **Private DNS**
to off/automatic, or use `llm.local` / `<LAN_IP>:8090`, since Android's
DNS-over-HTTPS bypasses LAN DNS.

The installer is reversible: a documented revert returns the system to its
original state (re-enable the resolved stub, remove the dnsmasq/nginx/avahi
config). Ship that revert in the box's docs.

Recommended onboarding for the appliance: bind all internal services to
`127.0.0.1` (Hub on `8090`, gateway on `8080`, Ollama on `11434`), let nginx be
the only thing on `:80`, and verify in this order: `nslookup llm.home 127.0.0.1`
on the box, `curl http://127.0.0.1/` on the box, then the same from a Wi-Fi
device.

---

## 2. Bundled model licenses — the part you must get right

The appliance ships model **weights**. If you sell it or use it commercially,
the license of each bundled model applies to you. **Do not claim a non-commercial
model is commercial-safe.** Here is the accurate status of every model this
product bundles, plus the common alternatives and the one to avoid.

### Models this appliance ships (all commercially usable)

| Role        | Model (Ollama tag)              | License        | Commercial use | Notes                                                                 |
| ----------- | ------------------------------- | -------------- | -------------- | --------------------------------------------------------------------- |
| **Chat**    | `qwen2.5-7b` (Qwen2.5-7B-Instruct, served via the gateway) | **Apache-2.0** | **Yes**        | Commercially usable, no royalties. The smaller **Qwen2.5 3B variant is NOT Apache-2.0** — don't swap it in for a commercial product. |
| **Vision**  | `moondream` (moondream2)        | **Apache-2.0** | **Yes**        | Recommended default: `moondream:1.8b-v2-q4_K_M` — ~1.8 GB, 1.42B params, Q4_K_M, 2K context, fits 16 GB CPU easily. Good for simple scenes/short text; weaker on dense charts/OCR. |
| **Embeddings** | `nomic-embed-text` (v1)      | **Apache-2.0** | **Yes**        | 768-dim, 137M params, Matryoshka-capable (can reduce to 64/128/256/512 dims), ~2–4 GB RAM in use; outperforms OpenAI `text-embedding-ada-002`. |

All three are **Apache-2.0**: commercial use is allowed without royalties, and
you do **not** have to open-source your own product code.

### Approved alternatives (also commercially safe)

| Swap        | Model (Ollama tag)              | License        | When to use                                                                 |
| ----------- | ------------------------------- | -------------- | --------------------------------------------------------------------------- |
| Vision (up) | `qwen2.5vl:7b` (Qwen2.5-VL-7B)  | **Apache-2.0** | Significantly better at structured content — charts, tables, dense document OCR. ~6 GB; still fits a 16 GB CPU box comfortably. A 3B variant (~3.2 GB) exists for tighter RAM. |
| Embeddings (up) | `mxbai-embed-large:335m`    | **Apache-2.0** | Higher-dimensional retrieval — 1000-dim, 335M params, ~670 MB.             |
| Embeddings (down) | `all-minilm` (l6-v2)      | **Apache-2.0** | Ultra-minimal footprint — 384-dim, ~22–33M params, ~46–67 MB. Lower quality than nomic; use only under hard space limits. |

### DO NOT bundle for a commercial product: LLaVA

> **LLaVA is non-commercial. Do not ship it in a product you sell or use
> commercially.** Even though some LLaVA model *weights* carry an Apache-2.0
> notice, LLaVA is trained on data under **CC BY-NC 4.0** and with GPT-4-derived
> dataset restrictions. The *training data itself* — not just the model — is
> non-commercial, which blocks commercial product use. Use `moondream` or
> `qwen2.5vl` instead.

This is the trap the appliance is designed to avoid: an open-weights download
does not, by itself, make a model commercially usable. Always check the **data
and training** licenses, not only the weights file.

### Attribution / NOTICES you owe (Apache-2.0)

Apache-2.0 requires you to carry the copyright notice and the license text in
your distribution. There is no NOTICE-file enforcement for these particular
models and no royalties, but include a clear notice in the appliance, for
example a `LICENSES`/`NOTICES` file stating:

> This product includes **Qwen2.5-7B-Instruct**, **moondream2**, and
> **nomic-embed-text v1**, each licensed under the **Apache License, Version
> 2.0**. Copyright held by the respective authors. The full Apache-2.0 license
> text is included; see the LICENSE file for full terms.

Apache-2.0 grants no trademark rights — you may describe origin (e.g. "based on
Alibaba's Qwen2.5"), but don't imply endorsement or use the model owners' marks
as your own.

### Versions and sizing to verify per build

- **Ollama:** require **0.7.0+** for the vision models and **0.1.26+** for
  embeddings. Keep Ollama patched (it has known CVEs and no auth of its own —
  bind it to `127.0.0.1` only).
- **16 GB CPU reference box:** `moondream` (~1.8 GB) + `nomic-embed-text`
  (~150 MB loaded) leaves 14 GB+ for Qwen2.5-7B inference, the OS, and the app.
- **Latency to expect** on CPU: roughly **5–15 tokens/sec** for vision
  captioning. Test on the actual target hardware before shipping; if dense
  chart/table OCR is a selling point, prefer `qwen2.5vl:7b`.

---

## 3. Operator responsibilities (what shipping it means)

When you ship the appliance, **you** (the operator/vendor) carry what a BYOK
cloud product would push onto the user:

- **Model-license compliance** for every bundled model (Section 2). This is on
  you, not the customer.
- **Security of the box on the customer's LAN** — the passwordless,
  device-bound, plain-HTTP-on-LAN model (see [`privacy.md`](privacy.md)). Be
  upfront with customers that it is for a trusted home network and not for the
  public internet; document the VPN path for remote access and the optional TLS
  path (`COOKIE_SECURE=1`).
- **Data handling** — everything stays on the appliance; market that honestly
  ("your data never leaves your home") and don't add telemetry that breaks the
  promise.
- **Updates and patching** — especially Ollama, given its CVE history.

---

## 4. Future phase: a native admin/user app

Today the entire experience is a **server-rendered web app** — no React, no
Node, no build step, no app-store install. That's a feature for a self-hosted
family box: it works in any browser on Android, iOS, Windows, macOS, and Linux,
with nothing to install and nothing to update on the client.

A **native admin/user app is a planned future phase**, not part of this release.
When it lands it would layer on top of the *same* HTTP API documented in the
README and would likely add:

- A smoother **device-approval** flow for the admin — push notification when a
  new device is pending, approve from the phone, optional QR-code pairing.
- Native **camera capture** straight into photo upload + vision search.
- **Offline-friendly** caches for notes/checklists with sync.
- Possibly **hardware device-binding** (MAC/UUID) and short-TTL token rotation
  for a stronger session model than browser cookies allow.

Until then, the web app is the product, the HTTP API is the contract, and the
appliance is complete and shippable as-is — provided you ship only the
commercially-licensed models above.
