# Design: Family Calendar & Chores (hub)

Status: **built & tested July 2026** (router registered + Calendar tab shipped; live after the next hub restart).
Code: `home-hub/app/calendar_store.py`, `home-hub/app/routes_calendar.py`, UI in `home-hub/app/static/app.js`/`style.css`. Tests: `home-hub/tests/test_calendar_store.py`.

## Purpose

Shared family calendar (events with optional time/person/recurrence) plus a chores list with cadences and assignee rotation — the 🔵 "shared family calendar & chores" Household Content item. NL entry and ICS import remain future work.

## Design

### Schema

Two lazy tables (`CREATE TABLE IF NOT EXISTS` on connect, own connections, does not touch `db.SCHEMA` — mirrors `secrets_store`, the sibling module):

- `events(id, title, date, time, duration_min, person, notes, recurrence, created_by, created_at, updated_at)` — `recurrence ∈ {NULL, daily, weekly, monthly, yearly}` (CHECK‑constrained), `date` TEXT `YYYY-MM-DD`, `time` TEXT `HH:MM`, both validated by **strict `strptime`**; timestamps are int epoch like neighbouring tables. Indexed on `date`.
- `chores(id, title, assignee, cadence, due_date, done_at, rotation, created_at, updated_at)` — `cadence ∈ {once, daily, weekly}`, `rotation` a JSON list of names. Indexed on `due_date`.

### Recurrence: EXPAND‑ON‑READ

A recurring event is stored **once** as an anchor row; `list_events(start, end)` expands it into concrete occurrences inside the window — each occurrence is the event dict with `id` = the anchor's id and `date` = the occurrence date, capped at `MAX_OCCURRENCES = 500`, sorted by `(date, time, id)`.

Why no materialization:

- editing/deleting the anchor **retroactively fixes all occurrences**;
- the DB never grows over time;
- expansion is O(window): `_first_n_on_or_after` computes an O(1) first‑occurrence estimate plus a tiny scan, so ancient anchors don't cause long walks.

**Month‑end clamping**: monthly/yearly steps preserve the **anchor** day‑of‑month and clamp per occurrence to the target month's length via `calendar.monthrange` — Jan 31 → Feb 28 (29 in leap years) → **Mar 31**; Feb 29 yearly → Feb 28 in non‑leap years. The clamped day is never carried forward.

### Chore completion semantics

| Cadence | On `complete` |
|---|---|
| `once` | `done_at` stamped — terminal; drops out of the default listing |
| `daily` / `weekly` | `done_at` stays NULL; `due_date` rolls +1 day/week (from **today** when undated); assignee advances through the rotation list with wrap‑around; an assignee not in the rotation hands off to `rotation[0]` |

### Error contract

Store raises `ValueError` for invalid input and returns `None`/`False` for missing rows; routes translate to `HubError` 400 / 404 (same thin‑route style as `routes_secrets`). All SQL parameterized.

## API surface

`/api/calendar` — reads need any authenticated **approved** device; writes need the `checklists` privilege (household content shares one grant; server‑enforced, the UI additionally hides write affordances).

| Route | What |
|---|---|
| `GET /events?start&end` | expanded occurrences in the inclusive window |
| `POST /events` · `PUT /events/{id}` · `DELETE /events/{id}` | anchor CRUD (edits/deletes affect the whole series; the UI says so) |
| `GET /chores?include_done=` | open chores, due‑date ordered (undated last) |
| `POST /chores` · `PUT /chores/{id}` · `DELETE /chores/{id}` | chore CRUD |
| `POST /chores/{id}/complete` | cadence‑aware completion (see above) |

UI notes: occurrences share the anchor id, so the editor explicitly edits the series — and when the date field is left unchanged it is **omitted from the PUT**, so editing (say) the Feb 28 occurrence of a Jan 31 monthly series can never silently re‑anchor the series to the occurrence date; local‑time date helpers avoid the UTC shift of `new Date('YYYY-MM-DD')`; `<input type=date/time>` values match the strict parsers; person chips reuse the note‑pastel tokens; <640 px collapses to an agenda view.

## Security

Nothing secret is stored; the exposure is family‑privacy, handled by the existing auth layers: no anonymous reads (approved device required), writes behind the `checklists` privilege, `created_by` recorded from the authenticated device. Input validation (strict date/time formats, title length cap, CHECK‑constrained enums, rotation shape validation) keeps the tables clean.

## Tests

14 offline unit tests against tmp sqlite (`db_path` param — the live `hub.db` is never opened): event CRUD + window queries, daily/weekly/monthly/yearly expansion incl. the Jan 31 and Feb 29 clamp cases and the 500‑occurrence cap, sort order, strict date/time rejection, chore CRUD, once‑vs‑recurring completion, rotation wrap‑around and the off‑rotation hand‑off. Part of the hub's 26‑test run (`home-hub/.venv/bin/python -m pytest`).

## Operational notes

- Live after the next hub restart (router already registered in `main.py`); hard‑refresh past the service worker to pick up the Calendar tab.
- The 500‑occurrence cap is a response‑size safety valve; a normal month view over daily events sits far below it.
- Future (per the roadmap): NL entry via the local LLM, read‑only ICS import; two‑way calendar sync would be a disclosed cloud opt‑in.
