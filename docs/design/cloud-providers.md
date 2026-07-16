# Design: Cloud AI Providers (BYO‑key hybrid, gateway)

Status: **built & tested July 2026** — code merged on both gateway and hub; goes live after a service restart + storing a provider API key.
Code: `qwen-stack/app/providers.py`, routing seam in `qwen-stack/app/openai_routes.py`, admin endpoints in `qwen-stack/app/admin_routes.py`, schema/migrations in `qwen-stack/app/db.py`; hub UI in `home-hub/app/static/app.js` (+ hub proxy routes). Tests: `qwen-stack/tests/test_providers.py`.

## Purpose

Optional Anthropic/OpenAI access through the existing gateway, **without changing the default**: local models stay the house standard, and nothing leaves the appliance unless an admin deliberately turns the cloud on — per provider *and* per API key.

## Design

### Architecture

```
                          ┌──────────────────────── qwen-gateway :8080 ───────────────────────┐
 client                   │                                                                   │
 (hub chat, BYO-key) ──▶  │  auth ▶ rate limits ▶ serve_check (admin kill-switch)             │
   POST /v1/chat/…        │            │                                                      │
                          │            ▼  model alias lookup (managed_models.provider)        │
                          │   ┌────────┴─────────┬──────────────────────────┐                 │
                          │   ▼ 'local'          ▼ 'anthropic'              ▼ 'openai'        │
                          │  Ollama :11434      translate OpenAI⇄native    passthrough        │
                          │  (proxy, unchanged)  /v1/messages (Anthropic)  /v1/chat/completions│
                          └───────────────────────────────────────────────────────────────────┘
```

- **Routing seam** sits in `openai_routes._forward_completion`, immediately after the existing `serve_check` and model‑field validation — so `serve_check` (suspend/stop) doubles as the **cloud kill‑switch** with zero new code, and cloud dispatch branches off before any Ollama URL is built.
- Cloud model rows store the provider‑side model id in **both** `ollama_tag` (NOT NULL; also lets `get_model_by_alias_or_tag` address them by provider model id) and the authoritative `upstream_model` column. `providers` rows are seeded disabled so the admin UI always has rows to show.
- The legacy `/v1/completions` path returns **400** for cloud models — only the chat‑completions shape translates. The Anthropic‑compat shim (`POST /v1/messages`, which forwards to Ollama) likewise rejects cloud aliases — and their provider‑side ids — with 400 `cloud_chat_only` **before any upstream HTTP**, instead of silently misrouting them to Ollama.
- **Native Anthropic Messages API**, not an OpenAI‑compat shim: the gateway already owns bidirectional translation code (`anthropic_routes.py`), so the pure‑function translators mirror that proven pattern, avoid drift in third‑party compat layers, and keep exact semantics testable offline: `end_turn→stop`, `max_tokens→length`, `refusal→content_filter`; `input/output_tokens → prompt/completion_tokens`.
- **Streaming**: `providers.call_provider(stream=True)` yields raw SSE lines; the route translates Anthropic events chunk‑by‑chunk (`message_stop` sentinel → `data: [DONE]`), harvesting `input_tokens` from `message_start` and `output_tokens` from `message_delta`. Usage is logged under the **client alias**, identical in shape to local logging — budgets, dashboards, and per‑key daily limits all keep working unmodified.

### Gating ladder (order matters)

| # | Gate | Failure |
|---|---|---|
| 1 | `api_keys.cloud_allowed` — **per‑key opt‑in** | 403 `cloud_not_allowed` |
| 2 | provider `enabled` **and** key stored | 503 `provider_not_available` |
| 3 | monthly token budget (calendar month over `usage_log`; injectable "now" for tests; **0 = unlimited**) | 429 `cloud_budget_exhausted` |

So a request only reaches the internet when the admin has: stored a provider key, enabled the provider, registered a cloud alias, **and** flipped `cloud_allowed` on that specific API key.

Budget accounting counts usage logged under **either** name a cloud model answers to — the alias *or* the provider‑side id stored in `ollama_tag` (`get_model_by_alias_or_tag` accepts both) — so addressing the model by its upstream id cannot spend past the cap (`db.provider_month_usage`).

### Key custody

Single Fernet key at `qwen-stack/data/provider.key`, created `O_EXCL` mode 0600, living **beside** the SQLite file so a DB‑only backup cannot decrypt stored provider keys. Plaintext keys exist only transiently in memory during dispatch; every outward surface (admin list, provider rows) carries only a masked hint (`first4…last4`).

### Error mapping

Upstream error text is **never leaked to clients** (same stance as the existing Ollama proxy): provider 401 → 502 "provider auth failed"; provider 429 → 429 with `retry-after` forwarded; timeouts → 504; anything else → generic 502.

## API surface

Gateway admin (existing `X-Admin-Token` auth, `/admin` prefix):

| Route | What |
|---|---|
| `GET /admin/providers` | hint, enabled, budget, month usage — never key bytes |
| `PUT /admin/providers/{name}/key` | store key (encrypted); returns masked hint only |
| `POST /admin/providers/{name}/enable` | opt‑in switch |
| `PUT /admin/providers/{name}/budget` | monthly token budget (0 = unlimited) |
| `POST /admin/providers/{name}/models` | register cloud alias → upstream model id |
| `POST /admin/keys/{key_id}/cloud` | flip `cloud_allowed` per API key |

Client surface is unchanged: `POST /v1/chat/completions` with a cloud alias as `model`. `GET /v1/models` badges each model with its `provider` so UIs can mark cloud entries.

## Security & privacy stance

- **Local‑default, cloud‑opt‑in**: four explicit admin actions before a single byte leaves the LAN; the egress lock (see `platform-activation.md`) keeps `qwen-gateway.service` as the only sanctioned egress path and firewalls everything else.
- **Admin‑only model picker in the hub**: the hub enforces server‑side (403) that only admins may override the model per conversation — otherwise any family member could POST a cloud alias and exfiltrate a conversation through the hub's shared gateway key once it is `cloud_allowed`. Members always ride the house default. Per‑conversation choice is memory‑only (reverts on reload).
- Provider API keys: write‑only end to end (password input → PUT → only `key_hint` ever rendered).
- Budgets cap monetary exposure per provider per calendar month.

## Tests

24 offline unit tests (tmp sqlite via `tmp_path`, monkeypatched settings, no network/Ollama): key encrypt/decrypt roundtrip + 0600 + hint masking, gating ladder order (403/503/429) as a pure function, month‑window budget math with frozen "now", both translation directions incl. system‑message extraction, stop‑reason/usage mapping, and SSE event → chunk translation incl. the `[DONE]` sentinel — plus two adversarial regressions: month usage accrues when a model is addressed by its provider‑side id (`test_month_usage_counts_upstream_id_addressing`) and the `/v1/messages` shim 400s on cloud aliases via offline `TestClient` (`test_anthropic_shim_rejects_cloud_models`). Suite green: `qwen-stack/.venv/bin/python -m pytest` (24 passed).

## Operational notes

- **Go‑live checklist**: restart gateway + hub → Models tab → provider card → set key → enable → set budget → add cloud model alias → flip Cloud on the intended API key(s). The hub's own `HUB_GATEWAY_KEY` must be `cloud_allowed` for hub‑chat cloud use — a deliberate decision, since that key is shared by all hub chat (the admin‑only picker is the guard).
- `cryptography` is a new runtime dependency of the gateway venv.
- Back up `data/provider.key` separately from `gateway.db` (mirrors the hub secret‑store rule).
- To take the cloud offline instantly: suspend/stop the cloud model (serve_check blocks it), or disable the provider.
