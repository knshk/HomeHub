# Qwen Stack Gateway

A minimal, self-hosted API gateway that puts authentication, per-key rate
limiting, and usage logging in front of a local [Ollama](https://ollama.com)
server running **Qwen2.5-7B-Instruct**. Your apps talk to the gateway using the
familiar OpenAI (`/v1/chat/completions`, `/v1/models`) wire format — plus an
Anthropic-style Messages shim (`/v1/messages`) — and the gateway forwards
requests to Ollama on the loopback interface.

The point: Ollama itself has **no authentication** (see
[`docs/positioning-and-compliance.md`](docs/positioning-and-compliance.md) and
the security notes below). Never expose Ollama directly. Bind Ollama to
`127.0.0.1:11434` and let this gateway be the only network-facing service, with
API keys, rate limits, and an audit log.

---

## Architecture

```
                         LAN (e.g. 192.168.1.0/24)
   +-----------+   +-----------+   +-----------+
   |   App A   |   |   App B   |   |  curl /   |
   | (OpenAI   |   | (Anthropic|   |  scripts  |
   |  SDK)     |   |  SDK)     |   |           |
   +-----+-----+   +-----+-----+   +-----+-----+
         |               |               |
         |  Authorization: Bearer qwsk-...  (or x-api-key: qwsk-...)
         |               |               |
         v               v               v
   +=====================================================+
   |          Qwen Stack Gateway   0.0.0.0:8080          |
   |-----------------------------------------------------|
   |  * API-key auth   (sha256 hash lookup, fail-closed) |
   |  * Rate limit     (per-key sliding window, RPM)     |
   |  * Usage log      (sqlite: model, tokens, status)   |
   |  * Model alias    (qwen2.5-7b -> qwen2.5:7b-...)    |
   |  * /v1/chat/completions   /v1/models                |
   |  * /v1/messages  (Anthropic -> OpenAI shim)         |
   |  * /admin UI + adminctl   (ADMIN_TOKEN protected)   |
   +=========================+===========================+
                             |  httpx, loopback only
                             |  (no auth needed: localhost)
                             v
                +============================+
                |   Ollama   127.0.0.1:11434 |
                |   OpenAI-compatible API     |
                +=============+==============+
                              |
                              v
                +============================+
                |  Qwen2.5-7B-Instruct (Q4)  |
                |  Apache-2.0 licensed        |
                +============================+
```

- **Apps** never see Ollama. They only hold a `qwsk-...` API key and point at
  the gateway.
- **Gateway** authenticates, rate-limits, logs, rewrites the model alias, and
  proxies to Ollama over `127.0.0.1`.
- **Ollama** is loopback-only; the gateway is the single front door.

---

## Quickstart

> Prerequisites: Python 3.10+, and an Ollama install with the model pulled:
> `ollama pull qwen2.5:7b-instruct-q4_K_M`. Ollama should be bound to
> `127.0.0.1:11434` (`OLLAMA_HOST=127.0.0.1:11434`).

```bash
cd /home/kanishka/kk_works/LLMs/qwen-stack

# 1. Install deps, create venv, init DB, write a default .env if missing.
./install.sh

# 2. Edit .env and set a strong ADMIN_TOKEN (and review the other vars).
#    GATEWAY_HOST, GATEWAY_PORT, OLLAMA_BASE_URL, DB_PATH, ADMIN_TOKEN, DEFAULT_RPM
$EDITOR .env

# 3. Mint your first API key (prints the plaintext key exactly ONCE).
./adminctl.py create-key --name "my-laptop"
#   -> qwsk-1a2b3c4d5e6f...   <-- copy this now, it is never shown again

# 4. Start the gateway.
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
#   (or: ./run.sh)
```

The uvicorn entrypoint is always **`app.main:app`**.

---

## How an app connects

Point any OpenAI- or Anthropic-compatible client at the gateway:

| Setting     | Value                                  |
| ----------- | -------------------------------------- |
| `base_url`  | `http://<LAN-IP>:8080/v1`              |
| `api_key`   | `qwsk-...` (your minted key)           |
| `model`     | `qwen2.5-7b`                           |

`<LAN-IP>` is the machine's address on your local network. Find it with:

```bash
ip addr show          # look for the inet on your LAN interface, e.g. 192.168.1.42
# or just the relevant line:
ip -4 addr show scope global
```

That same subnet (e.g. `192.168.1.0/24`) is what your firewall rules and
allowlists should be scoped to — see the security section below and
`docs/positioning-and-compliance.md`. **This is where the "subnet endpoint"
comes from:** the gateway listens on `0.0.0.0:8080`, but you reach it at your
host's LAN IP (`http://192.168.1.42:8080/v1`), and you restrict who can reach
that port to your LAN subnet via UFW.

### Authentication

Send your key as **either** of these headers (both are accepted):

```
Authorization: Bearer qwsk-...        # OpenAI style
x-api-key: qwsk-...                    # Anthropic style
```

Missing, malformed, revoked, or unknown keys return `401` with an
OpenAI-style error body. Auth is **fail-closed**: anything that is not a
valid, active key is rejected.

---

### Example: OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.1.42:8080/v1",   # <LAN-IP>:8080/v1
    api_key="qwsk-1a2b3c4d5e6f...",            # your gateway key
)

