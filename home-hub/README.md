# Home LLM Hub (`llm.home`)

A self-hosted, family-friendly portal for your own local LLM. It gives every
person in the house a private login, a chat assistant, sticky **notes**,
**checklists**, **file & photo** storage with **semantic search**, and the
ability to mint a **personal API key** for their own apps — all served from one
small box on your home network, fully offline.

The Hub is a **client** of an existing local gateway (the
[Qwen Stack gateway](../qwen-stack/README.md) at `127.0.0.1:8080`). The Hub does
**not** run or modify that gateway, Ollama, or the models — it talks to them
over localhost. Keep the two cleanly separated: the gateway is the
authenticated front door to the model; the Hub is the people-facing app on top.

- **What it is:** a server-rendered web app (FastAPI + Uvicorn, stdlib
  `sqlite3`, vanilla HTML/JS/CSS — no React, no Node, no build step, no CDNs).
- **Who it is for:** a household on a trusted LAN. Passwordless, device-bound
  logins; an admin (you) approves each new device.
- **Where it runs:** rootless on `HUB_HOST:HUB_PORT` (default `0.0.0.0:8090`).
  A separate **sudo** installer can promote it to `http://llm.home` on port 80.

> **Honesty up front.** This is built for a *trusted home LAN*: no passwords, no
> TLS by default, plain HTTP over your own Wi-Fi. That is a deliberate trade-off
> for a family box, not a mistake. It is **not** safe to expose to the public
> internet. See [`docs/privacy.md`](docs/privacy.md) for the full threat model.

---

## Architecture

```
   Browsers on the LAN (no app install, no build step)
   +-----------+   +-----------+   +-----------+   +-----------+
   |  Android  |   |   iPhone  |   |  Windows  |   |  macOS /  |
   |  phone    |   |  / iPad   |   |  laptop   |   |  Linux    |
   +-----+-----+   +-----+-----+   +-----+-----+   +-----+-----+
         |               |               |               |
         |   http://llm.home   (or  http://<LAN_IP>:8090 )
         |   cookie: hub_device   header: X-Hub-CSRF: 1
         v               v               v               v
   +=====================================================================+
   |        HOME LLM HUB        llm.home:80   (or  0.0.0.0:8090)          |
   |---------------------------------------------------------------------|
   |  * Passwordless, device-bound auth  (TOFU + admin approval)         |
   |  * Per-route privilege checks (chat/notes/checklists/files/photos/  |
   |    api_keys) enforced SERVER-SIDE; users see only their own data    |
   |  * Features: Chat - Notes - Checklists - Files/Photos + Search      |
   |  * SQLite (data/hub.db)   Uploads (data/uploads/<owner>/...)        |
   +======+=========================+=========================+=========+
          |                         |                         |
          | chat (SSE)              | embeddings              | vision caption
          | Bearer HUB_GATEWAY_KEY  | /api/embeddings         | /api/generate
          v                         v                         v
   +=========================+   +=================================================+
   |  QWEN STACK GATEWAY     |   |        OLLAMA   127.0.0.1:11434                 |
   |  127.0.0.1:8080         |   |  (loopback only; no auth of its own)           |
   |  OpenAI-compatible      |   |  nomic-embed-text  (768-d embeddings)          |
   |  /v1/chat/completions   |   |  moondream         (image captions)            |
   |  + /admin/keys (mint)   |   +=================================================+
   +============+============+
                | httpx, loopback only
                v
   +=================================+
   |  Qwen2.5-7B-Instruct (Q4)       |
   |  via Ollama, Apache-2.0          |
   +=================================+
```

- **Chat** streams through the gateway's OpenAI-compatible
  `/v1/chat/completions` (model `qwen2.5-7b`), Server-Sent Events forwarded to
  the browser.
- **Per-user API keys** are minted by the gateway's admin API
  (`POST /admin/keys`); the Hub stores only the key **id + prefix**, never the
  plaintext.
- **Embeddings** (`nomic-embed-text`) and **image captions** (`moondream`) come
  straight from Ollama on `127.0.0.1:11434`. The Hub never exposes Ollama to the
  LAN — the gateway and the Hub are the only network-facing services.

The three bundled models are all **Apache-2.0** and commercially usable:
`qwen2.5-7b`, `moondream` (moondream2), and `nomic-embed-text`. See
[`docs/productizing.md`](docs/productizing.md) for the per-model license detail.

---

## Quickstart (rootless, port 8090)

Prerequisites:

- **Python 3.10+**.
- The **gateway** already running and reachable at `http://127.0.0.1:8080`
  (see [`../qwen-stack/README.md`](../qwen-stack/README.md)), with a gateway key
  for the Hub and the gateway's `ADMIN_TOKEN` available.
- **Ollama** on `127.0.0.1:11434` with the helper models pulled:

  ```bash
  ollama pull moondream            # vision captions (Apache-2.0)
  ollama pull nomic-embed-text     # embeddings     (Apache-2.0)
  ```

