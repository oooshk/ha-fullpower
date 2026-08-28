DOMAIN = "fullpower"
MANUFACTURER = "Kiso / Fullwatt"

# ── Cloud endpoints (verified from decompiled BuildConfig) ────────────────────
# service     -> API_URL      (userCenter/* : auth, devices, areas, scenes)
# serviceTest -> FULLWATT_URL (fullwattService/* : charge point + storage battery)
API_URL      = "https://appglobal.kisoiot.com:8443"
FULLWATT_URL = "https://fullwatt.kisoiot.com"

APP_CODE = "92c9c09a-b7b5-4c6c-bbb9-028b761763d9"
LOGIN_VERSION = "v2"
RET_CODE_SUCCESS = "20000"

# ── REST API paths ────────────────────────────────────────────────────────────
# on API_URL
API_LOGIN          = "/userCenter/user/login"
API_REFRESH_TOKEN  = "/userCenter/user/refreshToken"
API_DEVICE_LIST    = "/userCenter/deviceService/list"
API_DEVICE_INFO    = "/userCenter/deviceService/getDeviceInfo"
API_DEVICE_REBOOT  = "/userCenter/deviceService/reboot"
# on FULLWATT_URL
API_CONTROL_ONOFF      = "/fullwattService/chargePoint/controlOnOff"
API_UPDATE_CP_DATA     = "/fullwattService/chargePoint/updateChargePointData"
API_ONOFF_TIMER        = "/fullwattService/chargePointTimer/onOffTimer"
API_WGD_RESERVE        = "/fullwattService/chargePointTimer/wgdReserve"
API_CHARGE_HISTORY     = "/fullwattService/chargePoint/listChargeHistory"

# Paths that must be sent to FULLWATT_URL instead of API_URL
FULLWATT_PATHS = (
    "/fullwattService/", "/storageBattery/",
)

# Config entry keys
CONF_USERNAME    = "username"
CONF_PASSWORD    = "password"
CONF_MAC         = "mac"
CONF_DEVICE_TYPE = "device_type"
CONF_DEVICE_NAME = "device_name"

# controlOnOff values
ONOFF_START = "1"
ONOFF_STOP  = "0"

# updateChargePointData attribute names (verified in ChargePileSetFragment)
ATTR_MAX_CURRENT = "MaxCurrent"   # charging current limit, amps
ATTR_DLB_ENABLE  = "DLBEnable"    # "1" / "0"
ATTR_DLB_MAX_C   = "DLBMaxC"      # house/dynamic-load current limit, amps
ATTR_CHARGE_MODE = "chargeMode"   # "0" swipe card, "1" plug & charge
ATTR_COMPATIBLE  = "Compatible"   # "0" normal, "1" compatibility mode (fussy vehicles)

# The app's 4 modes are a 2x2 of (chargeMode, Compatible) — verified in
# BleControlSetFragment.updateChargingModeText(). Each option sets BOTH attrs.
CHARGE_MODE_OPTIONS = {
    "Swipe Card":               ("0", "0"),
    "Plug & Charge":            ("1", "0"),
    "Compatible Swipe Card":    ("0", "1"),
    "Compatible Plug & Charge": ("1", "1"),
}

# Charge-current dropdown ladders per charger rating (from ChargeCurrentDialogFragment).
CURRENT_LADDERS = {
    16: [16, 12, 10, 8, 6],
    32: [32, 28, 24, 20, 16, 12, 10, 8, 6],
    40: [40, 36, 32, 28, 24, 20, 16, 12, 10, 8, 6],
    48: [48, 44, 40, 36, 32, 28, 24, 20, 16, 12, 10, 8, 6],
}
RATED_CURRENT_TIERS = [16, 32, 40, 48]
DEFAULT_RATED_CURRENT = 32
CONF_RATED_CURRENT = "rated_current"


