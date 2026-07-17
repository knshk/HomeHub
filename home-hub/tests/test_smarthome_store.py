"""Unit tests for app.smarthome_store — offline, tmp_path sqlite.

Every call passes explicit db_path so the live hub.db is never opened. The
provider token is NOT part of this store (it lives in secrets_store), so these
tests never touch encryption.
"""
import pytest

from app import smarthome_store as store


@pytest.fixture
def db(tmp_path):
    return tmp_path / "hub.db"


# --- config -----------------------------------------------------------------
def test_config_default_unconfigured(db):
    cfg = store.get_config(db_path=db)
    assert cfg["configured"] is False
    assert cfg["provider"] is None
    assert cfg["connected"] is False


def test_set_and_get_config(db):
    cfg = store.set_config("home_assistant", "http://192.168.1.20:8123/", db_path=db)
    assert cfg["configured"] is True
    assert cfg["provider"] == "home_assistant"
    assert cfg["base_url"] == "http://192.168.1.20:8123"   # trailing slash stripped
    assert cfg["connected"] is False
    # No token field ever leaks through the config row.
    assert "token" not in cfg


def test_set_config_requires_fields(db):
    with pytest.raises(ValueError):
        store.set_config("", "http://192.168.1.20:8123", db_path=db)
    with pytest.raises(ValueError):
        store.set_config("home_assistant", "", db_path=db)


def test_mark_connection(db):
    store.set_config("home_assistant", "http://192.168.1.20:8123", db_path=db)
    store.mark_connection(True, None, synced=True, db_path=db)
    cfg = store.get_config(db_path=db)
    assert cfg["connected"] is True
    assert cfg["last_synced_at"] is not None
    store.mark_connection(False, "boom", db_path=db)
    cfg = store.get_config(db_path=db)
    assert cfg["connected"] is False
    assert cfg["last_error"] == "boom"


def test_reconfigure_resets_connection(db):
    store.set_config("home_assistant", "http://192.168.1.20:8123", db_path=db)
    store.mark_connection(True, None, synced=True, db_path=db)
    store.set_config("home_assistant", "http://192.168.1.30:8123", db_path=db)
    assert store.get_config(db_path=db)["connected"] is False


def test_clear_config(db):
    store.set_config("home_assistant", "http://192.168.1.20:8123", db_path=db)
    store.replace_entities([_ent("light.a")], db_path=db)
    store.clear_config(db_path=db)
    assert store.get_config(db_path=db)["configured"] is False
    assert store.list_entities(db_path=db) == []


# --- entity cache -----------------------------------------------------------
def _ent(entity_id, area=None, state="on"):
    domain = entity_id.split(".")[0]
    return {
        "entity_id": entity_id, "domain": domain, "name": entity_id.upper(),
        "area": area, "state": state, "attributes": {"k": "v"},
        "controllable": domain in ("light", "switch", "lock"),
    }


def test_replace_and_list_entities(db):
    n = store.replace_entities(
        [_ent("light.living", "Living Room"), _ent("sensor.temp", "Kitchen")],
        db_path=db)
    assert n == 2
    all_e = store.list_entities(db_path=db)
    assert {e["entity_id"] for e in all_e} == {"light.living", "sensor.temp"}
    # attributes survive the JSON round-trip
    assert all_e[0]["attributes"] == {"k": "v"}


def test_replace_is_wholesale(db):
    store.replace_entities([_ent("light.a"), _ent("light.b")], db_path=db)
    store.replace_entities([_ent("light.c")], db_path=db)
    ids = {e["entity_id"] for e in store.list_entities(db_path=db)}
    assert ids == {"light.c"}


def test_list_entities_filter_by_domain(db):
    store.replace_entities(
        [_ent("light.a"), _ent("switch.b"), _ent("sensor.c")], db_path=db)
    lights = store.list_entities(domain="light", db_path=db)
    assert [e["entity_id"] for e in lights] == ["light.a"]


def test_get_entity(db):
    store.replace_entities([_ent("lock.front")], db_path=db)
    assert store.get_entity("lock.front", db_path=db)["domain"] == "lock"
    assert store.get_entity("lock.nope", db_path=db) is None


def test_list_rooms_groups_and_unassigned(db):
    store.replace_entities(
        [_ent("light.a", "Living Room"), _ent("light.b", "Living Room"),
         _ent("switch.c")],   # no area -> Unassigned
        db_path=db)
    rooms = {r["area"]: r["entities"] for r in store.list_rooms(db_path=db)}
    assert len(rooms["Living Room"]) == 2
    assert len(rooms["Unassigned"]) == 1


# --- permissions ------------------------------------------------------------
def test_permission_default_deny(db):
    assert store.can_control("light.a", "member", "alex", db_path=db) is False


def test_permission_role_grant(db):
    store.set_permission("light.a", "role:member", True, db_path=db)
    assert store.can_control("light.a", "member", "alex", db_path=db) is True
    assert store.can_control("light.a", "guest", "sam", db_path=db) is False


def test_permission_user_overrides_role(db):
    store.set_permission("light.a", "role:member", True, db_path=db)
    store.set_permission("light.a", "user:alex", False, db_path=db)
    # explicit user deny beats the role allow
    assert store.can_control("light.a", "member", "alex", db_path=db) is False
    # other members still allowed via the role grant
    assert store.can_control("light.a", "member", "jo", db_path=db) is True


def test_permission_invalid_scope(db):
    with pytest.raises(ValueError):
        store.set_permission("light.a", "everyone", True, db_path=db)


def test_list_permissions(db):
    store.set_permission("light.a", "role:member", True, db_path=db)
    store.set_permission("light.a", "user:alex", False, db_path=db)
    perms = store.list_permissions("light.a", db_path=db)
    assert {p["scope"] for p in perms} == {"role:member", "user:alex"}


# --- favourites -------------------------------------------------------------
def test_favorites_add_list_remove(db):
    store.add_favorite("alex", "light.a", db_path=db)
    store.add_favorite("alex", "light.b", db_path=db)
    assert store.list_favorites("alex", db_path=db) == ["light.a", "light.b"]
    assert store.remove_favorite("alex", "light.a", db_path=db) is True
    assert store.list_favorites("alex", db_path=db) == ["light.b"]
    # per-user isolation
    assert store.list_favorites("sam", db_path=db) == []


def test_favorites_add_idempotent(db):
    store.add_favorite("alex", "light.a", db_path=db)
    store.add_favorite("alex", "light.a", db_path=db)
    assert store.list_favorites("alex", db_path=db) == ["light.a"]


def test_remove_missing_favorite(db):
    assert store.remove_favorite("alex", "light.z", db_path=db) is False
