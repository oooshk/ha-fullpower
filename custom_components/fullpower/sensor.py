"""Sensors mapped to ChargingPileDeviceInfoMqttBean / deviceData fields.

Some units send only per-phase values (V_L1.., A_L1..) and leave the aggregate
fields (voltage / chargingCurrent / chargingPower) empty. For those we derive
the aggregate from the phases so HA shows real numbers instead of "Unknown".
"""
from __future__ import annotations

import logging
import re

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent, UnitOfElectricPotential,
    UnitOfEnergy, UnitOfPower, UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, OCPP_STATES
from .coordinator import FullPowerCoordinator

_LOGGER = logging.getLogger(__name__)

_PHASES_V = ("V_L1", "V_L2", "V_L3")
_PHASES_A = ("A_L1", "A_L2", "A_L3")


_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _num(state: dict, key: str):
    """Parse a numeric value, tolerating unit suffixes like '236.9V' / '7199W'."""
    v = state.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.search(str(v))
    return float(m.group()) if m else None


def _val_energy(state):
    """registerEnergy arrives like '2825Wh' (or 'x.ykWh'); return kWh."""
    raw = state.get("registerEnergy")
    n = _num(state, "registerEnergy")
    if n is None:
        return None
    return round(n, 3) if "kWh" in str(raw) else round(n / 1000.0, 3)


def _phase_pairs(state):
    for vk, ak in zip(_PHASES_V, _PHASES_A):
        v, a = _num(state, vk), _num(state, ak)
        if v is not None and a is not None:
            yield v, a


# ── derived value functions (fall back to per-phase data) ─────────────────────

def _val_power(state):
    p = _num(state, "chargingPower")
    if p is not None:
        return p
    pairs = list(_phase_pairs(state))
    return round(sum(v * a for v, a in pairs), 1) if pairs else None


def _val_voltage(state):
    v = _num(state, "voltage")
    if v is not None:
        return v
    live = [p for p in (_num(state, k) for k in _PHASES_V) if p]  # non-zero phases
    return max(live) if live else None


def _val_current(state):
    c = _num(state, "chargingCurrent")
    if c is not None:
        return c
    phases = [_num(state, k) for k in _PHASES_A]
    if all(p is None for p in phases):
        return None
    return round(sum(p for p in phases if p is not None), 2)


def _val_field(field):
    """Numeric field accessor (suffix-tolerant)."""
    return lambda state: _num(state, field)


def _val_text(field):
    """Text field accessor (status, error codes)."""
    return lambda state: (state.get(field) or None)


V = SensorDeviceClass.VOLTAGE
A = SensorDeviceClass.CURRENT
W = SensorDeviceClass.POWER
E = SensorDeviceClass.ENERGY
T = SensorDeviceClass.TEMPERATURE
B = SensorDeviceClass.BATTERY
MEAS = SensorStateClass.MEASUREMENT
TOTAL = SensorStateClass.TOTAL_INCREASING

# key, name, value_fn, device_class, state_class, unit, options
SENSORS = [
    ("voltage",      "Voltage",         _val_voltage, V, MEAS, UnitOfElectricPotential.VOLT, None),
    ("voltage_l1",   "Voltage L1",      _val_field("V_L1"), V, MEAS, UnitOfElectricPotential.VOLT, None),
    ("voltage_l2",   "Voltage L2",      _val_field("V_L2"), V, MEAS, UnitOfElectricPotential.VOLT, None),
    ("voltage_l3",   "Voltage L3",      _val_field("V_L3"), V, MEAS, UnitOfElectricPotential.VOLT, None),
    ("current",      "Charging Current",_val_current, A, MEAS, UnitOfElectricCurrent.AMPERE, None),
    ("current_l1",   "Current L1",      _val_field("A_L1"), A, MEAS, UnitOfElectricCurrent.AMPERE, None),
    ("current_l2",   "Current L2",      _val_field("A_L2"), A, MEAS, UnitOfElectricCurrent.AMPERE, None),
    ("current_l3",   "Current L3",      _val_field("A_L3"), A, MEAS, UnitOfElectricCurrent.AMPERE, None),
    ("max_current",  "Max Current",     _val_field("MaxCurrent"), A, MEAS, UnitOfElectricCurrent.AMPERE, None),
    ("power",        "Charging Power",  _val_power, W, MEAS, UnitOfPower.WATT, None),
    ("energy",       "Session Energy",  _val_energy, E, TOTAL, UnitOfEnergy.KILO_WATT_HOUR, None),
    ("soc",          "State of Charge", _val_field("SoC"), B, MEAS, PERCENTAGE, None),
    ("temperature",  "Temperature",     _val_field("temperature"), T, MEAS, UnitOfTemperature.CELSIUS, None),
    ("status",       "Charge Point Status", _val_text("ChargePointStatus"), None, None, None, OCPP_STATES),
    ("error_code",   "Charge Point Error",  _val_text("ChargePointErrorCode"), None, None, None, None),
]


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    coordinator: FullPowerCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(FullPowerSensor(coordinator, *s) for s in SENSORS)


class FullPowerSensor(CoordinatorEntity, SensorEntity):

    def __init__(self, coordinator, key, name, value_fn, dev_class, state_class, unit, options):
        super().__init__(coordinator)
        self._value_fn = value_fn
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.mac}_{key}"
        self._attr_device_class = dev_class
        self._attr_state_class = state_class
        self._attr_native_unit_of_measurement = unit
        if options is not None:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = options
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.mac)},
            name=coordinator.device_name,
            manufacturer=MANUFACTURER,
            model="EV Charging Pile",
        )

    @property
    def native_value(self):
        return self._value_fn(self.coordinator.device_state)