def tier_for(amps: int) -> int:
    """Nearest model tier at or above the given amps."""
    for tier in RATED_CURRENT_TIERS:
        if amps <= tier:
            return tier
    return RATED_CURRENT_TIERS[-1]


def ladder_for(rating: int) -> list[int]:
    return CURRENT_LADDERS[tier_for(rating)]


# House/DLB limit is a free numeric entry in the app, capped at 100 A.
CURRENT_MIN = 6
CURRENT_MAX = 100

# ── Adaptive polling ──────────────────────────────────────────────────────────
# Fast cadence only while a charge session was started BY this integration
# (so HA can monitor / stop it / take readings). Slow idle check-in otherwise.
CONF_ACTIVE_SECONDS     = "active_seconds"      # fast poll while HA-initiated charging
CONF_IDLE_MINUTES       = "idle_minutes"        # background check-in
CONF_MAX_ACTIVE_MINUTES = "max_active_minutes"  # safety cap on fast polling

DEFAULT_ACTIVE_SECONDS     = 30
DEFAULT_IDLE_MINUTES       = 15
DEFAULT_MAX_ACTIVE_MINUTES = 240

# ── Staleness / offline detection ─────────────────────────────────────────────
# The cloud keeps serving the LAST KNOWN device record after the charge point
# drops its link: get_device_list() still succeeds, so nothing here failed and
# every entity stayed 'available' while reporting a status that was, on
# 2026-08-16..19, up to 60.7 hours old. The payload carries no isOnline flag and
# ChargePointErrorCode stays 'NoError' throughout, so the only evidence of life
# is that the payload's own `timestamp` field keeps moving.
#
# Measured cadence (2609 captured REST payloads, both firmwares): `timestamp`
# advances roughly every 20 minutes even while parked at 'Available', and jumps
# instantly on a real event. So it is a heartbeat, not an event marker — but the
# idle heartbeat can legitimately sit ~20-25 min behind the wall clock, which is
# why the threshold is well above that rather than a few minutes.
CONF_STALE_MINUTES    = "stale_minutes"
DEFAULT_STALE_MINUTES = 90

# Cold-start guard. Change-detection needs two polls to say anything, so a HA
# restart during an outage would otherwise show a stale status as live for a
# full stale_minutes. On the first poll only, judge the timestamp in absolute
# terms — generously, because of the timezone caveat below.
COLD_START_MAX_AGE_H = 6

# The payload timestamp is suffixed 'Z' but is NOT UTC: captured polls show it
# tracking LOCAL time (a poll at 08:36:29 BST carried '...T08:21:27.459Z', i.e.
# 15 min behind local, 45 min AHEAD of the true UTC instant). Never trust that
# suffix. The primary guard therefore never does absolute arithmetic on it — it
# only asks "has this value changed?", measured on our own clock, which is
# immune to the device's timezone, to DST, and to clock skew. The cold-start
# check does compare absolutely, so it takes the most favourable of the two
# possible interpretations and uses a wide threshold.

# -- Capture log --------------------------------------------------------------
# Raw-payload capture is developer instrumentation for local-control work, not a
# user feature: it appends a JSONL record on every poll and is unbounded by
# nature. It is therefore OFF by default and size-capped, so a forgotten toggle
# cannot fill the config partition (a real 573 MB file prompted this).
CONF_CAPTURE      = "capture_enabled"
DEFAULT_CAPTURE   = False
CAPTURE_MAX_BYTES = 32 * 1024 * 1024

# Statuses that count as an active/ongoing session for monitoring purposes.
ACTIVE_MONITOR_STATES = {"Preparing", "Charging", "SuspendedEV", "SuspendedEVSE"}

OCPP_STATES = [
    "Available", "Preparing", "Charging", "SuspendedEV",
    "SuspendedEVSE", "Finishing", "Faulted", "Reserved", "Unavailable",
]

# Service name for delayed/timed charging
SERVICE_SET_DELAYED_CHARGE = "set_delayed_charge"
