# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Development and Testing
- **Install dependencies**: `pip install -r requirements.txt`
- **Test Modbus connection locally**: `python3 test_modbus.py`
- **Run the driver**: `./main.py` or `python3 main.py`
- **Configuration setup**: `cp alfen_driver_config.sample.yaml alfen_driver_config.yaml`

### Python Environment
- **Create virtual environment**: `python3 -m venv .venv`
- **Activate virtual environment**: `source .venv/bin/activate`

### Deployment Commands (Victron GX)
- **Install system dependencies**: `opkg update && opkg install git python3 python3-pip`
- **Make executable**: `chmod +x main.py`
- **Add to startup**: `echo '/data/victron-alfen-charger/main.py &' >> /data/rc.local`
- **View logs**: `tail -f /var/log/alfen_driver.log`

## Architecture

This is a Python-based driver that integrates an Alfen EV charger with Victron's Venus OS via D-Bus. The architecture consists of:

### Core Components
- **main.py**: Entry point that sets up D-Bus main loop and runs AlfenDriver
- **alfen_driver/driver.py**: Main AlfenDriver class handling polling, Modbus communication, and D-Bus publishing
- **alfen_driver/config.py**: Configuration management using dataclasses with YAML loading
- **alfen_driver/controls.py**: Current setting functions with verification and retry logic
- **alfen_driver/logic.py**: Business logic for charging modes (Manual/Auto/Scheduled), SOC handling
- **alfen_driver/dbus_utils.py**: D-Bus service registration and Victron-specific enums
- **alfen_driver/modbus_utils.py**: Modbus communication utilities, register reading, and connection handling

### Data Flow
1. **Modbus Polling**: Reads registers from Alfen charger (voltages, currents, power, status)
2. **Logic Processing**: Computes effective current based on mode, schedules, SOC levels
3. **D-Bus Publishing**: Exposes data to Victron system via standard EV charger paths
4. **Control Loop**: Sets charging current back to charger via Modbus writes

### Configuration System
- **YAML-based**: Primary config in `alfen_driver_config.yaml` (copy from `.sample`)
- **Dataclass structure**: Type-safe configuration with validation
- **Hierarchical**: Modbus settings, registers, defaults, logging, schedules, controls
- **Persistence**: JSON state file for runtime data persistence

### Key Design Patterns
- **Modular**: Clear separation between Modbus, D-Bus, configuration, and business logic
- **Retry logic**: Built-in retries for Modbus operations with configurable tolerances
- **Adaptive polling**: Different intervals for active vs idle states
- **Status mapping**: Translates Alfen status strings to Victron EVC_STATUS enums
- **Watchdog**: Periodic current refreshing to maintain charger state

### Register Layout (Alfen Modbus)
- **Socket slave (ID=1)**: Voltages (306), Currents (320), Power (344), Energy (374)
- **Station slave (ID=200)**: Status (1201), Current config (1210), Phases (1215), Max current (1100)
- **String registers**: Firmware, serial, manufacturer decoded from multiple 16-bit registers

### D-Bus Integration
- **Service name**: `com.victronenergy.evcharger.{device_instance}`
- **Standard paths**: `/Ac/Power`, `/Current`, `/Status`, `/Mode`, etc.
- **Victron compatibility**: Uses vedbus and follows Victron device conventions

### Charging Modes
- **Manual**: Fixed current from config (`intended_set_current`)
- **Auto**: Solar-based charging using available excess power
- **Scheduled**: Time-based charging windows with configurable days/hours

## Development Notes

### Dependencies
- **pymodbus==3.6.4**: Specific version for Modbus TCP communication
- **pyyaml**: YAML configuration parsing
- **pytz**: Timezone handling for schedules
- **System (Venus OS)**: dbus, gi (GLib), vedbus

### Common Patterns
- **Error handling**: Comprehensive exception catching with logging
- **Type hints**: Extensive use of typing for better IDE support
- **Logging**: Configurable levels with file output to `/var/log/alfen_driver.log`
- **State validation**: Current tolerance checks and verification delays

### Configuration Customization
Key settings to adjust in `alfen_driver_config.yaml`:
- **modbus.ip**: Alfen charger IP address (required)
- **defaults.intended_set_current**: Default charging current (6.0A)
- **controls.max_set_current**: Safety limit (64.0A)
- **schedules**: Up to 3 time-based charging windows
- **poll_interval_ms**: Base polling frequency (1000ms)

### Testing and Debugging
- Use `test_modbus.py` for local Modbus verification before deployment
- Enable DEBUG logging level for detailed operation tracing
- Monitor `/var/log/alfen_driver.log` for runtime issues
- Check D-Bus paths with `dbus -y com.victronenergy.system` commands

- Run pre-commit yourself before trying to commit because often it will trim whitespace.
