# Home LLM Hub — Deployment Guide

This directory contains everything needed to ship the **Home LLM Hub**
(`llm.home`) as a self-hosted family appliance that runs **next to the existing
gateway** at `/home/kanishka/kk_works/LLMs/qwen-stack`. The hub is a *client* of
that gateway — it never modifies it.

There are two ways to run the hub, depending on how polished you want the
URL/experience to be.

---

## Path A — Rootless now (no sudo, fastest)

Runs the hub on a high port (`HUB_PORT`, default **8090**). No root, ever.
Access it by IP+port from any LAN device.

```bash
cd /home/kanishka/kk_works/LLMs/home-hub

# 1. Install (venv + deps + sqlite schema). Idempotent, rootless.
./install.sh

# 2. Wire the two gateway secrets into .env (see below), then start:
./start-all.sh                 # background, logs to logs/hub.log
# or foreground:  make run
```

**Exact rootless run command** (foreground, what the scripts wrap):

```bash
/home/kanishka/kk_works/LLMs/home-hub/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8090
```

Open from any family device: `http://<this-host-LAN-ip>:8090`

Stop with `./stop-all.sh` (or `make stop`).

---

## Path B — `http://llm.home` on `:80` (one-time sudo installer)

Upgrades Path A to a real appliance URL: LAN DNS for `llm.home`, an mDNS
fallback (`llm.local`), and the hub bound on `:80` — **while the hub process
still runs rootless**. Only the installer needs root.

```bash
# Run AFTER ./install.sh has created the venv.
sudo bash /home/kanishka/kk_works/LLMs/home-hub/deploy/install-llmhome.sh
```

**One-line sudo installer command:**

```bash
sudo bash deploy/install-llmhome.sh
```

(Optionally pass an explicit LAN IP if auto-detection picks the wrong NIC:
`sudo bash deploy/install-llmhome.sh 192.168.1.50`.)

What the installer does (idempotent, re-runnable):

1. Installs + configures **dnsmasq** with `address=/llm.home/<auto-detected IP>`
   (detected via `ip route get 1.1.1.1`).
2. Frees **port 53** on Ubuntu by disabling only the **systemd-resolved stub
   listener** (`DNSStubListener=no` drop-in) — surgical and reversible; upstream
   DNS keeps working and dnsmasq forwards to it.
3. Enables **avahi-daemon** so `http://llm.local` works with **zero router
   config** (mDNS fallback).
4. Grants the **venv python** `cap_net_bind_service` via **setcap** so it can
   bind `:80` **without running as root** (preferred). nginx fallback below.
5. Sets `HUB_PORT=80` in `.env` and installs + enables the
   **home-hub.service** systemd unit (runs as the unprivileged project owner).

### The one manual step the installer cannot do for you

Point your **router's DHCP "DNS server"** option at this host's LAN IP. Without
it, *only this machine* resolves `llm.home`; the `llm.local` mDNS fallback still
works on most devices.

```
Router admin → LAN / DHCP settings → DNS server → <this-host-LAN-ip>
```

### Verify (in order)

```bash
# on this host
nslookup llm.home 127.0.0.1
curl -I http://127.0.0.1/healthz
# on a WiFi device
nslookup llm.home
# then open http://llm.home/   (or http://llm.local/)
```

### Android gotcha

Android **Private DNS** (DoH) bypasses LAN DNS entirely. If `llm.home` won't
resolve on a phone, set *Settings → Network & internet → Private DNS → Off /
Automatic*, or just use `http://llm.local`.

---

## Wiring the gateway secrets (required for either path)

The hub is a client of the gateway and needs two values from it in `.env`
(`chmod 600 .env`):

| Var | What it is | How to get it |
|-----|------------|---------------|
| `HUB_GATEWAY_KEY` | A gateway API key the **hub** uses for chat | From the gateway dir: `make key NAME="home-hub"` → paste the `qwsk-…` value (shown once) |
| `HUB_ADMIN_TOKEN` | The gateway's `ADMIN_TOKEN`; the hub uses it to mint per-user keys **and** to authorize the first admin device | `grep ADMIN_TOKEN /home/kanishka/kk_works/LLMs/qwen-stack/.env` and copy verbatim |
| `HUB_BOOTSTRAP_TOKEN` | *(optional)* alternate admin-claim secret so you don't type the gateway admin token into a browser | `python3 -c "import secrets; print('hubboot-'+secrets.token_hex(20))"` |

Also pull the models once (CPU-friendly, Apache-2.0, commercially safe):

```bash
ollama pull nomic-embed-text     # embeddings, 768-dim, ~150MB loaded
ollama pull moondream            # vision captions, 1.8GB
# optional upgrade for charts/tables/dense text:  ollama pull qwen2.5vl:7b
```

Claim the first admin device once the hub is running:

```bash
make claim USER=<your-name> TOKEN=<HUB_BOOTSTRAP_TOKEN or HUB_ADMIN_TOKEN>
```

---