Set up and run the Hub (no root needed):

```bash
cd /home/kanishka/kk_works/LLMs/home-hub

# 1. Create a virtualenv and install deps.
python3 -m venv .venv
. .venv/bin/activate
pip install fastapi uvicorn httpx python-dotenv python-multipart \
            pypdf python-docx pillow numpy

# 2. Configure. Copy the example, then fill in the secrets (see table below).
cp .env.example .env
chmod 600 .env
$EDITOR .env
#   HUB_GATEWAY_KEY  = a qwsk-... key minted on the gateway for the Hub
#   HUB_ADMIN_TOKEN  = the gateway's ADMIN_TOKEN (lets the Hub mint user keys
#                      AND lets you claim the first admin device)
#   HUB_BOOTSTRAP_TOKEN = optional second secret usable ONLY to claim admin
#   LAN_IP           = this box's address on your network (e.g. 192.168.1.42)

# 3. Start the Hub (rootless, on 0.0.0.0:8090).
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8090
```

Then open it from any device on the same Wi-Fi:

```
http://<LAN_IP>:8090        e.g.  http://192.168.1.42:8090
```

Find `<LAN_IP>` with `ip -4 addr show scope global`.

### Configuration (env / `.env`)

| Variable              | Default                         | Purpose                                                        |
| --------------------- | ------------------------------- | -------------------------------------------------------------- |
| `HUB_HOST`            | `0.0.0.0`                       | Hub bind address (LAN-reachable).                              |
| `HUB_PORT`            | `8090`                          | Hub bind port (rootless).                                      |
| `LAN_IP`              | `127.0.0.1`                     | This box's LAN address; shown to users for API-key base URLs. |
| `GATEWAY_URL`         | `http://127.0.0.1:8080`         | The gateway (chat + key minting). Loopback.                   |
| `HUB_GATEWAY_KEY`     | (set this)                      | `qwsk-...` key the Hub uses to call the gateway for chat.     |
| `HUB_ADMIN_TOKEN`     | (set this)                      | Gateway `ADMIN_TOKEN`: mints user keys; claims first admin.   |
| `HUB_BOOTSTRAP_TOKEN` | (optional)                      | Alternative secret that *only* claims the admin device.       |
| `OLLAMA_URL`          | `http://127.0.0.1:11434`        | Ollama (embeddings + vision). Loopback only.                 |
| `VISION_MODEL`        | `moondream`                     | Image-captioning model (Apache-2.0).                          |
| `EMBED_MODEL`         | `nomic-embed-text`              | Embedding model, 768-d (Apache-2.0).                          |
| `CHAT_MODEL`          | `qwen2.5-7b`                    | Chat model name passed to the gateway.                        |
| `DB_PATH`             | `…/home-hub/data/hub.db`        | SQLite database file.                                          |

---

## First run: claim the ADMIN device

A brand-new device that visits the Hub is trusted-on-first-use: it
**self-registers** as `role="guest"`, `status="pending"`. Nobody can do anything
useful until an **admin** approves them — and at the very start there is no admin
yet. You bootstrap the first admin by *claiming* it with the gateway's admin
token.

1. On the box owner's device, open `http://<LAN_IP>:8090` and pick a username.
   The device registers as a pending guest.
2. Claim admin. Either use the Hub's "Claim admin" form, or call the API
   directly (the `X-Hub-CSRF: 1` header is required on all state-changing
   requests):

   ```bash
   curl http://<LAN_IP>:8090/api/session/claim \
     -H "Content-Type: application/json" \
     -H "X-Hub-CSRF: 1" \
     --cookie-jar cookies.txt --cookie cookies.txt \
     -d '{"username":"mom","admin_token":"<GATEWAY ADMIN_TOKEN>"}'
   ```

   The `admin_token` must equal the gateway's `ADMIN_TOKEN` (your
   `HUB_ADMIN_TOKEN`) **or** the optional `HUB_BOOTSTRAP_TOKEN`. On success the
   device becomes `role="admin"`, `status="approved"`.

3. You are now the admin. Everyone else's pending devices show up under
   **Admin -> Devices** for you to approve. Full walkthrough:
   [`docs/admin-guide.md`](docs/admin-guide.md).

> The admin token is the keys to the kingdom. Treat it like a root password:
> keep `.env` at `chmod 600`, never paste it into chat, and rotate the gateway's
> `ADMIN_TOKEN` if it leaks.

---

## How everyone joins

1. Connect the phone/laptop to the **same Wi-Fi** as the Hub box.
2. Open `http://<LAN_IP>:8090` (or `http://llm.home` once the installer has
   run — see below).
3. Type a username and tap **Join**. A device token is set as an httponly
   cookie (`hub_device`); the device appears in the admin's approval list as a
   pending guest.
4. The **admin approves** the device and assigns a role + privileges
   (guest / member / admin).
