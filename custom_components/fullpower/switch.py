"""Switches — start/stop charging, and DLB (dynamic load balancing) enable."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, MANUFACTURER, ONOFF_START, ONOFF_STOP, ATTR_DLB_ENABLE,
)
from .coordinator import FullPowerCoordinator

_LOGGER = logging.getLogger(__name__)

# Statuses that mean charging is actually on (swipe-card "Preparing" = plugged
# but waiting for auth, so it is NOT counted as on).
CHARGING_ON_STATES = {"Charging", "SuspendedEV"}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    coordinator: FullPowerCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([
        FullPowerChargeSwitch(coordinator),
        FullPowerDLBSwitch(coordinator),
        FullPowerScheduledChargeSwitch(coordinator),
    ])


class _BaseSwitch(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH

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


class FullPowerChargeSwitch(_BaseSwitch):
    def __init__(self, coordinator: FullPowerCoordinator) -> None:
        super().__init__(coordinator, "charging", "Charging")
        # Optimistic state bridges the gap until the device confirms the change.
        self._optimistic: bool | None = None
        self._optimistic_ticks = 0

    def _status(self) -> str | None:
        return self.coordinator.device_state.get("ChargePointStatus")

    def _status_is_on(self) -> bool | None:
        status = self._status()
        return None if status is None else status in CHARGING_ON_STATES

    @callback
    def _handle_coordinator_update(self) -> None:
        # Clear the optimistic override once the device agrees, or after a few
        # updates as a safety net so it can never get stuck.
        if self._optimistic is not None:
            self._optimistic_ticks += 1
            if self._status_is_on() == self._optimistic or self._optimistic_ticks >= 6:
                self._optimistic = None
        super()._handle_coordinator_update()

    @property
    def is_on(self) -> bool | None:
        if self._optimistic is not None:
            return self._optimistic
        return self._status_is_on()

    @property
    def extra_state_attributes(self) -> dict:
        return {"charge_point_status": self._status()}

    def _set_optimistic(self, value: bool) -> None:
        self._optimistic = value
        self._optimistic_ticks = 0
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.api.set_charging(
            self.coordinator.mac, self.coordinator.device_type, ONOFF_START)
        self.coordinator.note_charge_initiated()
        self._set_optimistic(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.api.set_charging(
            self.coordinator.mac, self.coordinator.device_type, ONOFF_STOP)
        self.coordinator.note_charge_stopped()
        self._set_optimistic(False)
        await self.coordinator.async_request_refresh()


class FullPowerScheduledChargeSwitch(_BaseSwitch):
    """Enable/disable the deferred-charge schedule (start time + duration)."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: FullPowerCoordinator) -> None:
        super().__init__(coordinator, "scheduled_charge", "Scheduled Charge")

    @property
    def is_on(self) -> bool | None:
        raw = self.coordinator.device_state.get("scheduleOnoff")
        return None if raw is None else str(raw) not in ("0", "0.0", "False", "false")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "start_time": self.coordinator.sched_start,
            "duration_hours": self.coordinator.sched_duration_h,
        }

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.apply_schedule(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.apply_schedule(False)


class FullPowerDLBSwitch(_BaseSwitch):
    """Dynamic Load Balancing enable (DLBEnable attr, '1'/'0')."""

    def __init__(self, coordinator: FullPowerCoordinator) -> None:
        super().__init__(coordinator, "dlb_enable", "Dynamic Load Balancing")

    @property
    def is_on(self) -> bool | None:
        raw = self.coordinator.device_state.get("DLBEnable")
        return None if raw is None else str(raw) not in ("0", "0.0", "False", "false")

    async def _set(self, value: str) -> None:
        await self.coordinator.api.update_charge_point_data(
            self.coordinator.mac, self.coordinator.device_type, ATTR_DLB_ENABLE, value)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set("1")

    async def async_turn_off(self, **kwargs) -> None:
        await self._set("0")
