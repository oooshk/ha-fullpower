# Local control — what we know and how to get there

This charger is a **cloud appliance**: an nmap scan showed *all* TCP ports closed,
so there is **no local API**. It connects *outbound* to Kiso's cloud and is
commanded from there. "Local control with no internet" therefore means **becoming
its cloud**: stand up a local MQTT broker, redirect the cloud domain to it, and
replay the commands the cloud would send.

Everything below was recovered from the decompiled app (jadx) and validated.

## Transport facts (verified)

| Thing | Value |
|---|---|
| MQTT broker | `tcp://appglobal.kisoiot.com:3010` (plain TCP, **no TLS**) |
| MQTT username | the REST **accessToken** (`MQTTService.init`) |
| MQTT password | empty string |
| MQTT clientId | `androidId` + 10 random digits |
| Subscribe topic | `/#` (broker scopes by the authenticated account) |
| Publish (commands) | `MQTTService.publish(topic, payload)` |
| Provisioning | BLE BluFi (`0000f536-…`) or SoftAP HTTP POST to `192.168.4.1` |
| Telemetry model | OCPP — `DeviceDataPush` → `ChargingPileDeviceInfoMqttBean` |

Because the MQTT link is **plain TCP with no cert pinning**, a DNS redirect of
`appglobal.kisoiot.com` → your broker is sufficient to intercept everything.

## What the capture log collects

This integration writes `<config>/fullpower_capture/<mac>.jsonl` with:
- `identity` — mac, deviceType, modelCode, fwVersion, serialNo (to match the device locally)
- `rest` — raw `deviceData` snapshots every poll (the full OCPP telemetry schema)
- `mqtt` — every raw topic + payload the cloud sends (the command/topic structure)

Let it run for a while, and especially **toggle each control** (start/stop, set
current, change mode) so the capture records the resulting MQTT messages. That
correlation — "I called controlOnOff(1)" ↔ "broker delivered X on topic Y" — is
the exact mapping a local broker must reproduce.

## Path to local control (later)

1. Run a local MQTT broker (Mosquitto) on your LAN.
2. Redirect `appglobal.kisoiot.com` → broker IP (router DNS, or per-device).
3. Watch the device's MQTT **CONNECT** (username/clientId) and **SUBSCRIBE** topics.
4. Replay the captured command payloads to those topics to drive the charger.
5. Optionally bridge that local broker into HA's MQTT integration.

## Device command set (ChargePileTag — for local BLE/serial control)

The firmware's full command map (name, code) is in the decompiled
`ChargePileTag.java`. These codes are the command byte in the `55AA` BLE frames.
Notable ones:

| Command | Code | Notes |
|---|---|---|
| MAX_CURRENT | `12` | charge current limit |
| COMPATIBLE_MODE | `14` | compatibility mode |
| CHARGE_CONTROL | `1B` | start/stop charging |
| CHARGE_TYPE | `1C` | swipe vs plug |
| RESERVE_TIME | `1D` | scheduled charge |
| CP_STATUS | `1F` | pilot/connector status |
| REBOOT | `FD` | soft reboot |
| RECOVER_FACTORY | `F3` | factory reset |
| GET_CONFIG | `FF` | read all config |
| SERVICE_HOST/PATH | `0B`/`0C` | **the cloud server the device connects to** — settable, so local redirect may be possible without DNS spoofing |

There is **no screen/display/backlight command** in the set — the screen cannot
be controlled by software (cloud or local).

## Still to capture to finish local control

- The device's own MQTT CONNECT credentials (it authenticates separately from the app).
- The exact **command topic** the cloud publishes to for start/stop/current/mode
  (will appear in the `mqtt` capture once you toggle those controls).
- Whether any firmware build upgrades the broker to TLS (none seen in this APK).
