"""Diagnostics download — snapshot of device identity + live state.

Gives the live device state and identity for troubleshooting.
identity fields in one downloadable file from the HA UI.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD

REDACT = {CONF_USERNAME, CONF_PASSWORD, "accessToken", "refreshToken"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    data = hass.data[DOMAIN].get(entry.entry_id, {})
    coordinator = data.get("coordinator")
    state = dict(coordinator.device_state) if coordinator else {}
    for k in list(state):
        if k in REDACT:
            state[k] = "**REDACTED**"
    return {
        "mac": getattr(coordinator, "mac", None),
        "device_type": getattr(coordinator, "device_type", None),
        "device_name": getattr(coordinator, "device_name", None),
        "live_state": state,
    }
