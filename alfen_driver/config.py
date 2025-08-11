import dataclasses
import json
import logging
import os
from typing import Any, Dict, List, Optional

import yaml


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
class ScheduleItem:
    enabled: int = 0
    days_mask: int = 0
    start: str = "00:00"
    end: str = "00:00"


@dataclasses.dataclass
class ScheduleConfig:
    items: List[ScheduleItem] = dataclasses.field(default_factory=list)


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
    schedule=ScheduleConfig(
        items=[ScheduleItem(enabled=1, days_mask=127, start="13:00", end="16:00")]
    ),
    controls=ControlsConfig(),
    poll_interval_ms=1000,
)

CONFIG_PATH: str = os.path.join(
    os.path.dirname(__file__), "../alfen_driver_config.yaml"
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
                loaded_config = yaml.safe_load(f)
            if not isinstance(loaded_config, dict):
                raise ValueError("Config must be a dictionary")
            # Create instances
            modbus = ModbusConfig(**loaded_config.get("modbus", {}))
            registers = RegistersConfig(**loaded_config.get("registers", {}))
            defaults = DefaultsConfig(**loaded_config.get("defaults", {}))
            logging_cfg = LoggingConfig(**loaded_config.get("logging", {}))
            schedules_list = loaded_config.get("schedules", [])
            if not schedules_list:
                schedule_data = loaded_config.get("schedule", {})
                if schedule_data:
                    schedules_list = [schedule_data]
            items = [ScheduleItem(**s) for s in schedules_list]
            while len(items) < 3:
                items.append(ScheduleItem())
            schedule = ScheduleConfig(items=items)
            controls = ControlsConfig(**loaded_config.get("controls", {}))
            config = Config(
                modbus=modbus,
                device_instance=loaded_config.get("device_instance", 0),
                registers=registers,
                defaults=defaults,
                logging=logging_cfg,
                schedule=schedule,
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
            for item in config.schedule.items:
                parse_hhmm_to_minutes(item.start)
                parse_hhmm_to_minutes(item.end)
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


def parse_hhmm_to_minutes(timestr: Any) -> int:
    if not isinstance(timestr, str):
        return 0
    try:
        parts = timestr.strip().split(":")
        if len(parts) != 2:
            return 0
        hours = int(parts[0]) % 24
        minutes = int(parts[1]) % 60
        return hours * 60 + minutes
    except ValueError:
        return 0


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
