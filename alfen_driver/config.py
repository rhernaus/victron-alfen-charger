import dataclasses
import json
import logging
import os
from typing import Any, Dict, Optional

import dbus


@dataclasses.dataclass
class ModbusConfig:
    ip: str
    port: int
    socket_slave_id: int
    station_slave_id: int


@dataclasses.dataclass
class RegistersConfig:
    voltages: int
    currents: int
    power: int
    energy: int
    status: int
    amps_config: int
    phases: int
    firmware_version: int
    firmware_version_count: int
    station_serial: int
    station_serial_count: int
    manufacturer: int
    manufacturer_count: int
    platform_type: int
    platform_type_count: int
    station_max_current: int


@dataclasses.dataclass
class DefaultsConfig:
    intended_set_current: float
    station_max_current: float


@dataclasses.dataclass
class LoggingConfig:
    level: str
    file: str


@dataclasses.dataclass
class ScheduleConfig:
    enabled: int
    days_mask: int
    start: str
    end: str


@dataclasses.dataclass
class LowSocConfig:
    enabled: int
    threshold: float
    hysteresis: float
    battery_soc_dbus_path: str


@dataclasses.dataclass
class ControlsConfig:
    current_tolerance: float = 0.25
    update_difference_threshold: float = 0.1
    verification_delay: float = 0.1
    retry_delay: float = 0.5
    max_retries: int = 3
    watchdog_interval_seconds: int = 30
    max_set_current: float = 64.0

    def __post_init__(self):
        if self.current_tolerance < 0:
            raise ValueError("current_tolerance must be non-negative")
        if self.max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        # Add similar for others


@dataclasses.dataclass
class Config:
    modbus: ModbusConfig
    device_instance: int
    registers: RegistersConfig
    defaults: DefaultsConfig
    logging: LoggingConfig
    schedule: ScheduleConfig
    low_soc: LowSocConfig
    controls: ControlsConfig
    poll_interval_ms: int

    def __post_init__(self):
        # Basic validation
        if self.modbus.port <= 0:
            raise ValueError("modbus.port must be positive")
        if self.defaults.intended_set_current < 0:
            raise ValueError("defaults.intended_set_current must be non-negative")
        # Add more as needed


DEFAULT_CONFIG = Config(
    modbus=ModbusConfig(
        ip="10.128.0.64", port=502, socket_slave_id=1, station_slave_id=200
    ),
    device_instance=0,
    registers=RegistersConfig(
        voltages=306,
        currents=320,
        power=344,
        energy=374,
        status=1201,
        amps_config=1210,
        phases=1215,
        firmware_version=123,
        firmware_version_count=17,
        station_serial=157,
        station_serial_count=11,
        manufacturer=117,
        manufacturer_count=5,
        platform_type=140,
        platform_type_count=17,
        station_max_current=1100,
    ),
    defaults=DefaultsConfig(intended_set_current=6.0, station_max_current=32.0),
    logging=LoggingConfig(level="INFO", file="/var/log/alfen_driver.log"),
    schedule=ScheduleConfig(enabled=0, days_mask=0, start="00:00", end="00:00"),
    low_soc=LowSocConfig(
        enabled=0,
        threshold=20.0,
        hysteresis=2.0,
        battery_soc_dbus_path="/Dc/Battery/Soc",
    ),
    controls=ControlsConfig(),
    poll_interval_ms=1000,
)

CONFIG_PATH: str = os.path.join(
    os.path.dirname(__file__), "../alfen_driver_config.json"
)


def load_config(logger: logging.Logger) -> Config:
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
            if not isinstance(loaded_config, dict):
                raise ValueError("Config must be a dictionary")
            # Create instances
            modbus = ModbusConfig(**loaded_config.get("modbus", {}))
            registers = RegistersConfig(**loaded_config.get("registers", {}))
            defaults = DefaultsConfig(**loaded_config.get("defaults", {}))
            logging_cfg = LoggingConfig(**loaded_config.get("logging", {}))
            schedule = ScheduleConfig(**loaded_config.get("schedule", {}))
            low_soc = LowSocConfig(**loaded_config.get("low_soc", {}))
            controls = ControlsConfig(**loaded_config.get("controls", {}))
            config = Config(
                modbus=modbus,
                device_instance=loaded_config.get("device_instance", 0),
                registers=registers,
                defaults=defaults,
                logging=logging_cfg,
                schedule=schedule,
                low_soc=low_soc,
                controls=controls,
                poll_interval_ms=loaded_config.get("poll_interval_ms", 1000),
            )
            # Basic validation
            if not isinstance(config, Config):
                raise ValueError("Loaded config is not an instance of Config")
            # Validate specific fields (example)
            if config.modbus.port <= 0:
                raise ValueError("modbus.port must be positive")
            if config.defaults.intended_set_current < 0:
                raise ValueError("defaults.intended_set_current must be non-negative")
            if "schedule" in loaded_config:
                sched = loaded_config["schedule"]
                if "start" in sched:
                    parse_hhmm_to_minutes(sched["start"])  # Will raise if invalid
                if "end" in sched:
                    parse_hhmm_to_minutes(sched["end"])
            # Merge with defaults
            # config = DEFAULT_CONFIG.copy() # This line is removed as we are using objects
            # for key in config: # This line is removed as we are using objects
            #     if key in loaded_config: # This line is removed as we are using objects
            #         if isinstance(config[key], dict) and isinstance( # This line is removed as we are using objects
            #             loaded_config[key], dict # This line is removed as we are using objects
            #         ): # This line is removed as we are using objects
            #             config[key].update(loaded_config[key]) # This line is removed as we are using objects
            #         else: # This line is removed as we are using objects
            #             config[key] = loaded_config[key] # This line is removed as we are using objects
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
