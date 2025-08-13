# Victron–Alfen Charger Integration

Integrate an Alfen Eve (Pro Line and similar NG9xx platform) EV charger with a Victron GX device (Venus OS) using Modbus TCP and D‑Bus. The charger is exposed as a first‑class EV charger in the Victron ecosystem.

## Features

- MANUAL, AUTO (excess‑solar), and SCHEDULED modes
- Optional Tibber dynamic pricing support in SCHEDULED mode (level/threshold/percentile strategies)
- Robust Modbus reads/writes with retries and reconnection
- D‑Bus service: `com.victronenergy.evcharger.alfen_<device_instance>`
- Exposes key paths: `/Mode`, `/StartStop`, `/SetCurrent`, `/MaxCurrent`, `/Ac/Current`, `/Ac/Power`, `/Ac/Energy/Forward`, `/Status`, phase voltages/currents/power
- Session tracking and energy accounting per charging session
- Structured logging to console and `/var/log/alfen_driver.log`

## Requirements

- Python 3.8+
- Access to Alfen charger via Modbus TCP (slave)
- On Victron Venus OS: system libraries are preinstalled/provided (dbus, gi/GLib, vedbus)
- Python packages (installed via pip):
  - `pymodbus==3.6.4`
  - `pyyaml>=6.0.1`
  - `pytz`
  - Optional: `aiohttp>=3.9.1,<4` for faster Tibber API

See `requirements.txt` and `requirements-dev.txt`.

## Quick start (local testing)

1) Clone and enter the repo

```bash
git clone https://github.com/yourusername/victron-alfen-charger.git
cd victron-alfen-charger
```

2) Create a virtualenv and install deps

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3) Configure

```bash
cp alfen_driver_config.sample.yaml alfen_driver_config.yaml
# Edit alfen_driver_config.yaml and set at least:
# modbus.ip: "<your-charger-ip>"
```

4) Run driver (on a system with D‑Bus available)

```bash
python3 main.py
```

5) Modbus-only smoke test (no D‑Bus)

```bash
# Edit ALFEN_IP in test_modbus.py first
python3 test_modbus.py
```

## Running on Victron GX (Venus OS)

1) Enable SSH (Venus OS Settings → Services → SSH) and log in as `root`

2) Install minimal tooling

```bash
opkg update
opkg install git python3 python3-pip
```

3) Fetch code and configure

```bash
cd /data
git clone https://github.com/yourusername/victron-alfen-charger.git
cd victron-alfen-charger
cp alfen_driver_config.sample.yaml alfen_driver_config.yaml
vi alfen_driver_config.yaml   # set modbus.ip and other fields as needed
pip3 install -r requirements.txt
chmod +x main.py
```

4) Test run

```bash
./main.py
```

5) Auto‑start on boot (rc.local)

```bash
echo '/data/victron-alfen-charger/main.py &' >> /data/rc.local
chmod +x /data/rc.local
```

Logs: `/var/log/alfen_driver.log`

## Configuration

- Primary file: `alfen_driver_config.yaml` (copy from the provided sample)
- Validated on startup with clear errors and suggestions
- Key sections:
  - `modbus`: `ip`, optional `port`, `socket_slave_id`, `station_slave_id`
  - `defaults`: `intended_set_current`, `station_max_current`
  - `controls`: verification tolerance, watchdog interval, retries, etc.
  - `schedule`: optional legacy time windows (used when Tibber disabled)
  - `tibber`: optional dynamic pricing integration and strategy
  - `logging`: level, file, rotation
  - `timezone`, `device_instance`, `poll_interval_ms`

Minimal example:

```yaml
modbus:
  ip: "192.168.1.100"

defaults:
  intended_set_current: 16.0
  station_max_current: 32.0

controls:
  max_set_current: 32.0
  current_tolerance: 0.5

timezone: "Europe/Amsterdam"
```

For a detailed, field‑by‑field guide (validation ranges, examples, troubleshooting), see `docs/configuration_guide.md`.

## Architecture overview

```mermaid
graph TD
    A[Alfen Charger] -- Modbus TCP --> B[Driver]
    B -- Polls Metrics --> C[Modbus Utils]
    B -- Processes Logic --> D[Logic & Controls]
    B -- Publishes Data --> E[D-Bus Service]
    E -- Victron UI/System --> F[GX Device]
    G[Config YAML] -- Loads --> B
    H[Persistence JSON] -- Saves/Loads State --> B
```

- Modbus polling: voltages, currents, power, energy, status
- Logic: mode handling (MANUAL/AUTO/SCHEDULED), low SOC checks, excess‑solar calculation, dynamic scheduling (Tibber or legacy windows)
- D‑Bus: exposes EV‑charger paths for the Victron UI and ecosystem

## D‑Bus interface (selected paths)

- `/Mode` (0=MANUAL, 1=AUTO, 2=SCHEDULED)
- `/StartStop` (0=disabled, 1=enabled)
- `/SetCurrent` (A)
- `/MaxCurrent` (A)
- `/Status` (0=Disconnected, 1=Connected, 2=Charging, 7=Low SOC; also WAIT_SUN/WAIT_START via UI state mapping)
- `/Ac/Current`, `/Ac/Power`, `/Ac/Energy/Forward`
- `/Ac/L{1,2,3}/Voltage`, `/Ac/L{1,2,3}/Current`, `/Ac/L{1,2,3}/Power`

Service name: `com.victronenergy.evcharger.alfen_<device_instance>`

## Development

- Make targets:
  - `make install` / `make install-dev`
  - `make test` / `make test-cov`
  - `make lint` / `make format` / `make type-check` / `make security`
  - `make pre-commit` / `make all`
- Tests: `pytest` with coverage (see `tests/` and `pyproject.toml` for settings)
- Style/quality: black, ruff, mypy, bandit, pre-commit hooks

## Troubleshooting

- Modbus connection failures
  - Verify `modbus.ip` (port 502), network reachability, and that Modbus TCP is enabled on the charger
- Register read errors / wrong values
  - Confirm register addresses match your firmware; adjust `registers` in config if needed
- Charger not visible in Victron UI
  - Ensure the process is running, check logs, and verify `device_instance` uniqueness
- Set current not applied
  - Check logs for write/verify warnings; increase retries/tolerances if network is flaky
- Low SOC behavior
  - In AUTO mode, charging pauses when Victron battery SOC < system minimum; adjust the minimum SOC in Victron settings

Logs: `/var/log/alfen_driver.log`. Enable DEBUG via `logging.level: DEBUG`.

## Notes & assumptions

- Designed for Alfen NG9xx platform; 1‑phase vs 3‑phase is auto‑detected from register 1215 (2‑phase treated as 3‑phase)
- Tibber integration is optional and used only when `tibber.enabled: true`
- Venus OS provides system D‑Bus and `vedbus`; these are not pip dependencies

## License

MIT License
