# Privacy & Security — Home LLM Hub

This page is the honest account of what the Home LLM Hub does with your data and
what its security does — and does not — protect against. It is written plainly
so a non-technical family member and a careful admin can both trust it.

**Short version:** everything stays on your home box. There are no passwords and
(by default) no HTTPS, because this is built for a *trusted home LAN* — your
family and the guests you invite onto your Wi-Fi. That is a deliberate design
choice with real limits, spelled out below. It is **not** safe to put on the
public internet.

---

## 1. Everything stays on the home box

- **No cloud, no external calls.** Chat goes to a local gateway on
  `127.0.0.1:8080`; embeddings and photo captions go to a local Ollama on
  `127.0.0.1:11434`. All three run on the same machine as the Hub. Nothing is
  sent to Anthropic, OpenAI, Google, or anyone else.
- **No telemetry, no analytics, no CDNs.** The web pages, JavaScript, and CSS
  are served from the box itself. The Hub works with the house disconnected from
  the internet.
- **The model is local.** Qwen2.5-7B, the `moondream` vision model, and the
  `nomic-embed-text` embedding model all run on your hardware. Your prompts,
  documents, and photos are processed on-premises and never used to train anyone
  else's model.

If the box is off the internet, the Hub still works. That's the whole point.

---

## 2. What the Hub stores (and what it deliberately does not)

Stored in the SQLite database (`data/hub.db`):

| Table              | Holds                                                                 |
| ------------------ | --------------------------------------------------------------------- |
| `devices`          | Username, role, status, privileges, timestamps, and the **sha256 hash** of the device token (never the token itself). |
| `conversations` / `messages` | Your chat titles and message text, tied to your username. Private to you. |
| `notes`            | Your note titles, bodies, colors, pins. Private to you.               |
| `checklists` / `checklist_items` | Your lists and items. Private to you.                    |
| `files`            | Metadata for uploads: owner, kind (file/photo), filename, stored path, mime, size, the **shared** flag, the photo caption, indexed status. |
| `file_chunks`      | Extracted text chunks and their embedding vectors (float32 bytes) for search. |
| `user_keys`        | For each API key you mint: owner, the gateway **key id**, the display **prefix**, a name, and a revoked flag. **Not** the full key. |

Stored on disk (`data/uploads/<owner>/<uuid>_<safe_filename>`):

- The **raw bytes** of every file and photo you upload. These are served only
  after the Hub checks your authorization and validates the path (basename only;
  never served by a raw client-supplied path).

Stored as **hashes only — never plaintext:**

- **Device tokens.** The cookie holds an opaque token; the database holds only
  its sha256 hash. A database leak does not reveal usable tokens.
- **Secrets** (admin/bootstrap tokens used at claim time) are compared by hash.

Shown **once, then not persisted by the Hub:**

- **Your minted API key.** When you create a key, the gateway returns the
  plaintext exactly once and the Hub displays it to you. The Hub keeps only the
  key's id + prefix so you can recognize and revoke it. Lose it and you must
  revoke and re-create.

What the Hub does **not** store: your password (there is none), any cloud
credentials, and the plaintext of device tokens or API keys.

---

## 3. The honest auth threat model

The Hub is **passwordless and device-bound**, and runs over **plain HTTP on the
LAN by default**. Here is exactly how that holds up.

### How identity and sessions work

- **Identity** = a free-text username (no password) + a per-device token, a
  random 40-hex value set as an httponly cookie named `hub_device`. The server
  stores only the token's sha256 hash.
- **Trust-on-first-use (TOFU).** A new device self-registers as a pending guest.
  It can do nothing useful until an **admin approves it**. That human approval
  step is the gate that stops a stranger from silently provisioning access.
- **Admin claim.** The first admin is bootstrapped by presenting the gateway's
  admin token (or the bootstrap token). Whoever holds that token can become
  admin — so that token is the crown jewel.

### The browser-attack defenses (and why they work)

- **httponly cookie** — JavaScript (including any XSS) cannot read the device
  token, so it can't be exfiltrated from the page.
- **SameSite=Lax + a required custom header** — every state-changing request
  (POST/PUT/DELETE) must include `X-Hub-CSRF: 1`. A malicious third-party site
  *cannot* set a custom header on a cross-site request, and SameSite=Lax keeps
  the cookie from riding along on cross-site form posts. Together these defeat
  CSRF even over plain HTTP. Requests missing the header are rejected with
  `403`.
