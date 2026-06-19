"""Button entity — reboot the charger (deviceService/reboot)."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import FullPowerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    coordinator: FullPowerCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([FullPowerRebootButton(coordinator)])


class FullPowerRebootButton(CoordinatorEntity, ButtonEntity):
    _attr_name = "Reboot"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = None

    def __init__(self, coordinator: FullPowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_reboot"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.mac)},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model="EV Charging Pile",
        )

    async def async_press(self) -> None:
        _LOGGER.info("Rebooting charger %s", self.coordinator.mac)
        await self.coordinator.api.reboot(
            self.coordinator.mac, self.coordinator.device_type)
