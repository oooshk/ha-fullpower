# Full Power EV Charger — Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant integration for **Full Power** / **Kiso** EV charging piles
(app package `com.kiso.yusing`, devices branded **Fullwatt / FWT**). It talks to
the Kiso cloud over the same REST API the official app uses, giving you proper
start/stop control, live telemetry, and the charger's settings — no reliance on
the app being open.

> ⚠️ **Cloud integration.** It connects to Kiso's servers
> (`appglobal.kisoiot.com`, `fullwatt.kisoiot.com`) and therefore needs
> internet. Your app credentials are sent to those servers exactly as the
> official app does.

## Features

- **Start / stop charging** (`switch`) — proper OCPP control, not a power cut.
- **Charging current limit** (`select`) — the charger's amp ladder (model-aware).
- **Charge mode** (`select`) — Swipe Card / Plug & Charge / Compatible variants.
- **Dynamic Load Balancing** enable (`switch`) + house current limit (`number`).
- **Scheduled charge** — start time (`time`), duration (`number`), enable (`switch`).
- **Reboot** the charger (`button`).
- **Live sensors** — status (OCPP), power, per-phase voltage & current, session
  energy, temperature, state of charge*, charge-point error.
- **Adaptive polling** — a slow background check-in that speeds up to a short
  interval while a charge session is running.
- **Offline detection** — the cloud keeps serving the last known record after the
  charge point drops its link, so the integration watches the payload's own
  heartbeat and marks entities unavailable instead of reporting stale data as
  live.
- **Diagnostics** download, plus an optional raw-payload capture log (off by
  default, see below).

\* State of charge is only reported by some hardware; AC chargers usually can't
read the car's battery level.

## Installation (HACS)

1. HACS → **⋮** → **Custom repositories**.
2. Add `https://github.com/oooshk/ha-fullpower` with category
   **Integration**.
3. Install **Full Power EV Charger**, then **restart** Home Assistant.
4. **Settings → Devices & Services → Add Integration → Full Power EV Charger**.
5. Enter your Full Power app **email** and **password**, and pick your charger.

### Manual installation
Copy `custom_components/fullpower` into your HA `config/custom_components/`
directory and restart.

## Notes & limitations

- The charger firmware **rejects charge-current changes while a car is plugged
  in** — set the amp limit while the connector is `Available` (unplugged).
- Some settings are **rejected while charging**. The charger accepts the request
  and silently declines it, so check the entity afterwards to confirm the change
  took effect.

## Transport security

All cloud calls go over HTTPS with certificate verification enabled.

This integration deliberately does **not** connect to the vendor's MQTT broker.
That broker is reachable only as plaintext TCP on port 3010 — it offers no TLS
listener, no MQTT-over-WebSocket endpoint, and the app performs no certificate
pinning — so using it would mean putting the account's access token on the wire
in the clear. Live push was dropped in favour of polling rather than do that.

## Capture log

`Settings → Devices & Services → Full Power → Configure` has a **Capture raw
cloud payloads** toggle, off by default. When enabled the integration appends
every REST payload to `<config>/fullpower_capture/<mac>.jsonl` for
protocol/local-control work. It is developer instrumentation: it grows on every
poll, lives inside the config directory (so it lands in backups), and is capped
at 32 MB. Leave it off unless you are actively collecting.

## Disclaimer

Independent, unofficial integration built by reverse-engineering the app for
interoperability. Not affiliated with or endorsed by Kiso / Fullwatt. Use at
your own risk.

## License

[MIT](LICENSE)