- **Server-side authorization on every route** — the client UI hiding a control
  is never trusted. Each route re-checks the device's role and privileges, and
  scopes data to the owning user. The system **fails closed**: missing/invalid
  device -> `401`; insufficient privilege -> `403`.
- **Immediate revocation** — an admin can revoke a device; its next request is
  rejected and the old token can't be replayed.

### What this design does NOT protect against — be honest with yourself

This is secure **for a trusted home LAN only**. It assumes the people on your
Wi-Fi don't attack each other and that no hostile actor is on the network. Under
that assumption it's solid. Outside it:

- **The wire is plaintext.** Plain HTTP means anyone who can sniff your LAN
  traffic (an attacker already on your Wi-Fi, a compromised router) can read
  device tokens, chat content, and uploads in transit. If a network attacker is
  on your LAN, plain HTTP is already lost — but note: device binding limits the
  damage, because a sniffed token is bound to *its* device and registering the
  attacker's device triggers a new, admin-visible approval.
- **No TLS by default.** The cookie is **not** marked `Secure` (a `Secure`
  cookie would simply break on HTTP). If you front the Hub with TLS, set
  `COOKIE_SECURE=1` and use HTTPS — see the note below.
- **Physical device theft.** An unlocked, already-approved phone is logged in
  until the admin revokes that device. Lock your devices.
- **A compromised server or malware on a user's device** is out of scope — if
  the box or a client is owned, no app-level control saves you.
- **Not for the public internet.** Do not port-forward this. There are no
  passwords and no transport encryption by default. For remote access use a VPN
  (Tailscale / WireGuard) or an SSH tunnel so it stays effectively "on the LAN".

### A note on TLS (`Secure` cookies)

Cookies are `httponly`, `SameSite=Lax`, `path=/`. They are **not** `Secure`
because the default deployment is plain HTTP on the LAN, and a `Secure` cookie
is dropped on non-HTTPS connections. If you add TLS (e.g. a real cert behind
nginx, or a private CA for `llm.home`), turn on `COOKIE_SECURE=1` and serve over
HTTPS; the CSRF and SameSite protections then get the additional benefit of an
encrypted channel.

---

## 4. Admin responsibilities

The household admin is the trust anchor. With no passwords, your judgment *is*
the security. Please:

1. **Guard the admin / bootstrap token.** It can mint a new admin. Keep `.env`
   at `chmod 600`, never paste the token into chat/email/screenshots, and rotate
   the gateway's `ADMIN_TOKEN` if it might have leaked.
2. **Approve deliberately.** Look at each pending device's username and
   first/last-seen before approving. If a name you don't recognize appears, or a
   known person has an unexpected second device, **ask first**. This human check
   is the main defense on a shared LAN.
3. **Grant least privilege.** Start from the role defaults (guest = chat;
   member = chat/notes/checklists/files/photos; admin = everything) and only add
   what someone needs. Don't hand out `api_keys` or admin casually.
4. **Revoke promptly.** Lost phone, departed guest, or anything suspicious —
   revoke the device immediately, and revoke any API keys that person minted.
5. **Keep the upstreams locked down.** Ollama (`11434`) must stay on
   `127.0.0.1` — it has no auth of its own. The gateway and the Hub are the only
   network-facing services, and the gateway should be firewalled to your LAN
   subnet. Keep Ollama patched.
6. **Back up `data/` and protect `.env`.** See the admin guide for backup
   commands. Store backups off the box; keep `.env` backups access-controlled,
   since they contain the admin token.
7. **Set expectations with the family.** Tell them: no passwords; this device =
   this login; losing a device means asking you to revoke and re-approve; it
   works on home Wi-Fi only; the browser's "not secure" warning is expected at
   home.

---

## At a glance

- **Stays local:** chat, search, captions, files, photos — all on the box,
  offline-capable, no telemetry.
- **Stored as hashes only:** device tokens, claim secrets. **Stored once then
  dropped:** your API key plaintext.
- **Protects against:** CSRF, XSS-reading-the-token, casual cross-user snooping,
  and stolen-token reuse on another device — all enforced server-side,
  fail-closed.
- **Does NOT protect against:** on-LAN network sniffing (plain HTTP), physical
  theft of an unlocked approved device, a compromised box, or exposure to the
  public internet. Use a VPN for remote access; add TLS if you want the wire
  encrypted.
