"""Select entities — charge mode (4 combos) and charging current limit (per model)."""
from __future__ import annotations

import re

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, MANUFACTURER, CHARGE_MODE_OPTIONS, ATTR_CHARGE_MODE, ATTR_COMPATIBLE,
    ladder_for, ATTR_MAX_CURRENT,
)
from .coordinator import FullPowerCoordinator

_NUM_RE = re.compile(r"\d+")


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    coordinator: FullPowerCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([
        FullPowerChargeModeSelect(coordinator),
        FullPowerChargeCurrentSelect(coordinator),
    ])


class _BaseSelect(CoordinatorEntity, SelectEntity):
    def __init__(self, coordinator: FullPowerCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.mac)},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model="EV Charging Pile",
        )


class FullPowerChargeModeSelect(_BaseSelect):
    """Charge mode = (chargeMode, Compatible). Sets both attributes."""

    _attr_options = list(CHARGE_MODE_OPTIONS.keys())

    def __init__(self, coordinator: FullPowerCoordinator) -> None:
        super().__init__(coordinator, "charge_mode", "Charge Mode")

    @property
    def current_option(self) -> str | None:
        state = self.coordinator.device_state
        cm = state.get("chargeMode")
        if cm is None:
            return None
        cm = str(cm)
        comp = str(state.get("Compatible", "0"))  # default normal if absent
        for label, (m, c) in CHARGE_MODE_OPTIONS.items():
            if cm == m and comp == c:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        combo = CHARGE_MODE_OPTIONS.get(option)
        if combo is None:
            return
        mode, compatible = combo
        self.coordinator.note_setting_write("Charge Mode")
        await self.coordinator.api.update_charge_point_data(
            self.coordinator.mac, self.coordinator.device_type, ATTR_CHARGE_MODE, mode)
        await self.coordinator.api.update_charge_point_data(
            self.coordinator.mac, self.coordinator.device_type, ATTR_COMPATIBLE, compatible)
        await self.coordinator.async_request_refresh()


class FullPowerChargeCurrentSelect(_BaseSelect):
    """Charging current limit — dropdown ladder for the charger's rated current."""

    def __init__(self, coordinator: FullPowerCoordinator) -> None:
        super().__init__(coordinator, "charge_current", "Charging Current")
        amps = ladder_for(coordinator.rated_current)
        self._options_amps = sorted(amps)            # ascending for display
        self._attr_options = [f"{a} A" for a in self._options_amps]

    @property
    def current_option(self) -> str | None:
        raw = self.coordinator.device_state.get("MaxCurrent")
        if raw in (None, ""):
            return None
        m = _NUM_RE.search(str(raw))                 # handles 32, "32", "32A"
        if not m:
            return None
        label = f"{int(m.group())} A"
        return label if label in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        m = _NUM_RE.search(option)
        if not m:
            return
        self.coordinator.note_setting_write("Charging Current")
        await self.coordinator.api.update_charge_point_data(
            self.coordinator.mac, self.coordinator.device_type, ATTR_MAX_CURRENT, m.group())
        await self.coordinator.async_request_refresh()
