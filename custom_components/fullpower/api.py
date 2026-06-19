"""
REST API client for Full Power / Kiso cloud. Verified against decompiled app.

Two hosts (from BuildConfig + RetrofitUtil):
  * userCenter/*       -> API_URL      (appglobal.kisoiot.com:8443)
  * fullwattService/*  -> FULLWATT_URL (fullwatt.kisoiot.com)   <-- charge control

All endpoints: POST application/x-www-form-urlencoded, accessToken as a form
field, throwaway msgId, success retCode == "20000".

Login password (YuSingSdk.login -> MD5Utils -> BcryptUtils):
    bcrypt($2a$12$, base64( md5_hex(MD5_UPPER(pwd))[8:24] ))
"""
from __future__ import annotations

import base64
import hashlib
import logging
import random
import time

import aiohttp

from .const import (
    API_URL, FULLWATT_URL, FULLWATT_PATHS, APP_CODE, LOGIN_VERSION, RET_CODE_SUCCESS,
    API_LOGIN, API_REFRESH_TOKEN, API_DEVICE_LIST, API_DEVICE_INFO,
    API_DEVICE_REBOOT, API_CONTROL_ONOFF, API_UPDATE_CP_DATA, API_ONOFF_TIMER,
    API_WGD_RESERVE, API_CHARGE_HISTORY,
)

_LOGGER = logging.getLogger(__name__)

# Verified live: 20104 = bad credentials, 20102 = token invalid/"please re-login".
AUTH_RET_CODES = {"20102", "20103", "20104", "20105", "20106", "20107", "401", "403"}

# The app sets no custom User-Agent, so OkHttp sends its default "okhttp/x.y.z".
# We match it so our requests blend in with ordinary Android traffic rather than
# announcing themselves as "Python/aiohttp". (See the privacy/fingerprint notes.)
DEFAULT_HEADERS = {"User-Agent": "okhttp/4.12.0"}


class FullPowerAuthError(Exception):
    pass


class FullPowerApiError(Exception):
    pass


def _md5_32(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest().upper()


def _md5_16(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[8:24]


def _encode_password(password: str) -> str:
    a = _md5_32(password)
    b = _md5_16(a)
    c = base64.b64encode(b.encode()).decode()
    import bcrypt  # bundled with Home Assistant core
    return bcrypt.hashpw(c.encode(), bcrypt.gensalt(12, prefix=b"2a")).decode()


def _msg_id() -> str:
    return f"{int(time.time() * 1000)}{random.randint(0, 9999)}"


def _base_url_for(path: str) -> str:
    if any(p in path for p in FULLWATT_PATHS):
        return FULLWATT_URL
    return API_URL


class FullPowerApi:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._access_token: str | None = None
        self._refresh_token: str | None = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def login(self, username: str, password: str) -> dict:
        body = {
            "loginName": username,
            "password":  _encode_password(password),
            "appCode":   APP_CODE,
            "msgId":     _msg_id(),
            "version":   LOGIN_VERSION,
        }
        data = await self._post(API_LOGIN, body, auth_required=False)
        self._access_token  = data.get("accessToken")
        self._refresh_token = data.get("refreshToken")
        if not self._access_token:
            raise FullPowerAuthError(f"Login ok but no accessToken: {data}")
        return data

    async def refresh(self) -> None:
        if not (self._access_token and self._refresh_token):
            raise FullPowerAuthError("No tokens to refresh")
        body = {
            "accessToken":  self._access_token,
            "refreshToken": self._refresh_token,
            "msgId":        _msg_id(),
        }
        data = await self._post(API_REFRESH_TOKEN, body, auth_required=False)
        if data.get("accessToken"):
            self._access_token  = data["accessToken"]
            self._refresh_token = data.get("refreshToken", self._refresh_token)

    # ── Devices ───────────────────────────────────────────────────────────────

    async def get_device_list(self) -> list[dict]:
        data = await self._post(API_DEVICE_LIST, {})
        return data.get("devices") or []

    async def get_device_info(self, mac: str, device_type: str) -> dict:
        return await self._post(API_DEVICE_INFO, {"mac": mac, "deviceType": device_type})

    async def reboot(self, mac: str, device_type: str) -> dict:
        """deviceService/reboot — soft reboot (not a factory reset)."""
        return await self._post(API_DEVICE_REBOOT, {"mac": mac, "deviceType": device_type})

    # ── Control ───────────────────────────────────────────────────────────────

    async def set_charging(self, mac: str, device_type: str, on_off: str) -> dict:
        """controlOnOff — '1' start, '0' stop."""
        return await self._post(
            API_CONTROL_ONOFF,
            {"mac": mac, "deviceType": device_type, "onOff": on_off},
        )

    async def update_charge_point_data(
        self, mac: str, device_type: str, attr_name: str, attr_value: str
    ) -> dict:
        """Generic setter: MaxCurrent / DLBEnable / DLBMaxC / chargeMode / ..."""
        return await self._post(
            API_UPDATE_CP_DATA,
            {"mac": mac, "deviceType": device_type,
             "attrName": attr_name, "attrValue": str(attr_value)},
        )

    async def set_delayed_charge(
        self, mac: str, device_type: str, time_str: str,
        on_off: str, charge_duration: str, countdown: str,
    ) -> dict:
        """onOffTimer — delayed start (time/countdown) + duration (minutes)."""
        return await self._post(
            API_ONOFF_TIMER,
            {"mac": mac, "deviceType": device_type, "time": time_str,
             "onOff": on_off, "chargeDuration": charge_duration, "countdown": countdown},
        )

    async def wgd_reserve(
        self, mac: str, device_type: str, on_off: str, expiry_date: str
    ) -> dict:
        return await self._post(
            API_WGD_RESERVE,
            {"mac": mac, "deviceType": device_type, "onOff": on_off, "expiryDate": expiry_date},
        )

    async def get_charge_history(self, mac: str, device_type: str, time_filter: str = "") -> dict:
        return await self._post(
            API_CHARGE_HISTORY,
            {"mac": mac, "deviceType": device_type, "time": time_filter},
        )

    # ── HTTP core ─────────────────────────────────────────────────────────────

    async def _post(self, path: str, body: dict, auth_required: bool = True) -> dict:
        form = dict(body)
        form.setdefault("msgId", _msg_id())
        if auth_required:
            if not self._access_token:
                raise FullPowerAuthError("Not logged in")
            form["accessToken"] = self._access_token

        url = _base_url_for(path) + path
        try:
            async with self._session.post(
                url, data=form, headers=DEFAULT_HEADERS, ssl=False
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except aiohttp.ClientResponseError as err:
            raise FullPowerApiError(f"HTTP {err.status} on {path}") from err
        except Exception as err:
            raise FullPowerApiError(f"POST {path} failed: {err}") from err

        ret = str(payload.get("retCode")) if isinstance(payload, dict) else None
        if ret != RET_CODE_SUCCESS:
            msg = payload.get("retMsg") if isinstance(payload, dict) else payload
            if not auth_required or ret in AUTH_RET_CODES:
                raise FullPowerAuthError(f"{path} retCode={ret}: {msg}")
            raise FullPowerApiError(f"{path} retCode={ret}: {msg}")
        return payload

    @property
    def access_token(self) -> str | None:
        return self._access_token
