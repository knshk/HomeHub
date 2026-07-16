"""Unit tests for calendar_store: event CRUD, recurrence expansion (incl.
month-end clamping), chore completion rolling + rotation. Offline, tmp sqlite."""
import pytest

from app import calendar_store as cs


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test-hub.db")


# ----------------------------------------------------------------------------
# Events: CRUD
# ----------------------------------------------------------------------------
def test_event_crud(db):
    ev = cs.add_event("Dentist", "2026-07-20", time="14:30", duration_min=45,
                      person="kid1", notes="bring card", created_by="mom", db_path=db)
    assert ev["id"] == 1
    assert ev["title"] == "Dentist"
    assert ev["date"] == "2026-07-20"
    assert ev["time"] == "14:30"
    assert ev["recurrence"] is None
    assert ev["created_by"] == "mom"

    assert cs.get_event(1, db_path=db)["title"] == "Dentist"
    assert cs.get_event(999, db_path=db) is None

    up = cs.update_event(1, {"title": "Dentist (moved)", "date": "2026-07-21",
                             "time": None}, db_path=db)
    assert up["title"] == "Dentist (moved)"
    assert up["date"] == "2026-07-21"
    assert up["time"] is None
    assert up["updated_at"] >= ev["updated_at"]

    assert cs.update_event(999, {"title": "x"}, db_path=db) is None
    with pytest.raises(ValueError):
        cs.update_event(1, {}, db_path=db)

    assert cs.delete_event(1, db_path=db) is True
    assert cs.get_event(1, db_path=db) is None
    assert cs.delete_event(1, db_path=db) is False


def test_event_validation(db):
    with pytest.raises(ValueError):
        cs.add_event("", "2026-07-20", db_path=db)
    with pytest.raises(ValueError):
        cs.add_event("x", "20-07-2026", db_path=db)
    with pytest.raises(ValueError):
        cs.add_event("x", "2026-07-20", time="2pm", db_path=db)
    with pytest.raises(ValueError):
        cs.add_event("x", "2026-07-20", duration_min="soon", db_path=db)
    with pytest.raises(ValueError):
        cs.add_event("x", "2026-07-20", recurrence="fortnightly", db_path=db)
    with pytest.raises(ValueError):
        cs.list_events("2026-07-31", "2026-07-01", db_path=db)


# ----------------------------------------------------------------------------
# Events: listing + recurrence expansion
# ----------------------------------------------------------------------------
def test_list_plain_events_range_boundaries(db):
    cs.add_event("before", "2026-06-30", db_path=db)
    cs.add_event("on-start", "2026-07-01", db_path=db)
    cs.add_event("mid", "2026-07-15", db_path=db)
    cs.add_event("on-end", "2026-07-31", db_path=db)
    cs.add_event("after", "2026-08-01", db_path=db)
    got = cs.list_events("2026-07-01", "2026-07-31", db_path=db)
    assert [e["title"] for e in got] == ["on-start", "mid", "on-end"]


def test_daily_expansion_carries_source_id(db):
    ev = cs.add_event("meds", "2026-07-01", recurrence="daily", db_path=db)
    got = cs.list_events("2026-07-10", "2026-07-12", db_path=db)
    assert [o["date"] for o in got] == ["2026-07-10", "2026-07-11", "2026-07-12"]
    assert all(o["id"] == ev["id"] for o in got)
    # anchor after window end -> no occurrences of it
    later = cs.add_event("later", "2026-09-01", recurrence="daily", db_path=db)
    aug = cs.list_events("2026-08-01", "2026-08-05", db_path=db)
    assert all(o["id"] != later["id"] for o in aug)


def test_weekly_expansion(db):
    cs.add_event("piano", "2026-07-01", recurrence="weekly", db_path=db)
    got = cs.list_events("2026-07-01", "2026-07-31", db_path=db)
    assert [o["date"] for o in got] == [
        "2026-07-01", "2026-07-08", "2026-07-15", "2026-07-22", "2026-07-29"]


