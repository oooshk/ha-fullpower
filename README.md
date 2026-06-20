# Full Power EV Charger — Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant integration for **Full Power** / **Kiso** EV charging piles
(app package `com.kiso.yusing`, devices branded **Fullwatt / FWT**). It talks to
the Kiso cloud the same way the official app does (REST + MQTT), giving you
proper start/stop control, live telemetry, and the charger's settings — no
reliance on the app being open.

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
- **Real-time updates** via the cloud MQTT push, with adaptive REST polling that
  speeds up while charging.
- **Diagnostics** download and a local capture log for development.

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
- Some settings are **rejected while charging**; the integration raises a
  notification explaining when that happens.

## Disclaimer

Independent, unofficial integration built by reverse-engineering the app for
interoperability. Not affiliated with or endorsed by Kiso / Fullwatt. Use at
your own risk.

## License

[MIT](LICENSE)
