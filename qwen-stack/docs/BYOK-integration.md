# BYOK Integration: Local Qwen as a Selectable Backend

This guide shows how to offer **"Local Qwen 2.5 (free, self-hosted)"** as one
backend option *alongside* a user's bring-your-own-key (BYOK) Claude and OpenAI
credentials. The goal is a single product where the user picks which provider
handles each request — their paid frontier model, or your free local Qwen — and
where you can **fall back** from a paid key to local Qwen when desired.

The Qwen Stack gateway makes this practical because it speaks the **OpenAI**
wire format (`/v1/chat/completions`, `/v1/models`) and also exposes an
**Anthropic Messages shim** (`/v1/messages`). So whichever SDK your app already
uses, it can point at local Qwen with only a `base_url` + `api_key` change.

---

## The three backends, side by side

A typical BYOK product registers each provider as a named backend with its own
connection settings. Local Qwen slots in as just another entry:

```python
BACKENDS = {
    "local-qwen": {
        "label":    "Local Qwen 2.5 (free, self-hosted)",
        "kind":     "openai",                               # OpenAI-compatible
        "base_url": "http://192.168.1.42:8080/v1",          # the gateway, <LAN-IP>:8080/v1
        "api_key":  "qwsk-...",                             # gateway key, NOT a cloud key
        "model":    "qwen2.5-7b",
        "pays":     "operator (you)",                       # no per-token cost
    },
    "byo-openai": {
        "label":    "OpenAI (your key)",
        "kind":     "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key":  "<user-supplied sk-...>",               # from the user
        "model":    "gpt-4o-mini",                          # user's choice
        "pays":     "user",
    },
    "byo-anthropic": {
        "label":    "Claude (your key)",
        "kind":     "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key":  "<user-supplied sk-ant-...>",           # from the user
        "model":    "claude-3-5-sonnet-latest",             # user's choice
        "pays":     "user",
    },
}
```

The user selects a backend in your UI; you route the call accordingly. Local
Qwen requires no cloud account and bills nothing per token — its only "cost" is
the hardware you already run.

---

## Provider matrix

| Provider                         | Auth source                                   | Who pays                          | Who bears ToS / compliance                                                          | Format (request shape)                                  |
| -------------------------------- | --------------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------- |
| **Local Qwen 2.5 (self-hosted)** | Your gateway key `qwsk-...` (issued by you)   | **Operator** (you) — hardware/electricity, no per-token fee | **Operator** (you): model-license compliance, uptime, data handling, gateway security, abuse on the box | OpenAI `/v1/chat/completions`; **+ Anthropic `/v1/messages` shim** |
| **OpenAI (BYO)**                 | User's own OpenAI key `sk-...`                | **User** (billed to their OpenAI account) | **User**: bound by OpenAI ToS/usage policies under their account                   | OpenAI `/v1/chat/completions`                           |
| **Anthropic / Claude (BYO)**     | User's own Anthropic key `sk-ant-...`         | **User** (billed to their Anthropic account) | **User**: bound by Anthropic ToS/usage policies under their account                | Anthropic `/v1/messages`                                |

Key takeaways from the matrix:

- For **BYOK cloud** backends, the *user* pays and the *user* carries the
  provider's Terms of Service and acceptable-use obligations, because the call
  runs under their account and their key.
- For **Local Qwen**, *you* (the operator) carry everything: the model's
  license (Qwen2.5-7B is Apache-2.0 — commercial use OK; the 3B variant is
  **not**), uptime, where the data goes, securing the gateway, and abuse. See
  [`positioning-and-compliance.md`](positioning-and-compliance.md) for the full
  responsibility split.

---

## Local backend is OpenAI-compatible, with an Anthropic shim

You have two ways to call local Qwen; pick whichever matches the SDK your app
already uses.

### A) OpenAI-compatible path (recommended default)

Use the OpenAI SDK (or any OpenAI-compatible client). Only `base_url`,
`api_key`, and `model` change relative to a real OpenAI call:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.1.42:8080/v1",
    api_key="qwsk-...",
)
resp = client.chat.completions.create(
    model="qwen2.5-7b",
    messages=[{"role": "user", "content": "Hello from local Qwen."}],
)
print(resp.choices[0].message.content)
```

### B) Anthropic Messages shim

If your app is built on the Anthropic SDK / Messages format, the gateway's
`/v1/messages` shim accepts the Anthropic request shape, translates it to the
OpenAI form for Ollama, and returns an Anthropic-shaped response. Point the
Anthropic SDK at the gateway:

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://192.168.1.42:8080/v1",   # the shim lives under /v1
    api_key="qwsk-...",                        # sent as x-api-key, accepted by the gateway
)
msg = client.messages.create(
    model="qwen2.5-7b",
    max_tokens=512,
    messages=[{"role": "user", "content": "Hello from local Qwen via the shim."}],
)
print(msg.content[0].text)
```