resp = client.chat.completions.create(
    model="qwen2.5-7b",
    messages=[
        {"role": "system", "content": "You are a concise assistant."},
        {"role": "user", "content": "Explain a B-tree in two sentences."},
    ],
)
print(resp.choices[0].message.content)

# Streaming (forwarded verbatim as SSE):
stream = client.chat.completions.create(
    model="qwen2.5-7b",
    messages=[{"role": "user", "content": "Count to five."}],
    stream=True,
)
for chunk in stream:
    delta = chunk.choices[0].delta.content or ""
    print(delta, end="", flush=True)
```

### Example: Anthropic Python SDK

The gateway exposes an Anthropic-style Messages endpoint at `/v1/messages` that
translates to/from the OpenAI shape internally, so the official `anthropic` SDK
works against your local Qwen. Point `base_url` at the gateway's `/v1` and use
your `qwsk-...` key (sent automatically as `x-api-key`).

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://192.168.1.42:8080/v1",   # <LAN-IP>:8080/v1 (the shim lives here)
    api_key="qwsk-1a2b3c4d5e6f...",            # your gateway key, sent as x-api-key
)

msg = client.messages.create(
    model="qwen2.5-7b",
    max_tokens=512,
    system="You are a concise assistant.",
    messages=[
        {"role": "user", "content": "Explain a B-tree in two sentences."},
    ],
)
# Anthropic content is a list of blocks:
print(msg.content[0].text)
```

> Note: this is a compatibility **shim**, not Anthropic's API and not Claude.
> It accepts the Messages request/response shape so existing Anthropic-SDK code
> can target local Qwen without a rewrite. See
> [`docs/BYOK-integration.md`](docs/BYOK-integration.md) for using this
> alongside a real Claude/OpenAI key.

### Example: curl

```bash
# OpenAI-style chat completion
curl http://192.168.1.42:8080/v1/chat/completions \
  -H "Authorization: Bearer qwsk-1a2b3c4d5e6f..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5-7b",
    "messages": [{"role": "user", "content": "Say hello in one word."}]
  }'

# Same call, Anthropic-style auth header
curl http://192.168.1.42:8080/v1/chat/completions \
  -H "x-api-key: qwsk-1a2b3c4d5e6f..." \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5-7b","messages":[{"role":"user","content":"hi"}]}'

# List available models
curl http://192.168.1.42:8080/v1/models \
  -H "Authorization: Bearer qwsk-1a2b3c4d5e6f..."

# Streaming (Server-Sent Events, forwarded verbatim)
curl -N http://192.168.1.42:8080/v1/chat/completions \
  -H "Authorization: Bearer qwsk-1a2b3c4d5e6f..." \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5-7b","stream":true,
       "messages":[{"role":"user","content":"Count to three."}]}'
```

---

## Model alias

Clients use friendly names; the gateway rewrites them to the exact Ollama tag.

| Client model  | Upstream Ollama model              |
| ------------- | ---------------------------------- |
| `qwen2.5-7b`  | `qwen2.5:7b-instruct-q4_K_M`       |

Unknown model names **pass through unchanged**, so you can target any model your
Ollama instance has pulled. The alias map is an extensible dict in the gateway
config.

---

## Admin: web UI and `adminctl`

Key management is protected by `ADMIN_TOKEN` (set in `.env`). There are two
interfaces; both do the same things.

### `adminctl.py` (CLI)

