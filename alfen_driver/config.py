import json
import logging
import os
from typing import Any, Dict, Optional

import dbus

DEFAULT_CONFIG: Dict[str, Any] = {
    "modbus": {
        "ip": "10.128.0.64",
        "port": 502,
        "socket_slave_id": 1,
        "station_slave_id": 200,
    },
    "device_instance": 0,
    "registers": {
        "voltages": 306,
        "currents": 320,
        "power": 344,
        "energy": 374,
        "status": 1201,
        "amps_config": 1210,
        "phases": 1215,
        "firmware_version": 123,
        "firmware_version_count": 17,
        "station_serial": 157,
        "station_serial_count": 11,
        "manufacturer": 117,
        "manufacturer_count": 5,
        "platform_type": 140,
        "platform_type_count": 17,
        "station_max_current": 1100,
    },
    "defaults": {"intended_set_current": 6.0, "station_max_current": 32.0},
    "logging": {"level": "INFO", "file": "/var/log/alfen_driver.log"},
    "schedule": {"enabled": 0, "days_mask": 0, "start": "00:00", "end": "00:00"},
    "low_soc": {
        "enabled": 0,
        "threshold": 20.0,
        "hysteresis": 2.0,
        "battery_soc_dbus_path": "/Dc/Battery/Soc",
    },
    "poll_interval_ms": 1000,
}

CONFIG_PATH: str = os.path.join(
    os.path.dirname(__file__), "../alfen_driver_config.json"
)


def load_config(logger: logging.Logger) -> Dict[str, Any]:
    """
    Load configuration from JSON file, falling back to defaults.

    Returns:
        The loaded or default configuration dictionary.

    Validates key fields and merges with defaults.
    """
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded_config = json.load(f)
            # Basic validation
            if not isinstance(loaded_config, dict):
                raise ValueError("Config must be a dictionary")
            # Validate specific fields (example)
            if "modbus" in loaded_config:
                modbus = loaded_config["modbus"]
                if "ip" in modbus and not isinstance(modbus["ip"], str):
                    raise ValueError("modbus.ip must be a string")
                if "port" in modbus and not isinstance(modbus["port"], int):
                    raise ValueError("modbus.port must be an integer")
            if "schedule" in loaded_config:
                sched = loaded_config["schedule"]
                if "start" in sched:
                    parse_hhmm_to_minutes(sched["start"])  # Will raise if invalid
                if "end" in sched:
                    parse_hhmm_to_minutes(sched["end"])
            # Merge with defaults
            config = DEFAULT_CONFIG.copy()
            for key in config:
                if key in loaded_config:
                    if isinstance(config[key], dict) and isinstance(
                        loaded_config[key], dict
                    ):
                        config[key].update(loaded_config[key])
                    else:
                        config[key] = loaded_config[key]
            logger.info(f"Loaded and validated config from {CONFIG_PATH}")
            return config
        except (ValueError, KeyError) as e:
            logger.warning(f"Invalid config in {CONFIG_PATH}: {e}. Using defaults.")
        except Exception as e:
            logger.warning(
                f"Failed to load config from {CONFIG_PATH}: {e}. Using defaults."
            )
    else:
        logger.info(f"Config file {CONFIG_PATH} not found. Using defaults.")
    return DEFAULT_CONFIG


def parse_hhmm_to_minutes(timestr: str) -> int:
    """
    Parse HH:MM string to minutes since midnight.

    Parameters:
        timestr: The time string in HH:MM format.

    Returns:
        Minutes since midnight, or 0 if invalid.

    Raises:
        ValueError: If parsing fails (caught and returns 0).
    """
    try:
        parts = timestr.strip().split(":")
        if len(parts) != 2:
            return 0
        hours = int(parts[0]) % 24
        minutes = int(parts[1]) % 60
        return hours * 60 + minutes
    except ValueError:
        return 0


