"""Live MQTT client for realtime charge-point updates.

Subscribes to the account topic and merges incoming device messages
into the coordinator so sensors update without waiting for the REST poll.
"""
from __future__ import annotations

import json
import logging
import random
import threading

from homeassistant.core import HomeAssistant

from .const import MQTT_HOST, MQTT_PORT

_LOGGER = logging.getLogger(__name__)

# Keys whose presence means the message carries live device state worth merging.
# (Deliberately excludes bare "status", which appears on frequent CmdResult acks
# that no entity consumes — avoids needless entity-update churn.)
_INTERESTING = {
    "ChargePointStatus", "ChargePointErrorCode", "chargingPower",
    "voltage", "chargingCurrent", "V_L1", "V_L2", "V_L3", "A_L1", "A_L2", "A_L3",
    "MaxCurrent", "registerEnergy", "SoC", "temperature",
    "DLBEnable", "DLBMaxC", "chargeMode",
}


def _norm(mac) -> str:
    return str(mac).replace(":", "").lower()


class FullPowerMqtt:
    def __init__(self, hass: HomeAssistant, coordinator, access_token: str) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._token = access_token
        self._mac = coordinator.mac
        self._client = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass

    # ── paho callbacks ────────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe("/#", qos=1)
            _LOGGER.info("FullPower MQTT connected; subscribed to /#")
        else:
            _LOGGER.warning("FullPower MQTT connect failed rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        try:
            text = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            text = ""

        try:
            envelope = json.loads(text)
        except Exception:
            return
        if not isinstance(envelope, dict):
            return

        env_mac = envelope.get("mac")
        if env_mac and _norm(env_mac) != _norm(self._mac):
            return

        # The real payload is an escaped JSON string inside "content".
        inner = envelope
        content = envelope.get("content")
        if isinstance(content, str):
            try:
                inner = json.loads(content)
            except Exception:
                inner = envelope
        if not isinstance(inner, dict):
            return

        in_mac = inner.get("mac")
        if in_mac and _norm(in_mac) != _norm(self._mac):
            return

        # Surface a device-side rejection of a recent setting change.
        if inner.get("ocppHandler") == "CmdResult" and str(inner.get("status")) == "Rejected":
            self._hass.loop.call_soon_threadsafe(self._coordinator.note_command_rejected)

        live = {k: v for k, v in inner.items() if v not in (None, "")}
        if live and (_INTERESTING & live.keys()):
            # async_set_updated_data is loop-only; hop onto the event loop.
            self._hass.loop.call_soon_threadsafe(
                self._coordinator.update_from_mqtt, live)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0 and not self._stop.is_set():
            _LOGGER.debug("FullPower MQTT disconnected rc=%s (paho will retry)", rc)

    def _run(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            _LOGGER.warning("paho-mqtt not available; live MQTT disabled")
            return

        client_id = "androidId" + "".join(str(random.randint(0, 9)) for _ in range(10))
        client = mqtt.Client(client_id=client_id, clean_session=True)
        client.username_pw_set(username=self._token, password="")
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        client.reconnect_delay_set(min_delay=2, max_delay=60)
        self._client = client

        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=20)
            client.loop_start()
            self._stop.wait()
            client.loop_stop()
        except Exception as err:
            _LOGGER.warning("FullPower MQTT error: %s", err)
