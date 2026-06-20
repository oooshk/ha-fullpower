"""DataUpdateCoordinator — adaptive REST polling for one charger."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FullPowerApi, FullPowerApiError, FullPowerAuthError
from .const import DOMAIN, ACTIVE_MONITOR_STATES

_LOGGER = logging.getLogger(__name__)



def seconds_until(hhmm: str) -> int:
    """Seconds from now until the next occurrence of HH:MM (matches the app)."""
    try:
        h, m = (int(x) for x in hhmm.split(":")[:2])
    except (ValueError, AttributeError):
        return 0
    now = datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    delta = (target - now).total_seconds()
    if delta < 0:
        delta += 86400
    return int(delta)


class FullPowerCoordinator(DataUpdateCoordinator):
    def __init__(
        self, hass, api: FullPowerApi, mac, device_type, device_name,
        active_seconds: int, idle_minutes: int, max_active_minutes: int,
        rated_current: int,
    ) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_{mac}",
            update_interval=timedelta(minutes=idle_minutes),
        )
        self.api = api
        self.mac = mac
        self.device_type = device_type
        self.device_name = device_name
        self.rated_current = rated_current   # charger model rating (16/32/40/48 A)
        self.device_state: dict = {}

        # Pending deferred-charge schedule, seeded once from the device.
        self.sched_start = "00:00"     # HH:MM start time
        self.sched_duration_h = 0      # duration in hours (0 = no time limit)
        self._sched_init = False

        # Tracks the last setting write, to explain a device "Rejected" CmdResult.
        self._pending_write: tuple[str, float] | None = None

        self._active_s = active_seconds
        self._idle_m = idle_minutes
        self._max_active_s = max_active_minutes * 60

        # adaptive-polling state
        self._ha_active = False          # did THIS integration start the current session?
        self._active_started = 0.0
        self._seen_active = False        # have we observed an active status since start?

    # ── interval helpers ──────────────────────────────────────────────────────

    def _idle_interval(self) -> timedelta:
        return timedelta(minutes=self._idle_m)

    def _active_interval(self) -> timedelta:
        return timedelta(seconds=self._active_s)

    def _reschedule(self) -> None:
        """Apply the new update_interval immediately. Callers also refresh, so
        guard the internal reschedule hook against future HA API changes."""
        try:
            self._schedule_refresh()
        except AttributeError:  # pragma: no cover
            pass

    @callback
    def note_charge_initiated(self) -> None:
        """Called when a charge is started VIA this integration -> poll fast."""
        self._ha_active = True
        self._active_started = time.monotonic()
        self._seen_active = False
        self.update_interval = self._active_interval()
        self._reschedule()
        _LOGGER.debug("Fast monitoring enabled (%ss) for %s", self._active_s, self.mac)

    @callback
    def note_setting_write(self, description: str) -> None:
        """Record a setting change so a following 'Rejected' can be explained."""
        self._pending_write = (description, time.monotonic())

    @callback
    def note_command_rejected(self) -> None:
        """Device rejected a command (CmdResult=Rejected) — tell the user why."""
        pw = self._pending_write
        if not pw or (time.monotonic() - pw[1]) > 20:
            return
        self._pending_write = None
        persistent_notification.async_create(
            self.hass,
            f"The charger **rejected** the change to **{pw[0]}**.\n\n"
            "On this firmware that setting usually can't be changed while a "
            "vehicle is connected. Unplug the car, make the change, then plug "
            "back in.",
            title=f"{self.device_name}: change rejected",
            notification_id=f"{DOMAIN}_{self.mac}_rejected",
        )

    @callback
    def note_charge_stopped(self) -> None:
        """Called when charging is stopped via this integration -> back to idle."""
        if self._ha_active:
            self._ha_active = False
            self.update_interval = self._idle_interval()
            self._reschedule()
            _LOGGER.debug("Fast monitoring disabled for %s", self.mac)

    def _update_polling(self, status: str | None) -> None:
        """Choose interval: fast while charging (any initiator) or HA-initiated."""
        if self._ha_active:
            if status in ACTIVE_MONITOR_STATES:
                self._seen_active = True
            elapsed = time.monotonic() - self._active_started
            if (self._seen_active and status not in ACTIVE_MONITOR_STATES) \
                    or elapsed > self._max_active_s:
                self._ha_active = False
        fast = self._ha_active or (status in ACTIVE_MONITOR_STATES)
        # Only set update_interval; the coordinator reschedules after the refresh.
        self.update_interval = self._active_interval() if fast else self._idle_interval()

    # ── polling ───────────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict:
        try:
            devices = await self.api.get_device_list()
        except FullPowerAuthError:
            try:
                await self.api.refresh()
                devices = await self.api.get_device_list()
            except (FullPowerAuthError, FullPowerApiError) as err:
                raise UpdateFailed(f"Auth/refresh failed: {err}") from err
        except FullPowerApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

        device = next((d for d in devices if d.get("mac") == self.mac), None)
        if device is None:
            raise UpdateFailed(f"Device {self.mac} not in account list")

        state = dict(device)
        raw = device.get("deviceData")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    state.update(parsed)
            except (ValueError, TypeError):
                _LOGGER.debug("deviceData not JSON: %r", raw[:120])

        self.device_state = state
        self._update_polling(state.get("ChargePointStatus"))
        self._init_schedule_from_device(state)
        return state

    def _init_schedule_from_device(self, state: dict) -> None:
        if self._sched_init:
            return
        st = state.get("scheduleTime")
        if isinstance(st, str) and ":" in st and "null" not in st and st != "--:--":
            self.sched_start = st[:5]
        dur = state.get("chargeDuration")
        try:
            if dur not in (None, ""):
                self.sched_duration_h = max(0, int(float(dur)) // 3600)
        except (ValueError, TypeError):
            pass
        self._sched_init = True

    async def apply_schedule(self, enabled: bool) -> None:
        """Commit the deferred-charge schedule via onOffTimer."""
        duration = str(self.sched_duration_h or 0)
        if enabled:
            start = self.sched_start or "00:00"
            countdown = str(seconds_until(start))
            await self.api.set_delayed_charge(
                self.mac, self.device_type, start, "1", duration, countdown)
            self.note_charge_initiated()
        else:
            await self.api.set_delayed_charge(
                self.mac, self.device_type, "", "0", duration, "0")
        await self.async_request_refresh()

    def update_from_mqtt(self, data: dict) -> None:
        self.device_state.update(data)
        if "ChargePointStatus" in data:
            # A status change (from any initiator) adjusts the REST cadence too.
            self._update_polling(data["ChargePointStatus"])
        # async_set_updated_data notifies entities and reschedules at the new interval.
        self.async_set_updated_data(self.device_state)
