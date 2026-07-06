# Admin Guide — Home LLM Hub

This guide is for the household admin (usually whoever owns the box). It covers
claiming the first admin device, approving everyone else, assigning roles and
privileges, revoking access, and keeping the data safe.

If you just want to *use* the Hub, see [`user-guide.md`](user-guide.md).
For the trust model behind the passwordless design, see [`privacy.md`](privacy.md).

---

## 1. Claiming the admin device

The Hub is **passwordless and device-bound**. Every browser that visits is a
"device": it gets an opaque token stored as an httponly cookie (`hub_device`),
and the Hub remembers that device. A brand-new device is trusted-on-first-use
and self-registers as **`role="guest"`, `status="pending"`** — it can't do
anything until an admin approves it.

At the very beginning there is no admin, so you bootstrap one by **claiming** it
with the gateway's admin token.

### Before you start

In the Hub's `.env`, two secrets matter for claiming:

- `HUB_ADMIN_TOKEN` — this is the **gateway's `ADMIN_TOKEN`** (the same value
  from `qwen-stack/.env`). The Hub uses it both to mint user API keys *and* to
  authorize an admin claim.
- `HUB_BOOTSTRAP_TOKEN` — an optional, separate secret that can **only** be used
  to claim admin (handy if you'd rather not reuse the gateway token here).

A claim succeeds when the submitted `admin_token` equals **either** of those.

### Steps

1. From the box owner's device, open the Hub:
   `http://<LAN_IP>:8090` (or `http://llm.home` if the installer ran).
2. Enter a username and join. Your device registers as a pending guest.
3. Claim admin — use the in-app "Claim admin" form, or call the API. Note the
   required `X-Hub-CSRF: 1` header (every state-changing request needs it):

   ```bash
   curl http://<LAN_IP>:8090/api/session/claim \
     -H "Content-Type: application/json" \
     -H "X-Hub-CSRF: 1" \
     --cookie-jar cookies.txt --cookie cookies.txt \
     -d '{"username":"mom","admin_token":"<GATEWAY ADMIN_TOKEN>"}'
   ```

4. On success your device becomes **`role="admin"`, `status="approved"`** with
   **all** privileges. Reload the Hub; you'll see the **Admin** area.

> Anyone holding the admin/bootstrap token can become admin. Treat it like a
> root password: keep `.env` at `chmod 600`, never paste the token into chat or
> screenshots, and rotate the gateway's `ADMIN_TOKEN` if it ever leaks.

---

## 2. Approving devices

When a family member or guest joins, their device shows up pending. Approve it
before they can use anything beyond the join screen.

1. Open **Admin -> Devices** (or `GET /api/admin/devices`). Pending devices list
   the username, the current role/status, and when the device was first/last
   seen.
2. Approve with a role and privilege set:

   ```bash
   curl http://<LAN_IP>:8090/api/admin/devices/<DEVICE_ID>/approve \
     -H "Content-Type: application/json" \
     -H "X-Hub-CSRF: 1" \
     --cookie cookies.txt \
     -d '{"role":"member","privileges":["chat","notes","checklists","files_read","files_write","photos_read","photos_write"]}'
   ```

   The device flips to `status="approved"` with the role and privileges you
   chose. The user does **not** re-enter anything — their existing cookie now
   carries the new access on their next request.

**Each browser/phone is its own device.** If the same person adds a second phone
or a different browser, that's a new pending device to approve. This is the
point of device binding: a token stolen from one device is useless elsewhere
because the other device was never approved.

### Watch for surprises

The device list shows username, first-seen, and last-seen. If a username you
don't recognize appears, or a known person suddenly has a second unexpected
device, **don't approve it** — ask them first. On a shared LAN this is your main
line of defense, so review before you click.

---

## 3. Roles and privileges

A device has one **role** and an explicit **privilege list**. The role sets the
sensible default privileges; you can then tailor the privilege list per device.
Privileges are always enforced **server-side** — the UI hiding a button is not
security; the route check is.

### The eight privileges

| Privilege      | Grants                                                      |
| -------------- | ---------------------------------------------------------- |
| `chat`         | Use the chat assistant; create/read own conversations.     |
| `notes`        | Create, edit, delete own notes.                            |
| `checklists`   | Create, edit, delete own checklists and items.             |
| `files_read`   | Read/download files (own + ones marked **shared**); search.|
| `files_write`  | Upload files; delete own files.                            |
| `photos_read`  | Read/download photos (own + **shared**); search photos.    |
| `photos_write` | Upload photos; delete own photos.                          |
| `api_keys`     | Mint and revoke personal gateway API keys.                 |

### The three roles and their default privileges

| Role       | Default privileges                                                                                       | Typical use                              |
| ---------- | -------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| **guest**  | `chat`                                                                                                    | A visitor; chat only, nothing stored to share. |
| **member** | `chat`, `notes`, `checklists`, `files_read`, `files_write`, `photos_read`, `photos_write`                 | A family member; everything but admin + API keys by default. |
| **admin**  | **all** of the above plus `api_keys` and the admin endpoints                                              | You; approves devices, sets roles.       |

> Defaults are a starting point. On approval you may pass any `privileges` list.
> For example, give a teen `member` minus `files_write`, or grant a power user
> `api_keys` without making them an admin. **Only admins** can approve devices
> and change roles/privileges, regardless of what other privileges someone has.

### Data isolation (always on, regardless of role)

- Users see only **their own** notes, checklists, and conversations — never
  anyone else's.
- Files and photos have an **owner** and a **shared** flag. A reader needs the
  matching `*_read` privilege; they can see their own items plus anything marked
  shared. **Only the owner or an admin can delete** a file or photo.
- Search results are scoped the same way: a user only gets hits from items they
  are allowed to read.

---

## 4. Revoking access

Revoke a device the moment it's lost, stolen, or no longer welcome.

```bash
curl http://<LAN_IP>:8090/api/admin/devices/<DEVICE_ID>/revoke \
  -H "X-Hub-CSRF: 1" \
  --cookie cookies.txt
```

Revocation is immediate and **fail-closed**: the device's next request is
rejected (it must be re-approved to return). The token can't be replayed. The
user's data is untouched — revoking the *device* doesn't delete the *user's*
notes/files; it just cuts off that browser.

Related cleanup:

- **A user's API keys** are separate from their device. Revoking a device does
  not revoke the gateway keys they minted. To cut off a key, revoke it via the
  Hub (`POST /api/keys/{id}/revoke`) — which calls the gateway's
  `POST /admin/keys/{id}/revoke` — or revoke it directly on the gateway with
  `adminctl.py revoke-key`.
- **Lost the admin device?** Re-claim from another browser using the
  admin/bootstrap token (Section 1). Then revoke the lost device.
- **Token leaked / suspected compromise?** Rotate the gateway's `ADMIN_TOKEN`
  (and `HUB_GATEWAY_KEY` if needed) in both `.env` files and restart the
  affected service.

---

## 5. Where the data lives

Everything is on **this box** — nothing leaves the LAN. Default locations
(override via env where noted):

| What                          | Location                                                          | Notes                                            |
| ----------------------------- | ----------------------------------------------------------------- | ------------------------------------------------ |
| SQLite database               | `/home/kanishka/kk_works/LLMs/home-hub/data/hub.db` (`DB_PATH`)    | Devices, users, conversations, messages, notes, checklists, file metadata + chunk embeddings, API-key records. |
| Uploaded files & photos       | `/home/kanishka/kk_works/LLMs/home-hub/data/uploads/<owner>/<uuid>_<safe_filename>` | Raw bytes; served only after authz + path-safety checks, never by raw client path. |
| Config & secrets              | `/home/kanishka/kk_works/LLMs/home-hub/.env`                       | `HUB_ADMIN_TOKEN`, `HUB_GATEWAY_KEY`, etc. `chmod 600`, never commit. |

What the database **does not** store in plaintext: device tokens and secrets are
kept only as **sha256 hashes**, and minted API keys are stored only as their
gateway **key id + display prefix** (the full key is shown to the user once and
never persisted by the Hub). See [`privacy.md`](privacy.md) for the full
inventory.

The Hub's SQLite tables (for reference):

```
devices, conversations, messages, notes, checklists, checklist_items,
files, file_chunks (embedding stored as float32 bytes), user_keys
```

---

## 6. Backups

Because everything is in `data/`, a backup is a copy of two things: the SQLite
DB and the uploads tree. Back up `.env` separately and securely (it holds
secrets).

### Simple cold backup (Hub briefly stopped — safest)

```bash
# Stop the Hub first so the DB isn't mid-write, then:
tar czf hub-backup-$(date +%F).tar.gz \
    -C /home/kanishka/kk_works/LLMs/home-hub data
# Restart the Hub afterward.
```

### Hot backup of the database (Hub running)

Use SQLite's consistent online backup so you don't capture a half-written file:

```bash
sqlite3 /home/kanishka/kk_works/LLMs/home-hub/data/hub.db \
  ".backup '/path/to/backups/hub-$(date +%F).db'"
# Then copy the uploads tree separately:
rsync -a /home/kanishka/kk_works/LLMs/home-hub/data/uploads/ \
         /path/to/backups/uploads/
```

### Restore

```bash
# Stop the Hub, then put files back where they came from:
cp /path/to/backups/hub-YYYY-MM-DD.db \
   /home/kanishka/kk_works/LLMs/home-hub/data/hub.db
rsync -a /path/to/backups/uploads/ \
         /home/kanishka/kk_works/LLMs/home-hub/data/uploads/
# Start the Hub.
```

Tips:

- Keep backups **off the box** (external drive, another machine on the LAN). A
  backup on the same disk doesn't survive a disk failure.
- The embeddings inside `file_chunks` are derived data; if a backup ever lacks
  them, re-uploading regenerates captions/embeddings. The raw uploads and the DB
  rows are the irreplaceable parts.
- Store `.env` backups encrypted or somewhere access-controlled — it contains
  the admin token.

---

## Quick command reference

```bash
# List devices (pending + approved)
curl http://<LAN_IP>:8090/api/admin/devices --cookie cookies.txt

# Approve a device with a role + privileges
curl http://<LAN_IP>:8090/api/admin/devices/<ID>/approve \
  -H "Content-Type: application/json" -H "X-Hub-CSRF: 1" --cookie cookies.txt \
  -d '{"role":"member","privileges":["chat","notes","checklists","files_read","files_write","photos_read","photos_write"]}'

# Revoke a device
curl http://<LAN_IP>:8090/api/admin/devices/<ID>/revoke \
  -H "X-Hub-CSRF: 1" --cookie cookies.txt

# Who am I right now?
curl http://<LAN_IP>:8090/api/me --cookie cookies.txt
```
