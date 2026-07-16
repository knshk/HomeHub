"""Family calendar events + chores store (Household Content).

Self-contained like secrets_store: own lazy tables, own connections, does not
touch db.SCHEMA. All SQL is parameterized. Store-level module so the date math
and rotation logic are unit-testable against a tmp sqlite DB (db_path param)
without FastAPI or the live hub.db.

Recurrence model: EXPAND-ON-READ. A recurring event is stored ONCE (the anchor
row); list_events() expands it into concrete occurrences inside the requested
window, capped at 500 total. Nothing is materialized, so editing/deleting the
anchor retroactively fixes every occurrence and the DB never grows with time.

Month-end clamping: monthly/yearly recurrences keep the ANCHOR day-of-month
and clamp each occurrence to the target month's last day. A monthly event
anchored Jan 31 lands on Feb 28 (29 in leap years) and returns to Mar 31 —
the clamped day is never carried forward.

Errors: invalid input raises ValueError (routes translate to HubError 400);
missing rows return None/False (routes translate to HubError 404).
"""
import calendar
import datetime as _dt
import json
import sqlite3
import time
from pathlib import Path

from . import config

# Safety valve on expansion so a wide window over daily recurrences cannot
# produce an unbounded response.
MAX_OCCURRENCES = 500

RECURRENCES = ("daily", "weekly", "monthly", "yearly")
CADENCES = ("once", "daily", "weekly")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT,
    duration_min INTEGER,
    person TEXT,
    notes TEXT,
    recurrence TEXT CHECK (recurrence IS NULL OR recurrence IN ('daily','weekly','monthly','yearly')),
    created_by TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    assignee TEXT,
    cadence TEXT NOT NULL DEFAULT 'once' CHECK (cadence IN ('once','daily','weekly')),
    due_date TEXT,
    done_at TEXT,
    rotation TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