def test_monthly_month_end_clamp(db):
    # Jan 31 monthly: Feb clamps to 28 (2026 not a leap year), Mar restores 31.
    cs.add_event("rent", "2026-01-31", recurrence="monthly", db_path=db)
    got = cs.list_events("2026-01-01", "2026-04-30", db_path=db)
    assert [o["date"] for o in got] == [
        "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]


def test_yearly_leap_day_clamp(db):
    cs.add_event("bday", "2024-02-29", recurrence="yearly", db_path=db)
    got = cs.list_events("2025-01-01", "2028-12-31", db_path=db)
    assert [o["date"] for o in got] == [
        "2025-02-28", "2026-02-28", "2027-02-28", "2028-02-29"]


def test_expansion_cap_500(db):
    cs.add_event("daily", "2020-01-01", recurrence="daily", db_path=db)
    got = cs.list_events("2020-01-01", "2029-12-31", db_path=db)
    assert len(got) == cs.MAX_OCCURRENCES == 500


def test_list_events_ordering(db):
    cs.add_event("late", "2026-07-02", time="18:00", db_path=db)
    cs.add_event("early", "2026-07-02", time="08:00", db_path=db)
    cs.add_event("allday", "2026-07-01", db_path=db)
    got = cs.list_events("2026-07-01", "2026-07-02", db_path=db)
    assert [e["title"] for e in got] == ["allday", "early", "late"]


# ----------------------------------------------------------------------------
# Chores
# ----------------------------------------------------------------------------
def test_chore_crud_and_validation(db):
    ch = cs.add_chore("Dishes", assignee="ana", cadence="daily",
                      due_date="2026-07-20", rotation=["ana", "ben"], db_path=db)
    assert ch["rotation"] == ["ana", "ben"]
    up = cs.update_chore(ch["id"], {"title": "Dishes+", "rotation": None}, db_path=db)
    assert up["title"] == "Dishes+"
    assert up["rotation"] is None
    assert cs.update_chore(999, {"title": "x"}, db_path=db) is None
    with pytest.raises(ValueError):
        cs.add_chore("x", cadence="monthly", db_path=db)
    with pytest.raises(ValueError):
        cs.add_chore("x", rotation=["ok", ""], db_path=db)
    with pytest.raises(ValueError):
        cs.add_chore("x", due_date="tomorrow", db_path=db)
    assert cs.delete_chore(ch["id"], db_path=db) is True
    assert cs.delete_chore(ch["id"], db_path=db) is False
    assert cs.complete_chore(999, db_path=db) is None


def test_complete_weekly_rolls_and_rotates_wraparound(db):
    ch = cs.add_chore("Trash", assignee="cara", cadence="weekly",
                      due_date="2026-07-20", rotation=["ana", "ben", "cara"],
                      db_path=db)
    done = cs.complete_chore(ch["id"], db_path=db)
    assert done["due_date"] == "2026-07-27"   # +1 week
    assert done["assignee"] == "ana"          # cara -> wrap to ana
    assert done["done_at"] is None            # recurring: never terminal
    # still listed as open
    assert [c["id"] for c in cs.list_chores(db_path=db)] == [ch["id"]]


def test_complete_daily_rolls_one_day_and_handles_unknown_assignee(db):
    ch = cs.add_chore("Feed cat", assignee="guest", cadence="daily",
                      due_date="2026-07-20", rotation=["ana", "ben"], db_path=db)
    done = cs.complete_chore(ch["id"], db_path=db)
    assert done["due_date"] == "2026-07-21"
    assert done["assignee"] == "ana"          # not in rotation -> rotation[0]


def test_complete_once_is_terminal(db):
    ch = cs.add_chore("Fix shelf", assignee="ben", cadence="once",
                      due_date="2026-07-20", db_path=db)
    done = cs.complete_chore(ch["id"], db_path=db)
    assert done["done_at"] is not None
    assert done["due_date"] == "2026-07-20"   # unchanged
    assert done["assignee"] == "ben"          # unchanged
    assert cs.list_chores(db_path=db) == []
    assert [c["id"] for c in cs.list_chores(include_done=True, db_path=db)] == [ch["id"]]


def test_list_chores_ordering(db):
    a = cs.add_chore("later", due_date="2026-08-01", db_path=db)
    b = cs.add_chore("soon", due_date="2026-07-05", db_path=db)
    c = cs.add_chore("undated", db_path=db)
    d = cs.add_chore("finished", due_date="2026-07-01", db_path=db)
    cs.complete_chore(d["id"], db_path=db)
    got = cs.list_chores(include_done=True, db_path=db)
    # open by due date first, undated after dated, done last
    assert [x["id"] for x in got] == [b["id"], a["id"], c["id"], d["id"]]
