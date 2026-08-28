"""Config flow — username/password login (mirrors LoginViewModel)."""
from __future__ import annotations

import json
import logging
import re
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    FullPowerApi, FullPowerAuthError, FullPowerApiError, encode_password,
)
from .const import (
    DOMAIN, CONF_USERNAME, CONF_PASSWORD,
    CONF_MAC, CONF_DEVICE_TYPE, CONF_DEVICE_NAME,
    CONF_ACTIVE_SECONDS, CONF_IDLE_MINUTES, CONF_MAX_ACTIVE_MINUTES,
    DEFAULT_ACTIVE_SECONDS, DEFAULT_IDLE_MINUTES, DEFAULT_MAX_ACTIVE_MINUTES,
    CONF_RATED_CURRENT, DEFAULT_RATED_CURRENT, RATED_CURRENT_TIERS, tier_for,
    CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES,
    CONF_CAPTURE, DEFAULT_CAPTURE,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


def _device_name(d: dict) -> str:
    return d.get("deviceAlias") or d.get("deviceCode") or d.get("mac") or "Charger"


def _detect_rated_current(device: dict) -> int:
    """Infer the charger's rated current tier from its live MaxCurrent."""
    raw = device.get("deviceData")
    if isinstance(raw, str):
        try:
            mc = json.loads(raw).get("MaxCurrent")
            m = re.search(r"\d+", str(mc)) if mc is not None else None
            if m:
                return tier_for(int(m.group()))
        except (ValueError, TypeError):
            pass
    return DEFAULT_RATED_CURRENT


class FullPowerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._username = ""
        self._password = ""
        self._devices: list[dict] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FullPowerOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            session = async_get_clientsession(self.hass)
            api = FullPowerApi(session)
            try:
                pw_hash = await self.hass.async_add_executor_job(
                    encode_password, self._password)
                await api.login(self._username, pw_hash)
                self._devices = await api.get_device_list()
            except FullPowerAuthError:
                errors["base"] = "invalid_auth"
            except FullPowerApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "unknown"

            if not errors:
                if not self._devices:
                    errors["base"] = "no_devices"
                elif len(self._devices) == 1:
                    return await self._create_entry(self._devices[0])
                else:
                    return await self.async_step_pick_device()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_pick_device(self, user_input=None):
        if user_input is not None:
            mac = user_input[CONF_MAC]
            device = next((d for d in self._devices if d.get("mac") == mac), None)
            if device:
                return await self._create_entry(device)
            return self.async_abort(reason="unknown")

        device_map = {d.get("mac"): _device_name(d) for d in self._devices}
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema({vol.Required(CONF_MAC): vol.In(device_map)}),
        )

    async def async_step_reauth(self, entry_data):
        self._username = entry_data.get(CONF_USERNAME, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            api = FullPowerApi(async_get_clientsession(self.hass))
            try:
                pw_hash = await self.hass.async_add_executor_job(
                    encode_password, password)
                await api.login(self._username, pw_hash)
            except FullPowerAuthError:
                errors["base"] = "invalid_auth"
            except FullPowerApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, CONF_PASSWORD: password})

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": self._username},
            errors=errors,
        )

    async def _create_entry(self, device: dict):
        mac = device.get("mac")
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=_device_name(device),
            data={
                CONF_USERNAME:      self._username,
                CONF_PASSWORD:      self._password,
                CONF_MAC:           mac,
                CONF_DEVICE_TYPE:   device.get("deviceType"),
                CONF_DEVICE_NAME:   _device_name(device),
                CONF_RATED_CURRENT: _detect_rated_current(device),
            },
        )


class FullPowerOptionsFlow(config_entries.OptionsFlow):
    """Lets the user tune the adaptive polling intervals."""

    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self._entry.options
        schema = vol.Schema({
            vol.Optional(
                CONF_ACTIVE_SECONDS,
                default=opts.get(CONF_ACTIVE_SECONDS, DEFAULT_ACTIVE_SECONDS),
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=600)),
            vol.Optional(
                CONF_IDLE_MINUTES,
                default=opts.get(CONF_IDLE_MINUTES, DEFAULT_IDLE_MINUTES),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=240)),
            vol.Optional(
                CONF_MAX_ACTIVE_MINUTES,
                default=opts.get(CONF_MAX_ACTIVE_MINUTES, DEFAULT_MAX_ACTIVE_MINUTES),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
            # Floor of 30 min: the idle heartbeat itself can sit ~20-25 min
            # behind, so anything tighter would flap on a healthy charger.
            vol.Optional(
                CONF_STALE_MINUTES,
                default=opts.get(CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES),
            ): vol.All(vol.Coerce(int), vol.Range(min=30, max=1440)),
            vol.Optional(
                CONF_RATED_CURRENT,
                default=opts.get(
                    CONF_RATED_CURRENT,
                    self._entry.data.get(CONF_RATED_CURRENT, DEFAULT_RATED_CURRENT)),
            ): vol.In(RATED_CURRENT_TIERS),
            vol.Optional(
                CONF_CAPTURE,
                default=opts.get(CONF_CAPTURE, DEFAULT_CAPTURE),
            ): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
