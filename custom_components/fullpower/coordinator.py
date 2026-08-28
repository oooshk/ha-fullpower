"""DataUpdateCoordinator — adaptive REST polling + capture for local-control prep."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FullPowerApi, FullPowerApiError, FullPowerAuthError
from .const import (
    DOMAIN, ACTIVE_MONITOR_STATES, COLD_START_MAX_AGE_H, DEFAULT_STALE_MINUTES,
    DEFAULT_CAPTURE,
)
from . import capture

_LOGGER = logging.getLogger(__name__)

_IDENTITY_KEYS = (
    "mac", "deviceType", "deviceModelCode", "modelCode", "mainType",
    "fwVersion", "serialNo", "deviceCode", "deviceSignCode", "ssid",
)


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
        rated_current: int, stale_minutes: int = DEFAULT_STALE_MINUTES,
        capture_enabled: bool = DEFAULT_CAPTURE,
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
        self._identity_saved = False

        # Pending deferred-charge schedule, seeded once from the device.
        self.sched_start = "00:00"     # HH:MM start time
        self.sched_duration_h = 0      # duration in hours (0 = no time limit)
        self._sched_init = False

        self._active_s = active_seconds
        self._idle_m = idle_minutes
        self._max_active_s = max_active_minutes * 60

        # adaptive-polling state
        self._ha_active = False          # did THIS integration start the current session?
        self._active_started = 0.0
        self._seen_active = False        # have we observed an active status since start?

        # staleness / offline detection (see the const.py note)
        self._capture_enabled = bool(capture_enabled)
        self._stale_s = max(1, int(stale_minutes)) * 60
        self._last_payload_ts: str | None = None   # last `timestamp` value seen
        self._last_fresh = time.monotonic()        # when it last CHANGED, our clock
        self._offline = False

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
    def note_charge_stopped(self) -> None:
        """Called when charging is stopped via this integration -> back to idle."""
        if self._ha_active:
            self._ha_active = False
            self.update_interval = self._idle_interval()
            self._reschedule()
            _LOGGER.debug("Fast monitoring disabled for %s", self.mac)

    # ── staleness / offline detection ────────────────────────────────────────

    @callback
    def note_liveness(self) -> None:
        """Proof the charge point is actually talking to us. Restarts the clock."""
        self._last_fresh = time.monotonic()
        self._offline = False

    @staticmethod
    def _payload_age_s(raw: str) -> float | None:
        """Best-case age of a payload timestamp, in seconds.

        The 'Z' suffix is not to be trusted (see const.py), so read it both as
        local time and as UTC and take whichever looks FRESHER. Only used by the
        cold-start check, which is deliberately generous; the steady-state guard
        never does absolute arithmetic on this value.
        """
        try:
            txt = str(raw).strip().rstrip("Zz")
            parsed = datetime.fromisoformat(txt)
        except (ValueError, TypeError, AttributeError):
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        as_local = (datetime.now() - parsed).total_seconds()
        as_utc = (datetime.utcnow() - parsed).total_seconds()
        return min(abs(as_local), abs(as_utc))

    def _go_offline(self, state: dict, why: str) -> None:
        """Latch offline and reject this poll, so entities go unavailable."""
        self._offline = True
        raise UpdateFailed(
            f"Charge point offline: {why} (cloud still serving "
            f"{state.get('ChargePointStatus')!r})")

    def _check_freshness(self, state: dict) -> None:
        """Raise UpdateFailed when the cloud is replaying a cached record.

        Primary test is change-detection measured on our own clock: if the
        device's `timestamp` has not moved for stale_minutes, the charge point
        is not reporting, whatever ChargePointStatus happens to say. That is
        immune to the device's timezone, to DST, and to clock skew.
        """
        stamp = state.get("timestamp")
        if stamp in (None, ""):
            # Every captured payload carried this field, on both firmwares, but
            # fail OPEN rather than bricking the integration on a variant that
            # does not: a missing field is not evidence of an outage.
            self._offline = False
            self.note_liveness()
            return

        first_poll = self._last_payload_ts is None
        moved = stamp != self._last_payload_ts
        self._last_payload_ts = stamp

        if first_poll:
            # No change-history yet — on the first poll the value always "moves"
            # (from None), so change-detection cannot say anything here. Judge it
            # absolutely instead, generously, because of the timezone caveat.
            # Without this, a HA restart during an outage would present a cached
            # status as live for a full stale window.
            age = self._payload_age_s(stamp)
            if age is not None and age > COLD_START_MAX_AGE_H * 3600:
                _LOGGER.warning(
                    "FullPower %s: first poll returned a record %.1f h old — "
                    "charge point is not reporting. Marking unavailable.",
                    self.mac, age / 3600)
                self._go_offline(
                    state, f"cloud is replaying a record {age / 3600:.1f} h old")
            self._offline = False
            self.note_liveness()
            return

        if moved:
            if self._offline:
                _LOGGER.warning(
                    "FullPower %s is reporting again after %.1f h silent",
                    self.mac, (time.monotonic() - self._last_fresh) / 3600)
                self._offline = False
            self.note_liveness()
            return

        # Unchanged since last poll. Once latched offline, stay offline until the
        # device actually reports something new — never time back into "healthy".
        if self._offline:
            self._go_offline(state, "still not reporting")

        silent_s = time.monotonic() - self._last_fresh
        if silent_s > self._stale_s:
            _LOGGER.warning(
                "FullPower %s has not reported for %.1f min; the cloud is "
                "replaying a cached record (status %s). Marking unavailable.",
                self.mac, silent_s / 60, state.get("ChargePointStatus"))
            self._go_offline(
                state, f"no device report for {silent_s / 60:.0f} min")

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

        # Judge freshness before anything consumes this record. _capture() is
        # skipped on a stale poll on purpose: the capture log is the evidence
        # trail for outages, and 60 h of identical replayed records buries it.
        self._check_freshness(state)

        self.device_state = state
        self._update_polling(state.get("ChargePointStatus"))
        self._init_schedule_from_device(state)
        await self._capture(device, raw)
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
        # chargeDuration is SECONDS on the wire. _init_schedule_from_device()
        # already reads it that way (// 3600), and every other duration the
        # device reports is in seconds (ConnectionTimeOut 120,
        # MinimumStatusDuration 1200). This previously sent hours, so a 2-hour
        # limit went out as "2".
        duration = str(int(self.sched_duration_h or 0) * 3600)
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

    async def _capture(self, device: dict, raw_device_data) -> None:
        if not self._capture_enabled:
            return
        cfg = self.hass.config.config_dir
        if not self._identity_saved:
            identity = {k: device.get(k) for k in _IDENTITY_KEYS if device.get(k) is not None}
            await self.hass.async_add_executor_job(
                capture.append, cfg, self.mac, "identity", identity, None)
            self._identity_saved = True
        if raw_device_data:
            await self.hass.async_add_executor_job(
                capture.append, cfg, self.mac, "rest", raw_device_data, None)
