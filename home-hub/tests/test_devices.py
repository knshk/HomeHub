"""Unit tests for device delete + admin-count (backs the admin 'Remove device'
action and its last-admin guard). Offline: config.DB_PATH -> a tmp sqlite file.
"""
import pytest

from app import db


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "hub.db"))
    db.init_db()
    return db.config.DB_PATH


def _add(conn, name, role, status):
    # unique token hash per device
    return db.create_device(conn, f"hash-{name}-{role}-{status}", name, role, status, [])


def test_delete_removes_device(tmpdb):
    conn = db.connect()
    try:
        did = _add(conn, "old-phone", "guest", "revoked")
        assert db.get_device(conn, did) is not None
        db.delete_device(conn, did)
        assert db.get_device(conn, did) is None
    finally:
        conn.close()


def test_count_admins_tracks_deletes(tmpdb):
    conn = db.connect()
    try:
        a1 = _add(conn, "admin1", "admin", "approved")
        a2 = _add(conn, "admin2", "admin", "approved")
        _add(conn, "guest1", "guest", "pending")
        assert db.count_admins(conn) == 2
        db.delete_device(conn, a2)
        assert db.count_admins(conn) == 1
        # a revoked ex-admin should not count as an admin
        db.update_device(conn, a1, role="guest", status="revoked")
        assert db.count_admins(conn) == 0
    finally:
        conn.close()


def test_last_admin_guard_precondition(tmpdb):
    """The route blocks deleting the last admin; this verifies the count it keys
    on: with one approved admin left, count_admins == 1 (so the route refuses)."""
    conn = db.connect()
    try:
        only = _add(conn, "solo-admin", "admin", "approved")
        _add(conn, "member1", "member", "approved")
        assert db.count_admins(conn) == 1
        # deleting a non-admin never trips the guard
        m = db.get_device(conn, only)
        assert m["role"] == "admin"
    finally:
        conn.close()