## The `:80` + `setcap` interaction (read before you upgrade Python)

`setcap cap_net_bind_service=+ep` is applied to the **real** python binary the
venv symlinks to. This lets the unprivileged hub bind `:80`. **The capability is
lost** whenever the venv is rebuilt or the system python is upgraded — binds
then fail with `EACCES`. Fix: **re-run `sudo bash deploy/install-llmhome.sh`**
(it re-detects everything and re-applies setcap).

### nginx reverse-proxy fallback (if you'd rather not use setcap)

Keep `HUB_PORT=8090` and let nginx own `:80`:

```nginx
# /etc/nginx/sites-available/llm.home  (then symlink into sites-enabled)
server {
    listen 80;
    server_name llm.home llm.local;
    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        # SSE streaming for chat: do not buffer.
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
```

nginx already holds `CAP_NET_BIND_SERVICE`, survives Python upgrades, and is the
production-standard pattern. With this fallback, skip the setcap step and leave
`HUB_PORT=8090`.

---

## Security notes (honest threat model)

- **Trusted-LAN appliance.** This is designed for a home WiFi shared by family
  and invited guests. It assumes the people on the network trust each other and
  that a hostile attacker is **not** already on the LAN.
- **Plain HTTP.** Traffic (including the device cookie) is **unencrypted on the
  wire**. That's acceptable on a private LAN but means anyone sniffing the LAN
  can see it. For real TLS you'd front it with a cert (out of scope here).
- **Passwordless, device-bound auth.** Identity = username + an opaque 40-hex
  **device token** in an httponly cookie (`hub_device`). New devices self-
  register as `pending`/`guest`; an **admin must approve** them. Tokens and
  secrets are stored **only as sha256 hashes**.
- **Cookies:** httponly, `SameSite=Lax`, `path=/`. The `Secure` flag is **off**
  because we serve plain HTTP on the LAN — `Secure` would break it. If you add
  TLS, turn `Secure` on.
- **CSRF defense:** every state-changing request (POST/PUT/DELETE) must carry
  the custom header `X-Hub-CSRF: 1`. Browsers cannot set custom headers on
  cross-site requests, which together with `SameSite=Lax` defeats CSRF. Requests
  missing it are rejected `403`.
- **Server-side authorization on every route.** Privileges (`chat`, `notes`,
  `checklists`, `files_read`, `files_write`, `photos_read`, `photos_write`,
  `api_keys`) are enforced on the server — the client is never trusted. Users
  see only their own conversations/notes/checklists. Files/photos use an
  owner + `shared` flag; readers need the matching `*_read` privilege; only the
  owner or an admin can delete.
- **Upstreams stay on localhost.** The gateway (`:8080`) and Ollama (`:11434`)
  are reached over `127.0.0.1`. Never expose Ollama to the LAN/WAN — it has no
  auth of its own.
- **Per-user keys:** minted through the gateway admin API; the hub stores only
  the returned key **id + prefix** locally — the plaintext is shown to the user
  **once** and never persisted.

---

## Licensing / attribution

Bundled model recommendations are Apache-2.0 and commercially usable:
**moondream** (vision) and **nomic-embed-text** (embeddings). Apache-2.0
requires you to include the copyright notice and license text in distributions.
**Do not ship LLaVA** in a commercial product — its CC BY-NC training data
forbids commercial use despite open weights.

---

## Files in this directory

| File | Purpose |
|------|---------|
| `install-llmhome.sh` | **The sudo product installer** (dnsmasq + :53 fix + setcap + service). |
| `home-hub.service` | systemd unit (runs the venv uvicorn as the project owner; notes the `:80`/setcap interaction). |
| `dnsmasq-llm.conf` | dnsmasq drop-in template (`address=/llm.home/<IP>`); the installer rewrites the IP in place at `/etc/dnsmasq.d/`. |
| `README-deploy.md` | This guide. |

Project-root ops scripts (rootless): `install.sh`, `start-all.sh`,
`stop-all.sh`, `Makefile`, `.env.example`.

---

## Uninstall / revert

```bash
# stop + remove the service
sudo systemctl disable --now home-hub.service
sudo rm -f /etc/systemd/system/home-hub.service
sudo systemctl daemon-reload

# remove LAN DNS + re-enable the systemd-resolved stub listener
sudo rm -f /etc/dnsmasq.d/dnsmasq-llm.conf
sudo systemctl disable --now dnsmasq            # or restart if you use it elsewhere
sudo rm -f /etc/systemd/resolved.conf.d/llm-home.conf
sudo systemctl restart systemd-resolved

# drop the :80 capability from the venv python
sudo setcap -r "$(readlink -f /home/kanishka/kk_works/LLMs/home-hub/.venv/bin/python)"

# (optional) revert HUB_PORT back to 8090 in .env
sed -i 's/^HUB_PORT=.*/HUB_PORT=8090/' /home/kanishka/kk_works/LLMs/home-hub/.env
```

The hub data (sqlite db + uploads) under `data/` is untouched by uninstall.
```