CREATE INDEX IF NOT EXISTS idx_chores_due ON chores(due_date);
"""


def _now() -> int:
    return int(time.time())


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection like db.connect(), but table-lazy and path-injectable."""
    conn = sqlite3.connect(str(db_path or config.DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.executescript(_SCHEMA)
    return conn


# ----------------------------------------------------------------------------
# Validation helpers (shared with routes_calendar)
# ----------------------------------------------------------------------------
def parse_date(value, label: str = "date") -> _dt.date:
    """Strict YYYY-MM-DD -> date, else ValueError."""
    try:
        return _dt.datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be YYYY-MM-DD")


def parse_time(value, label: str = "time") -> str:
    """Strict HH:MM (24h) -> normalized 'HH:MM', else ValueError."""
    try:
        return _dt.datetime.strptime(str(value), "%H:%M").strftime("%H:%M")
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be HH:MM")


def _clean_title(title) -> str:
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    return title[:200]


def _clean_duration(value):
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        raise ValueError("duration_min must be an integer")


def _clean_recurrence(value):
    if value is not None and value not in RECURRENCES:
        raise ValueError(f"recurrence must be one of {RECURRENCES} or null")
    return value


def _clean_cadence(value):
    if value not in CADENCES:
        raise ValueError(f"cadence must be one of {CADENCES}")
    return value


def _rotation_to_json(rotation):
    """Rotation is a JSON list of names the assignee cycles through."""
    if rotation in (None, []):
        return None
    if (not isinstance(rotation, list)
            or not all(isinstance(n, str) and n.strip() for n in rotation)):
        raise ValueError("rotation must be a list of non-empty names")
    return json.dumps([n.strip() for n in rotation])


# ----------------------------------------------------------------------------
# Recurrence math (pure stdlib datetime/calendar)
# ----------------------------------------------------------------------------
def _nth_occurrence(anchor: _dt.date, freq: str, n: int) -> _dt.date:
    """The n-th occurrence (n=0 is the anchor itself).

    Monthly/yearly clamp anchor.day to the target month's length (Jan 31
    monthly -> Feb 28 -> Mar 31): the anchor day is preserved across steps.
    """
    if freq == "daily":
        return anchor + _dt.timedelta(days=n)
    if freq == "weekly":
        return anchor + _dt.timedelta(weeks=n)
    if freq == "monthly":
        months = anchor.month - 1 + n
        year, month = anchor.year + months // 12, months % 12 + 1
    else:  # yearly
        year, month = anchor.year + n, anchor.month
    return _dt.date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def _first_n_on_or_after(anchor: _dt.date, freq: str, start: _dt.date) -> int:
    """Smallest n with occurrence(n) >= start; O(1) estimate + tiny scan."""
    if anchor >= start:
        return 0
    days = (start - anchor).days
    if freq == "daily":
        n = days
    elif freq == "weekly":
        n = days // 7
    elif freq == "monthly":
        n = (start.year - anchor.year) * 12 + start.month - anchor.month - 1
    else:  # yearly
        n = start.year - anchor.year - 1
    n = max(0, n)
    while _nth_occurrence(anchor, freq, n) < start:
        n += 1
    return n


# ----------------------------------------------------------------------------
# Events
# ----------------------------------------------------------------------------
def add_event(title, date, *, time=None, duration_min=None, person=None,
              notes=None, recurrence=None, created_by=None,
              db_path: str | Path | None = None) -> dict:
    title = _clean_title(title)
    date = parse_date(date).isoformat()
    time = None if time is None else parse_time(time)
    duration_min = _clean_duration(duration_min)
    recurrence = _clean_recurrence(recurrence)
    now = _now()
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO events (title, date, time, duration_min, person, notes, "
            "recurrence, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (title, date, time, duration_min, person, notes, recurrence,
             created_by, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM events WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_event(event_id: int, db_path: str | Path | None = None) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def update_event(event_id: int, fields: dict,
                 db_path: str | Path | None = None) -> dict | None:
    """Partial update. Returns the updated row, or None if the id is unknown."""
    sets, params = [], []
    if "title" in fields:
        sets.append("title=?"); params.append(_clean_title(fields.get("title")))
    if "date" in fields:
        sets.append("date=?"); params.append(parse_date(fields.get("date")).isoformat())
    if "time" in fields:
        t = fields.get("time")
        sets.append("time=?"); params.append(None if t is None else parse_time(t))
    if "duration_min" in fields:
        sets.append("duration_min=?"); params.append(_clean_duration(fields.get("duration_min")))
    if "person" in fields:
        sets.append("person=?"); params.append(fields.get("person"))
    if "notes" in fields:
        sets.append("notes=?"); params.append(fields.get("notes"))
    if "recurrence" in fields:
        sets.append("recurrence=?"); params.append(_clean_recurrence(fields.get("recurrence")))
    if not sets:
        raise ValueError("no fields to update")
    sets.append("updated_at=?"); params.append(_now())
    params.append(event_id)
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone() is None:
            return None
        conn.execute(f"UPDATE events SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def delete_event(event_id: int, db_path: str | Path | None = None) -> bool:
    conn = _connect(db_path)
    try:
        cur = conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_events(start_date, end_date,
                db_path: str | Path | None = None) -> list[dict]:
    """Concrete occurrences in [start_date, end_date] (both inclusive).

    Recurring anchors are expanded on read (see module docstring); each
    occurrence is the event dict with `id` = source event id and `date` set to
    the occurrence date. Capped at MAX_OCCURRENCES total; sorted by date, time.
    """
    start = parse_date(start_date, "start")
    end = parse_date(end_date, "end")
    if end < start:
        raise ValueError("end must be on or after start")
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE (recurrence IS NULL AND date BETWEEN ? AND ?) "
            "OR (recurrence IS NOT NULL AND date <= ?) ORDER BY date ASC, id ASC",
            (start.isoformat(), end.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        if len(out) >= MAX_OCCURRENCES:
            break
        ev = dict(r)
        if ev["recurrence"] is None:
            out.append(ev)  # its stored date IS the occurrence (query bounded it)
            continue
        anchor = parse_date(ev["date"])
        n = _first_n_on_or_after(anchor, ev["recurrence"], start)
        occ = _nth_occurrence(anchor, ev["recurrence"], n)
        while occ <= end and len(out) < MAX_OCCURRENCES:
            o = dict(ev)
            o["date"] = occ.isoformat()
            out.append(o)
            n += 1
            occ = _nth_occurrence(anchor, ev["recurrence"], n)
    out.sort(key=lambda o: (o["date"], o["time"] or "", o["id"]))
    return out


# ----------------------------------------------------------------------------
# Chores
# ----------------------------------------------------------------------------
def _chore_to_dict(row) -> dict:
    d = dict(row)
    d["rotation"] = json.loads(d["rotation"]) if d["rotation"] else None
    return d


def add_chore(title, *, assignee=None, cadence="once", due_date=None,
              rotation=None, db_path: str | Path | None = None) -> dict:
    title = _clean_title(title)
    cadence = _clean_cadence(cadence)
    due_date = None if due_date is None else parse_date(due_date, "due_date").isoformat()
    rotation = _rotation_to_json(rotation)
    now = _now()
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO chores (title, assignee, cadence, due_date, rotation, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (title, assignee, cadence, due_date, rotation, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM chores WHERE id=?", (cur.lastrowid,)).fetchone()
        return _chore_to_dict(row)
    finally:
        conn.close()


def update_chore(chore_id: int, fields: dict,
                 db_path: str | Path | None = None) -> dict | None:
    """Partial update. Returns the updated row, or None if the id is unknown."""
    sets, params = [], []
    if "title" in fields:
        sets.append("title=?"); params.append(_clean_title(fields.get("title")))
    if "assignee" in fields:
        sets.append("assignee=?"); params.append(fields.get("assignee"))
    if "cadence" in fields:
        sets.append("cadence=?"); params.append(_clean_cadence(fields.get("cadence")))
    if "due_date" in fields:
        d = fields.get("due_date")
        sets.append("due_date=?")
        params.append(None if d is None else parse_date(d, "due_date").isoformat())
    if "rotation" in fields:
        sets.append("rotation=?"); params.append(_rotation_to_json(fields.get("rotation")))
    if not sets:
        raise ValueError("no fields to update")
    sets.append("updated_at=?"); params.append(_now())
    params.append(chore_id)
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM chores WHERE id=?", (chore_id,)).fetchone() is None:
            return None
        conn.execute(f"UPDATE chores SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM chores WHERE id=?", (chore_id,)).fetchone()
        return _chore_to_dict(row)
    finally:
        conn.close()


def delete_chore(chore_id: int, db_path: str | Path | None = None) -> bool:
    conn = _connect(db_path)
    try:
        cur = conn.execute("DELETE FROM chores WHERE id=?", (chore_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def complete_chore(chore_id: int, db_path: str | Path | None = None) -> dict | None:
    """Mark a chore done. Returns the updated row, or None if unknown.

    - cadence 'once': terminal — done_at is stamped and the chore drops out of
      the default listing.
    - cadence 'daily'/'weekly': the chore recurs, so done_at stays NULL; the
      due_date rolls forward one period (from today when no due_date is set)
      and the assignee advances through the rotation list, wrapping around.
      An assignee not present in the rotation hands off to rotation[0].
    """
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM chores WHERE id=?", (chore_id,)).fetchone()
        if row is None:
            return None
        now = _now()
        if row["cadence"] == "once":
            done_at = _dt.datetime.now().isoformat(timespec="seconds")
            conn.execute("UPDATE chores SET done_at=?, updated_at=? WHERE id=?",
                         (done_at, now, chore_id))
        else:
            base = (parse_date(row["due_date"], "due_date") if row["due_date"]
                    else _dt.date.today())
            step = _dt.timedelta(days=1 if row["cadence"] == "daily" else 7)
            assignee = row["assignee"]
            names = json.loads(row["rotation"]) if row["rotation"] else []
            if names:
                if assignee in names:
                    assignee = names[(names.index(assignee) + 1) % len(names)]
                else:
                    assignee = names[0]
            conn.execute(
                "UPDATE chores SET due_date=?, assignee=?, updated_at=? WHERE id=?",
                ((base + step).isoformat(), assignee, now, chore_id),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM chores WHERE id=?", (chore_id,)).fetchone()
        return _chore_to_dict(row)
    finally:
        conn.close()


def list_chores(include_done: bool = False,
                db_path: str | Path | None = None) -> list[dict]:
    """Open chores ordered by due date (undated last); done ones on request."""
    sql = "SELECT * FROM chores"
    if not include_done:
        sql += " WHERE done_at IS NULL"
    sql += (" ORDER BY (done_at IS NOT NULL) ASC, (due_date IS NULL) ASC, "
            "due_date ASC, id ASC")
    conn = _connect(db_path)
    try:
        return [_chore_to_dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()
