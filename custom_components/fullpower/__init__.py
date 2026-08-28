"""Full Power EV Charger — Home Assistant integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    FullPowerApi, FullPowerAuthError, FullPowerApiError, encode_password,
)
from .const import (
    DOMAIN, CONF_USERNAME, CONF_PASSWORD,
    CONF_MAC, CONF_DEVICE_TYPE, CONF_DEVICE_NAME,
    ONOFF_START, ONOFF_STOP, SERVICE_SET_DELAYED_CHARGE,
    CONF_ACTIVE_SECONDS, CONF_IDLE_MINUTES, CONF_MAX_ACTIVE_MINUTES,
    DEFAULT_ACTIVE_SECONDS, DEFAULT_IDLE_MINUTES, DEFAULT_MAX_ACTIVE_MINUTES,
    CONF_RATED_CURRENT, DEFAULT_RATED_CURRENT,
    CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES,
    CONF_CAPTURE, DEFAULT_CAPTURE,
)
from .coordinator import FullPowerCoordinator, seconds_until

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SENSOR, Platform.SWITCH, Platform.NUMBER,
    Platform.SELECT, Platform.TIME, Platform.BUTTON,
]

SET_DELAYED_CHARGE_SCHEMA = vol.Schema({
    vol.Required("mac"): cv.string,
    vol.Optional("time", default=""): cv.string,            # e.g. "22:30" scheduled start
    vol.Optional("on_off", default=ONOFF_START): cv.string,  # "1" start, "0" stop
    vol.Optional("charge_duration", default="0"): cv.string,  # minutes
    vol.Optional("countdown", default="0"): cv.string,        # minutes until start
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    api = FullPowerApi(session)
    try:
        pw_hash = await hass.async_add_executor_job(
            encode_password, entry.data[CONF_PASSWORD])
        await api.login(entry.data[CONF_USERNAME], pw_hash)
    except FullPowerAuthError as err:
        raise ConfigEntryAuthFailed(f"Login failed: {err}") from err
    except FullPowerApiError as err:
        raise ConfigEntryNotReady(f"Cannot reach Kiso cloud: {err}") from err

    coordinator = FullPowerCoordinator(
        hass, api,
        mac=entry.data[CONF_MAC],
        device_type=entry.data[CONF_DEVICE_TYPE],
        device_name=entry.data.get(CONF_DEVICE_NAME) or entry.data[CONF_MAC],
        active_seconds=entry.options.get(CONF_ACTIVE_SECONDS, DEFAULT_ACTIVE_SECONDS),
        idle_minutes=entry.options.get(CONF_IDLE_MINUTES, DEFAULT_IDLE_MINUTES),
        max_active_minutes=entry.options.get(
            CONF_MAX_ACTIVE_MINUTES, DEFAULT_MAX_ACTIVE_MINUTES),
        rated_current=entry.options.get(
            CONF_RATED_CURRENT,
            entry.data.get(CONF_RATED_CURRENT, DEFAULT_RATED_CURRENT)),
        stale_minutes=entry.options.get(CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES),
        capture_enabled=entry.options.get(CONF_CAPTURE, DEFAULT_CAPTURE),
    )
    await coordinator.async_config_entry_first_refresh()

    # Reload the entry when options (polling intervals) change.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator, "api": api}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_DELAYED_CHARGE):
        return

    async def _set_delayed_charge(call: ServiceCall) -> None:
        mac = call.data["mac"]
        entry_data = _find_by_mac(hass, mac)
        if not entry_data:
            raise ValueError(f"No configured Full Power charger with mac {mac}")
        api: FullPowerApi = entry_data["api"]
        coordinator: FullPowerCoordinator = entry_data["coordinator"]
        # Auto-compute countdown (seconds until the HH:MM start) when starting.
        countdown = call.data["countdown"]
        if call.data["on_off"] == ONOFF_START and call.data["time"]:
            countdown = str(seconds_until(call.data["time"]))
        await api.set_delayed_charge(
            mac, coordinator.device_type,
            call.data["time"], call.data["on_off"],
            call.data["charge_duration"], countdown,
        )
        # A delayed/timed start is an HA-initiated session too.
        if call.data["on_off"] == ONOFF_START:
            coordinator.note_charge_initiated()
        elif call.data["on_off"] == ONOFF_STOP:
            coordinator.note_charge_stopped()
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_SET_DELAYED_CHARGE, _set_delayed_charge,
        schema=SET_DELAYED_CHARGE_SCHEMA,
    )


def _find_by_mac(hass: HomeAssistant, mac: str) -> dict | None:
    for data in hass.data.get(DOMAIN, {}).values():
        if isinstance(data, dict) and data.get("coordinator") and data["coordinator"].mac == mac:
            return data
    return None


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when polling-interval options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SET_DELAYED_CHARGE)
    return unloaded
