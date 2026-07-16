# Design: Encrypted‑at‑Rest Secret Store (hub)

Status: **built & tested July 2026** (router registered; live after the next hub restart).
Code: `home-hub/app/secrets_store.py`, `home-hub/app/routes_secrets.py`. Tests: `home-hub/tests/test_secrets_store.py`.

## Purpose

The hub previously only **hashed** issued device/gateway keys (sha256) — fine for credentials it merely checks, useless for credentials it must **read back**: cloud provider keys, future Home Assistant tokens, SIP trunk secrets. This store holds retrievable secrets encrypted at rest, closing the 🔵 "encrypted‑at‑rest secret store" item the app‑shells/safety roadmap listed as a cross‑cutting need.

## Design

### Why Fernet

Fernet (from the `cryptography` lib) = **AES‑128‑CBC + HMAC‑SHA256**, i.e. authenticated symmetric encryption:

- **tamper‑evident** — any bit‑flip or wrong key raises `InvalidToken`, never silently returns garbage;
- **versioned token format** and **no nonce/IV management to get wrong**;
- the right default for retrievable app secrets — as opposed to the existing sha256 hashing, which stays correct for issued keys that never need reading back.

### Key custody

- Master key auto‑generated with `Fernet.generate_key()` at `DATA_DIR/secret.key`.
- Created via `os.open(O_WRONLY | O_CREAT | O_EXCL, 0o600)` — **0600 from birth**, and `O_EXCL` means a racing writer can never clobber an existing key.
- Perms are **verified and chmod'd back to 0600 on every read** (backups/copies loosen them).
- The key file is validated before use (urlsafe‑b64 of exactly 32 bytes); a corrupt file raises `SecretStoreError` with an explicit *"restore from backup — deleting it orphans all stored secrets"* message rather than failing deep inside `cryptography`. Decrypting with a replaced key likewise raises `SecretStoreError`, not a bare `InvalidToken`.

### Storage model

Self‑contained module mirroring `db.py`'s conventions: own `_connect()` with the same pragmas/row_factory, `CREATE TABLE IF NOT EXISTS` on connect (does not touch `db.SCHEMA`), module `_LOCK` on writes. Table `secrets(namespace, name, value_encrypted, hint, created_at, updated_at)` with `PRIMARY KEY (namespace, name)`; `set_secret` upserts via `ON CONFLICT`, preserving `created_at`. Both `db_path` and `key_path` are public parameters, so tests never touch the live `hub.db` or real `secret.key`.

## API surface

HTTP is **write‑only** — no route ever returns plaintext; retrieval is server‑side via `secrets_store.get_secret()`. All routes require admin (`auth.require_admin()` — approved admin device + CSRF), errors are `HubError` envelopes, namespace/name are validated as 1–128 char path segments.

| Route | Returns |
|---|---|
| `GET /api/admin/secrets` | namespaces summary `{namespace, count, updated_at}` |
| `GET /api/admin/secrets/{ns}` | per‑secret `{name, hint, updated_at}` — **hint only** |
| `PUT /api/admin/secrets/{ns}/{name}` body `{value}` | `{ok, namespace, name}` — never echoes the value |
| `DELETE /api/admin/secrets/{ns}/{name}` | `{ok, …}` or 404 |

Hints are masked like `sk-a…7f2`; values ≤8 chars are fully masked (`…`) so hints can never leak short PINs.

## Security

**Threat model: at‑rest / backup exfiltration.** A stolen `hub.db` (or a DB‑only backup) is useless without `secret.key` — back the two up **separately**. It does **not** protect against root or the hub user itself, who can read the key file; that is the accepted boundary for an on‑appliance store (no HSM). The write‑only HTTP surface means even an admin session cannot exfiltrate stored values through the API.

## Tests

12 offline unit tests (`pytest`, tmp sqlite + tmp key file, no network): roundtrip/upsert/delete/list semantics, hint masking incl. the short‑value rule, key auto‑creation with 0600 + perm repair, corrupt‑key and replaced‑key `SecretStoreError` paths, `O_EXCL` non‑clobbering. Suite green: part of the hub's 26‑test run (`home-hub/.venv/bin/python -m pytest`).

## Operational notes

- The router is registered in `main.py`; the endpoints appear after the next hub restart.
- `data/secret.key` must be included in (separate) backups — losing it orphans every stored secret; there is deliberately no recovery path.
- The gateway has its **own** parallel Fernet key (`qwen-stack/data/provider.key`) for provider API keys — see `cloud-providers.md`; the two stores are intentionally independent (different trust domains, different DBs).
- `cryptography` is a new runtime dependency of the hub venv.
