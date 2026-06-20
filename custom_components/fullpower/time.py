"""Time entity — deferred charge start time (HH:MM)."""
from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import FullPowerCoordinator


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    coordinator: FullPowerCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([FullPowerScheduledStart(coordinator)])


class FullPowerScheduledStart(CoordinatorEntity, TimeEntity):
    _attr_name = "Scheduled Start Time"
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator: FullPowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_scheduled_start"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.mac)},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model="EV Charging Pile",
        )

    @property
    def native_value(self) -> dt_time | None:
        raw = self.coordinator.sched_start or "00:00"
        try:
            h, m = (int(x) for x in raw.split(":")[:2])
            return dt_time(hour=h, minute=m)
        except (ValueError, AttributeError):
            return None

    async def async_set_value(self, value: dt_time) -> None:
        # Store the desired start; the Scheduled Charge switch commits it.
        self.coordinator.sched_start = value.strftime("%H:%M")
        self.async_write_ha_state()