> The shim is a **compatibility layer**, not Claude and not Anthropic's API. It
> lets Anthropic-SDK code target local Qwen unchanged. Capabilities (tool use,
> vision, exact token accounting, etc.) match what Qwen2.5-7B and Ollama
> support, which is a subset of Claude's. Do not present shim responses as
> coming from Claude.

This dual compatibility is what lets local Qwen sit beside BYO OpenAI and BYO
Claude in the same product without forking your client code: an OpenAI-style
app and an Anthropic-style app can both reach it.

---

## Falling back from a paid key to local Qwen

A common pattern: try the user's paid backend first, and on failure (or when the
user opts into "save money / privacy mode"), fall back to free local Qwen. The
gateway's OpenAI compatibility makes the fallback a near drop-in.

### When to fall back

- The user has no valid paid key configured, or it was rejected (`401`).
- The paid provider is rate-limited (`429`), over quota, or returning errors.
- The user explicitly chooses "free local model" for a request.
- A privacy mode requires data to stay on-premises (local Qwen never leaves
  your network).

### Example: paid-first, local-fallback router

```python
from openai import OpenAI, OpenAIError

PAID = OpenAI(base_url="https://api.openai.com/v1", api_key=USER_OPENAI_KEY)
LOCAL = OpenAI(base_url="http://192.168.1.42:8080/v1", api_key=GATEWAY_KEY)

def chat(messages, *, prefer_paid=True):
    """Try the user's paid backend, then fall back to free local Qwen."""
    attempts = []
    if prefer_paid and USER_OPENAI_KEY:
        attempts.append(("openai", PAID, "gpt-4o-mini"))
    attempts.append(("local-qwen", LOCAL, "qwen2.5-7b"))

    last_err = None
    for name, client, model in attempts:
        try:
            return client.chat.completions.create(model=model, messages=messages)
        except OpenAIError as e:
            last_err = e
            # 401 (bad/absent key), 429 (rate/quota), 5xx (provider down) -> next backend
            continue
    raise last_err
```

Because the gateway returns OpenAI-style error bodies and status codes (`401`,
`429`, `502/504`), your existing error handling around the paid client works
unchanged against the local client.

### Notes for a clean fallback

- **Keep keys separate.** The `qwsk-...` gateway key is unrelated to the user's
  `sk-...` / `sk-ant-...` cloud keys. Never send a cloud key to the gateway or a
  gateway key to a cloud provider.
- **Map the model per backend.** Don't send `gpt-4o-mini` to the gateway; send
  `qwen2.5-7b`. Keep a per-backend model in your config (see `BACKENDS` above).
- **Set expectations on quality and speed.** Local Qwen2.5-7B (Q4, CPU) is
  capable but slower and smaller than a frontier model — see
  [`performance-notes.md`](performance-notes.md) for throughput and when 7B is
  the right tool versus a smaller commercially-licensed model.
- **Streaming works on both sides.** The gateway forwards Ollama SSE verbatim
  for `"stream": true`, so streaming fallback behaves like the paid path.
- **Tell the user which backend ran.** Surface the active backend label so the
  user knows when a request fell back to local Qwen (different model, different
  privacy/cost properties).

---

## Summary

- Register **"Local Qwen 2.5 (free, self-hosted)"** as one more backend pointed
  at `http://<LAN-IP>:8080/v1` with a `qwsk-...` key and model `qwen2.5-7b`.
- It is **OpenAI-compatible** and also speaks the **Anthropic Messages shape**
  via the `/v1/messages` shim — so it drops in next to BYO OpenAI and BYO Claude.
- BYOK cloud backends: **user pays, user bears the provider ToS.** Local Qwen:
  **you pay (hardware), you bear license/uptime/data/security/abuse** — detailed
  in [`positioning-and-compliance.md`](positioning-and-compliance.md).
- Fall back from a paid key to local Qwen on `401/429/5xx` or by user choice;
  the OpenAI-style errors make this a near drop-in.