```bash
# Create a key (plaintext shown ONCE; only its sha256 hash is stored).
./adminctl.py create-key --name "service-x" --rpm 120 --daily-token-limit 200000

# List keys (shows id, name, display prefix qwsk-xxxxxxxx, created_at,
# revoked flag, limits — never the full key, which is not recoverable).
./adminctl.py list-keys

# Revoke a key by id (immediately fail-closed on next request).
./adminctl.py revoke-key --id 3

# Inspect recent usage from the usage_log.
./adminctl.py usage --key-id 3
```

### Admin web UI

Browse to `http://<LAN-IP>:8080/admin` and authenticate with `ADMIN_TOKEN`.
The UI lists keys, lets you create a key (the plaintext is revealed exactly
once on the create screen), revoke keys, and view per-key usage. Treat the
admin surface as sensitive: it is reachable from the LAN, so keep `ADMIN_TOKEN`
strong and rotate it if leaked.

> What is stored, what is not: only the **sha256 hash** of the full key and a
> 13-char display **prefix** (e.g. `qwsk-1a2b3c4d`) are persisted. The plaintext
> key is shown once at creation and never again. Verification uses a
> constant-time comparison on the hash.

---

## Configuration

All config comes from environment variables / `.env` (loaded via
`python-dotenv`):

| Variable          | Default                                              | Purpose                                  |
| ----------------- | ---------------------------------------------------- | ---------------------------------------- |
| `GATEWAY_HOST`    | `0.0.0.0`                                             | Gateway bind address                     |
| `GATEWAY_PORT`    | `8080`                                               | Gateway bind port                        |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434`                             | Upstream Ollama base URL (loopback)      |
| `DB_PATH`         | `/home/kanishka/kk_works/LLMs/qwen-stack/data/gateway.db` | SQLite database file                |
| `ADMIN_TOKEN`     | (none — set this)                                    | Protects admin UI + `adminctl`           |
| `DEFAULT_RPM`     | `60`                                                 | Default per-key requests/minute          |

---

## Errors and limits

- **401** — missing, malformed, unknown, or revoked API key.
- **429** — per-key requests-per-minute exceeded (in-memory sliding window).
- **400** — malformed request body.
- **502 / 504** — upstream Ollama failed or timed out.

All errors use the OpenAI-style body:

```json
{"error": {"message": "...", "type": "...", "code": "..."}}
```

---

## Security must-reads

Ollama has **no built-in auth** and ships endpoints that can delete or pull
models. Do not expose it directly. Recommended baseline (see the research notes
and `docs/positioning-and-compliance.md`):

1. **Update Ollama** to a current, patched version (CVE-2026-7482 "Bleeding
   Llama", CVSS 9.3, allowed unauthenticated memory extraction; patched in
   v0.17.1+). Verify with `ollama --version`.
2. **Bind Ollama to loopback**: `OLLAMA_HOST=127.0.0.1:11434`. The gateway is
   the only thing that talks to it.
3. **Firewall the gateway to your LAN subnet** with UFW:
   ```bash
   sudo ufw deny in 8080
   sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp   # your subnet
   sudo ufw status numbered
   ```
   Replace `192.168.1.0/24` with your real subnet (`ip addr show`).
4. **Never expose the gateway or Ollama to the public internet.** For remote
   access use a VPN (Tailscale/WireGuard) or SSH tunnel.
5. Keep `ADMIN_TOKEN` strong; rotate keys via `adminctl` if anything leaks.

---

## Further docs

- [`docs/BYOK-integration.md`](docs/BYOK-integration.md) — register Local Qwen
  as a selectable backend alongside BYO Claude/OpenAI keys; provider matrix;
  fallback strategy.
- [`docs/positioning-and-compliance.md`](docs/positioning-and-compliance.md) —
  honest "built & tested with Claude" positioning; BYOK vs self-hosted
  responsibility split; Qwen2.5 license status; disclaimer.
- [`docs/performance-notes.md`](docs/performance-notes.md) — CPU throughput
  expectations, `num_ctx` guidance, the default-context truncation trap, KV-cache
  RAM cost, and 7B-vs-smaller-model guidance.

---

Built and tested with the help of Claude (Anthropic's assistant). This project
is **not** affiliated with or endorsed by Anthropic. See
`docs/positioning-and-compliance.md`.