def load_initial_config(
    service_name: str,
    current_mode: Any,
    start_stop: Any,
    auto_start: int,
    intended_set_current: float,
    logger: logging.Logger,
    config_file_path: str,
) -> None:
    """
    Load initial configuration from D-Bus or disk.

    Checks for existing D-Bus service and loads values if found,
    otherwise loads from persisted JSON file.
    """
    existing_service_found = False
    if dbus is not None:
        try:
            bus = dbus.SystemBus()
            dbus_proxy = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
            dbus_iface = dbus.Interface(dbus_proxy, "org.freedesktop.DBus")
            if dbus_iface.NameHasOwner(service_name):
                logger.info(
                    f"Existing D-Bus service {service_name} found. Loading initial values from it."
                )
                existing_service_found = True
                load_from_dbus(
                    bus,
                    service_name,
                    current_mode,
                    start_stop,
                    auto_start,
                    intended_set_current,
                    logger,
                )
        except dbus.DBusException:
            logger.warning(
                "Failed to inspect existing D-Bus service state; using defaults."
            )

    if not existing_service_found:
        data = load_config_from_disk(config_file_path, logger)
        if data is not None:
            apply_config(
                data, current_mode, start_stop, auto_start, intended_set_current
            )


def load_from_dbus(
    bus: Any,
    service_name: str,
    current_mode: Any,
    start_stop: Any,
    auto_start: int,
    intended_set_current: float,
    logger: logging.Logger,
) -> None:
    """
    Load configuration values from an existing D-Bus service.

    Parameters:
        bus: The D-Bus system bus object.

    Loads values like mode, start/stop, auto-start, and set current.
    """
    paths = {
        "/Mode": lambda v: setattr(current_mode, "value", int(v)),
        "/StartStop": lambda v: setattr(start_stop, "value", int(v)),
        "/AutoStart": lambda v: setattr(auto_start, "value", int(v)),
        "/SetCurrent": lambda v: setattr(intended_set_current, "value", float(v)),
    }
    for path, setter in paths.items():
        v = get_busitem_value(bus, service_name, path)
        if v is not None:
            try:
                setter(v)
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse existing {path}: {v!r} ({e})")


def get_busitem_value(bus: Any, service_name: str, path: str) -> Optional[Any]:
    """
    Retrieve a value from a D-Bus path.

    Parameters:
        bus: The D-Bus system bus object.
        path: The D-Bus path to query.

    Returns:
        The value from the D-Bus item, or None if not found or error.

    Raises:
        dbus.DBusException: If D-Bus query fails (caught externally).
    """
    try:
        obj = bus.get_object(service_name, path)
        return obj.GetValue(dbus_interface="com.victronenergy.BusItem")
    except Exception:
        return None


def persist_config_to_disk(
    config_file_path: str,
    current_mode: Any,
    start_stop: Any,
    auto_start: int,
    intended_set_current: float,
    logger: logging.Logger,
) -> None:
    """
    Persist current configuration to disk as JSON.

    Saves key states like mode, start/stop, auto-start, and set current.
    """
    try:
        cfg: Dict[str, Any] = {
            "Mode": int(current_mode),
            "StartStop": int(start_stop),
            "AutoStart": int(auto_start),
            "SetCurrent": float(intended_set_current),
        }
        os.makedirs(os.path.dirname(config_file_path), exist_ok=True)
        with open(config_file_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to persist config: {e}")


def load_config_from_disk(
    config_file_path: str, logger: logging.Logger
) -> Optional[Dict[str, Any]]:
    """
    Load persisted configuration from disk.

    Returns:
        The loaded config dictionary, or None if file not found or invalid.

    Raises:
        OSError, json.JSONDecodeError: Handled and logged.
    """
    try:
        if not os.path.exists(config_file_path):
            return None
        with open(config_file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to load persisted config: {e}")
        return None


def apply_config(
    data: Dict[str, Any],
    current_mode: Any,
    start_stop: Any,
    auto_start: int,
    intended_set_current: float,
) -> None:
    """
    Apply loaded configuration data to instance variables.

    Parameters:
        data: The configuration dictionary to apply.

    Handles type conversions and defaults on errors.
    """
    try:
        current_mode = int(data.get("Mode", int(current_mode)))
    except ValueError:
        pass
    try:
        start_stop = int(data.get("StartStop", int(start_stop)))
    except ValueError:
        pass
    auto_start = int(data.get("AutoStart", auto_start))
    intended_set_current = float(data.get("SetCurrent", intended_set_current))
