"""Number entities — house/DLB current limit and scheduled charge duration."""
from __future__ import annotations

import re

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfElectricCurrent, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, CURRENT_MIN, CURRENT_MAX, ATTR_DLB_MAX_C
from .coordinator import FullPowerCoordinator

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    coordinator: FullPowerCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([
        FullPowerDLBMaxNumber(coordinator),
        FullPowerScheduledDuration(coordinator),
    ])


class _BaseNumber(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

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


class FullPowerDLBMaxNumber(_BaseNumber):
    """House / dynamic-load current limit — free entry (matches the app)."""

    _attr_native_min_value = float(CURRENT_MIN)
    _attr_native_max_value = float(CURRENT_MAX)
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator: FullPowerCoordinator) -> None:
        super().__init__(coordinator, "dlb_max_current", "House Current Limit (DLB)")

    @property
    def native_value(self) -> float | None:
        raw = self.coordinator.device_state.get("DLBMaxC")
        if raw in (None, ""):
            return None
        m = _NUM_RE.search(str(raw))
        return float(m.group()) if m else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.api.update_charge_point_data(
            self.coordinator.mac, self.coordinator.device_type,
            ATTR_DLB_MAX_C, str(int(value)))
        await self.coordinator.async_request_refresh()


class FullPowerScheduledDuration(_BaseNumber):
    """Deferred-charge duration in hours (0 = no time limit). Committed by the
    Scheduled Charge switch, not applied immediately."""

    _attr_native_min_value = 0
    _attr_native_max_value = 24
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator: FullPowerCoordinator) -> None:
        super().__init__(coordinator, "scheduled_duration", "Scheduled Duration")

    @property
    def native_value(self) -> float | None:
        return float(self.coordinator.sched_duration_h)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.sched_duration_h = int(value)
        self.async_write_ha_state()