5. Done — that browser stays logged in on that device (the cookie persists). No
   passwords, ever. New browser or phone = a new device that needs its own
   approval.

Step-by-step for end users: [`docs/user-guide.md`](docs/user-guide.md).

---

## The `llm.home` installer (optional, needs sudo)

The Quickstart runs the Hub at `http://<LAN_IP>:8090`. To give the family a
friendly URL — `http://llm.home` on the standard port 80 — run the separate
sudo installer. It does **not** change the Hub app or the gateway; it only sets
up name resolution and a port-80 reverse proxy:

```bash
sudo bash deploy/install-llmhome.sh <LAN_IP> 8090
#   e.g.  sudo bash deploy/install-llmhome.sh 192.168.1.42 8090
```

What it sets up (idempotent, reversible, app keeps running rootless):

- **dnsmasq** answers `llm.home` -> `<LAN_IP>` for the whole LAN
  (`address=/llm.home/<LAN_IP>`). It frees port 53 by disabling only the
  `systemd-resolved` stub listener (`DNSStubListener=no`), preserving your
  upstream DNS.
- **nginx** listens on port 80 and reverse-proxies to the rootless Hub on
  `127.0.0.1:8090`. nginx already holds `CAP_NET_BIND_SERVICE`, so the Python
  app needs **no** elevated privileges and survives system/Python updates.
- **avahi** (mDNS) provides an automatic `http://llm.local` fallback that works
  with zero router configuration if LAN DNS ever fails.

**Critical manual step:** point your **router's DHCP DNS** at `<LAN_IP>` so
every Wi-Fi client resolves `llm.home`. Without it, only this box resolves the
name. Android users with **Private DNS** enabled bypass LAN DNS entirely — they
should set Private DNS to "Off / device default", or just use `http://llm.local`
or `http://<LAN_IP>:8090`. Details and the full revert procedure live in the
deploy docs.

> Browsers will warn that `http://llm.home` is "not secure" — that is expected
> on a plain-HTTP LAN with a made-up domain. It is safe on your own network. TLS
> is out of scope for this family build; see [`docs/privacy.md`](docs/privacy.md).

---

## Feature tour

- **Chat.** A streaming assistant powered by local Qwen2.5-7B through the
  gateway. Conversations are private to you and saved so you can pick them back
  up. Responses stream token-by-token (SSE). Requires the `chat` privilege.
- **Notes.** Quick, colour-coded, pinnable sticky notes. Private to each user.
  Requires `notes`.
- **Checklists.** To-do lists with checkable items, reorderable, for chores,
  shopping, packing. Private to each user. Requires `checklists`.
- **Files & photo search (vision + semantic).** Upload documents (PDF, DOCX,
  text) and photos. The Hub extracts (or captions) the content, splits it into
  chunks, and stores a `nomic-embed-text` embedding per chunk. **Photos are
  captioned by `moondream`** at upload time ("objects, scene, any visible
  text"), and that caption is embedded too — so you can search your pictures by
  what's *in* them. Search blends semantic similarity with keyword matching and
  is scoped to what you're allowed to see (your own items plus anything marked
  **shared**). Requires `files_read`/`files_write` or
  `photos_read`/`photos_write`.
- **Per-user API keys.** Generate a personal `qwsk-...` key to use the household
  model from your own scripts and apps. The Hub asks the gateway to mint it,
  shows you the plaintext **once**, and stores only its id + prefix. Point any
  OpenAI- or Anthropic-SDK client at `http://<LAN_IP>:8080/v1` with model
  `qwen2.5-7b`. Requires `api_keys`. How-to: [`docs/user-guide.md`](docs/user-guide.md).

---

## Documentation

- [`docs/admin-guide.md`](docs/admin-guide.md) — claim admin, approve devices,
  roles & privileges, revoke, where data lives, backups.
- [`docs/user-guide.md`](docs/user-guide.md) — join from a phone/laptop; use
  chat/notes/checklists; upload & search files/photos; make and use an API key.
- [`docs/privacy.md`](docs/privacy.md) — what stays on the box, what the Hub
  stores, the honest passwordless/no-TLS threat model, admin responsibilities.
- [`docs/productizing.md`](docs/productizing.md) — shipping this as a home
  appliance; the `install-llmhome.sh` story; commercial license status of every
  bundled model; the native-app future phase.

---

## Project layout

```
home-hub/
  app/            FastAPI backend (routes, auth, db, gateway/ollama clients)
  templates/      server-rendered HTML
  static/         vanilla JS + CSS (no CDNs, no build step)
  deploy/         install-llmhome.sh, nginx/dnsmasq/avahi configs
  data/
    hub.db        SQLite database
    uploads/<owner>/<uuid>_<safe_filename>   user files & photos
  docs/           the guides linked above
  .env            secrets + config  (chmod 600, never commit)
```

---

Built and tested with the help of Claude (Anthropic's assistant). This project
is **not** affiliated with or endorsed by Anthropic.
