"""Unit tests for app.smarthome pure helpers — no network, no I/O.

Covers the three translation seams the hybrid layer is built on: entity
normalization, the normalized-action -> HA-service map, and the LAN-only URL
guard that keeps local control local.
"""
import pytest

from app import smarthome


# --- domain_of / is_controllable -------------------------------------------
def test_domain_of():
    assert smarthome.domain_of("light.living_room") == "light"
    assert smarthome.domain_of("sensor.kitchen_temp") == "sensor"
    assert smarthome.domain_of("") == ""


def test_is_controllable():
    assert smarthome.is_controllable("light")
    assert smarthome.is_controllable("lock")
    assert not smarthome.is_controllable("sensor")
    assert not smarthome.is_controllable("weather")


# --- require_lan_url --------------------------------------------------------
@pytest.mark.parametrize("url", [
    "http://192.168.1.20:8123",
    "http://10.0.0.5:8123",
    "http://172.16.4.4:8123",
    "http://localhost:8123",
    "http://homeassistant.local:8123",
    "192.168.1.20:8123",            # scheme optional
])
def test_require_lan_url_accepts_lan(url):
    assert smarthome.require_lan_url(url).endswith("8123") or "local" in url


@pytest.mark.parametrize("url", [
    "http://8.8.8.8:8123",
    "https://ha.example.com",
    "http://93.184.216.34",
])
def test_require_lan_url_rejects_public(url):
    with pytest.raises(ValueError):
        smarthome.require_lan_url(url)


def test_require_lan_url_empty():
    with pytest.raises(ValueError):
        smarthome.require_lan_url("")


def test_require_lan_url_allow_non_lan(monkeypatch):
    monkeypatch.setattr(smarthome.config, "SMARTHOME_ALLOW_NON_LAN", True)
    assert smarthome.require_lan_url("https://ha.example.com") == "https://ha.example.com"


# --- normalize_ha_state -----------------------------------------------------
def test_normalize_light():
    e = smarthome.normalize_ha_state({
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {"friendly_name": "Living Room", "brightness": 128},
    })
    assert e["domain"] == "light"
    assert e["name"] == "Living Room"
    assert e["state"] == "on"
    assert e["controllable"] is True
    assert e["attributes"]["brightness"] == 128


def test_normalize_sensor_is_readonly():
    e = smarthome.normalize_ha_state({
        "entity_id": "sensor.kitchen_temp",
        "state": "21.4",
        "attributes": {"friendly_name": "Kitchen Temp", "unit_of_measurement": "°C"},
    })
    assert e["domain"] == "sensor"
    assert e["controllable"] is False


def test_normalize_falls_back_to_entity_id_for_name():
    e = smarthome.normalize_ha_state({"entity_id": "switch.pump", "state": "off"})
    assert e["name"] == "switch.pump"
    assert e["controllable"] is True


# --- action_to_ha_service ---------------------------------------------------
def test_action_turn_on_off():
    assert smarthome.action_to_ha_service("light", "turn_on") == ("light", "turn_on", {})
    assert smarthome.action_to_ha_service("switch", "turn_off") == ("switch", "turn_off", {})


def test_action_set_brightness():
    dom, svc, data = smarthome.action_to_ha_service(
        "light", "set_brightness_pct", {"value": 30})
    assert (dom, svc) == ("light", "turn_on")
    assert data == {"brightness_pct": 30}


def test_action_brightness_out_of_range():
    with pytest.raises(ValueError):
        smarthome.action_to_ha_service("light", "set_brightness_pct", {"value": 250})


def test_action_cover():
    assert smarthome.action_to_ha_service("cover", "open") == ("cover", "open_cover", {})
    assert smarthome.action_to_ha_service("cover", "close") == ("cover", "close_cover", {})


def test_action_lock():
    assert smarthome.action_to_ha_service("lock", "lock") == ("lock", "lock", {})
    assert smarthome.action_to_ha_service("lock", "unlock") == ("lock", "unlock", {})


def test_action_climate():
    dom, svc, data = smarthome.action_to_ha_service(
        "climate", "set_temperature", {"value": 21.5})
    assert (dom, svc) == ("climate", "set_temperature")
    assert data == {"temperature": 21.5}


def test_action_unsupported():
    with pytest.raises(ValueError):
        smarthome.action_to_ha_service("sensor", "turn_on")
    with pytest.raises(ValueError):
        smarthome.action_to_ha_service("light", "explode")


def test_push_bridge_is_stub():
    bridge = smarthome.PushBridge()
    assert bridge.available() is False
