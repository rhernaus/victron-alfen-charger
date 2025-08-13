"""Configuration management for the Alfen EV Charger Driver.

This module defines the configuration structure and loading mechanisms for the
Alfen driver. It uses dataclasses to provide type-safe configuration with
automatic validation and supports both YAML file loading and runtime defaults.

The configuration is hierarchical and covers all aspects of the driver:
    - Modbus TCP connection parameters
    - Register address mappings for the Alfen charger
    - Default charging parameters and limits
    - Structured logging configuration
    - Charging schedules and time-based control
    - Control parameters for current setting and verification
    - Polling intervals and operational parameters

Example:
    ```python
    from alfen_driver.config import load_config

    # Load configuration with validation
    config = load_config("alfen_driver_config.yaml")

    # Access configuration values
    print(f"Connecting to {config.modbus.ip}:{config.modbus.port}")
    print(f"Default current: {config.defaults.intended_set_current}A")
    ```

The configuration system supports:
    - YAML file loading with comprehensive validation
    - Detailed error messages with correction suggestions
    - Type checking and range validation
    - Relationship validation between settings
    - Environment-specific overrides
"""

import dataclasses
import json
import logging
import os
from typing import Any, Dict, List, Optional

import yaml

from .exceptions import ConfigurationError, ValidationError

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ModbusConfig:
    """Configuration for Modbus TCP connection parameters.

    This class defines the network connection settings for communicating with
    the Alfen EV charger via Modbus TCP protocol. The Alfen charger typically
    uses two different slave IDs for different types of data access.

    Attributes:
        ip: IP address of the Alfen charger (e.g., "192.168.1.100").
        port: TCP port for Modbus communication (typically 502).
        socket_slave_id: Slave ID for real-time socket data (typically 1).
            Used for reading voltages, currents, power, and energy.
        station_slave_id: Slave ID for station configuration (typically 200).
            Used for control operations like setting current and phases.

    Example:
        ```python
        modbus_config = ModbusConfig(
            ip="192.168.1.100",
            port=502,
            socket_slave_id=1,
            station_slave_id=200
        )
        ```
    """

    ip: str
    port: int = 502
    socket_slave_id: int = 1
    station_slave_id: int = 200


@dataclasses.dataclass
class RegistersConfig:
    """Modbus register address mappings for the Alfen charger.

    This class defines the register addresses used to read various data points
    from the Alfen EV charger. These addresses are specific to the Alfen
    charger's Modbus implementation and may vary between firmware versions.

    Attributes:
        voltages: Starting address for voltage readings (L1, L2, L3).
        currents: Starting address for current readings (L1, L2, L3).
        power: Starting address for power readings (active, reactive).
        energy: Starting address for energy counter (64-bit float).
        status: Address for charging status string.
        amps_config: Address for setting charging current.
        phases: Address for reading/setting number of phases.
        firmware_version: Starting address for firmware version string.
        firmware_version_count: Number of registers for firmware version.
        station_serial: Starting address for station serial number.
        station_serial_count: Number of registers for serial number.
        manufacturer: Starting address for manufacturer string.
        manufacturer_count: Number of registers for manufacturer.
        platform_type: Starting address for platform type string.
        platform_type_count: Number of registers for platform type.
        station_max_current: Address for maximum current capability.

    Note:
        These register addresses are based on Alfen's Modbus documentation
        and may need adjustment for different charger models or firmware versions.
    """

    voltages: int = 306
    currents: int = 320
    power: int = 344
    energy: int = 374
    status: int = 1201
    amps_config: int = 1210
    phases: int = 1215
    firmware_version: int = 123
    firmware_version_count: int = 17
    station_serial: int = 157
    station_serial_count: int = 11
    manufacturer: int = 117
    manufacturer_count: int = 5
    platform_type: int = 140
    platform_type_count: int = 17
    station_max_current: int = 1100
    station_status: int = 1201  # Added for completeness


@dataclasses.dataclass
class DefaultsConfig:
    """Default operational values for the charger.

    Attributes:
        intended_set_current: Default charging current in amperes.
        station_max_current: Maximum current the station can provide.
    """

    intended_set_current: float = 6.0
    station_max_current: float = 32.0


@dataclasses.dataclass
class LoggingConfig:
    """Configuration for logging behavior.

    Attributes:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        file: Path to log file.
        format: Log format style ("structured" or "simple").
        max_file_size_mb: Maximum log file size before rotation.
        backup_count: Number of rotated log files to keep.
        console_output: Whether to also log to console.
        json_format: Whether to use JSON formatting.
    """

    level: str = "INFO"
    file: str = "/var/log/alfen_driver.log"
    format: str = "structured"  # "structured" or "simple"
    max_file_size_mb: int = 10
    backup_count: int = 5
    console_output: bool = True
    json_format: bool = False

    def __post_init__(self) -> None:
        """Validate logging configuration."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.level.upper() not in valid_levels:
            raise ValidationError(
                "logging.level", self.level, f"must be one of {valid_levels}"
            )

        if self.max_file_size_mb <= 0:
            raise ValidationError(
                "logging.max_file_size_mb", self.max_file_size_mb, "must be positive"
            )

        if self.backup_count < 0:
            raise ValidationError(
                "logging.backup_count", self.backup_count, "must be non-negative"
            )


@dataclasses.dataclass
class ScheduleItem:
    """Individual schedule configuration.

    Attributes:
        active: Whether this schedule is active.
        days: List of days (0=Mon, 6=Sun).
        start_time: Start time in HH:MM format.
        end_time: End time in HH:MM format.
        enabled: Legacy field for compatibility.
        days_mask: Legacy field for compatibility.
        start: Legacy field for compatibility.
        end: Legacy field for compatibility.
    """

    active: bool = False
    days: List[int] = dataclasses.field(default_factory=list)
    start_time: str = "00:00"
    end_time: str = "00:00"
    # Legacy fields for compatibility
    enabled: int = 0
    days_mask: int = 0
    start: str = "00:00"
    end: str = "00:00"

    def __post_init__(self) -> None:
        """Handle legacy field mapping."""
        # Map legacy fields to new fields if new fields not set
        if not self.active and self.enabled:
            self.active = bool(self.enabled)
        if not self.days and self.days_mask:
            # Convert days_mask to list of days
            self.days = [i for i in range(7) if self.days_mask & (1 << i)]
        if self.start_time == "00:00" and self.start != "00:00":
            self.start_time = self.start
        if self.end_time == "00:00" and self.end != "00:00":
            self.end_time = self.end


@dataclasses.dataclass
class TibberConfig:
    """Tibber API configuration for dynamic pricing.

    Attributes:
        access_token: Tibber API access token.
        enabled: Whether Tibber integration is enabled.
        home_id: Optional specific home ID (if multiple homes).
        charge_on_cheap: Charge when price level is CHEAP.
        charge_on_very_cheap: Charge when price level is VERY_CHEAP.
    """

    access_token: str = ""
    enabled: bool = False
    home_id: str = ""
    charge_on_cheap: bool = True
    charge_on_very_cheap: bool = True


@dataclasses.dataclass
class ScheduleConfig:
    """Schedule configuration container.

    Attributes:
        items: List of schedule items.
    """

    items: List[ScheduleItem] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ControlsConfig:
    """Control and safety limit configuration.

    Attributes:
        current_tolerance: Tolerance for current verification in amperes.
        update_difference_threshold: Minimum difference to trigger update.
        verification_delay: Delay before verifying settings.
        retry_delay: Delay between retry attempts.
        max_retries: Maximum number of retry attempts.
        watchdog_interval_seconds: Watchdog timer interval.
        max_set_current: Maximum settable current in amperes.
        min_charge_duration_seconds: Minimum charging session duration.
        current_update_interval: Interval for refreshing current settings.
        verify_delay: Verification delay in milliseconds.
    """

    current_tolerance: float = 0.5
    update_difference_threshold: float = 0.1
    verification_delay: float = 0.1
    retry_delay: float = 0.5
    max_retries: int = 3
    watchdog_interval_seconds: int = 30
    max_set_current: float = 64.0
    min_charge_duration_seconds: int = 300
    current_update_interval: int = 30000
    verify_delay: int = 100

    def __post_init__(self) -> None:
        """Validate control configuration."""
        if self.current_tolerance < 0:
            raise ValidationError(
                "current_tolerance", self.current_tolerance, "must be non-negative"
            )
        if self.max_retries < 1:
            raise ValidationError("max_retries", self.max_retries, "must be at least 1")
        if self.watchdog_interval_seconds <= 0:
            raise ValidationError(
                "watchdog_interval_seconds",
                self.watchdog_interval_seconds,
                "must be positive",
            )
        if self.max_set_current <= 0:
            raise ValidationError(
                "max_set_current", self.max_set_current, "must be positive"
            )


@dataclasses.dataclass
class Config:
    """Main configuration container.

    Attributes:
        modbus: Modbus connection configuration.
        device_instance: Venus OS device instance number.
        registers: Register address mappings.
        defaults: Default operational values.
        logging: Logging configuration.
        schedule: Schedule configuration.
        tibber: Tibber API configuration for dynamic pricing.
        controls: Control and safety limits.
        poll_interval_ms: Polling interval in milliseconds.
        timezone: Timezone for schedule operations.
    """

    modbus: ModbusConfig
    device_instance: int = 0
    registers: RegistersConfig = dataclasses.field(default_factory=RegistersConfig)
    defaults: DefaultsConfig = dataclasses.field(default_factory=DefaultsConfig)
    logging: LoggingConfig = dataclasses.field(default_factory=LoggingConfig)
    schedule: ScheduleConfig = dataclasses.field(default_factory=ScheduleConfig)
    tibber: TibberConfig = dataclasses.field(default_factory=TibberConfig)
    controls: ControlsConfig = dataclasses.field(default_factory=ControlsConfig)
    poll_interval_ms: int = 1000
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        """Perform basic validation."""
        if self.modbus.port <= 0:
            raise ValidationError("modbus.port", self.modbus.port, "must be positive")
        if self.defaults.intended_set_current < 0:
            raise ValidationError(
                "defaults.intended_set_current",
                self.defaults.intended_set_current,
                "must be non-negative",
            )
        if self.poll_interval_ms <= 0:
            raise ValidationError(
                "poll_interval_ms", self.poll_interval_ms, "must be positive"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        """Create Config from dictionary with validation.

        This method creates a Config object from a dictionary, typically
        loaded from a YAML file. It handles missing sections gracefully
        by using sensible defaults.

        Args:
            data: Configuration dictionary.

        Returns:
            Config object with validated settings.

        Raises:
            ConfigurationError: If required fields are missing or invalid.
        """
        # Validate required fields
        if "modbus" not in data:
            raise ConfigurationError(
                "Missing required 'modbus' section in configuration.\n"
                "Add a 'modbus' section with at least an 'ip' field."
            )

        if "ip" not in data["modbus"]:
            raise ConfigurationError(
                "Missing required 'modbus.ip' field in configuration.\n"
                "Add the IP address of your Alfen charger (e.g., 'ip: 192.168.1.100')."
            )

        # Extract nested configurations
        modbus_data = data.get("modbus", {})
        modbus_config = ModbusConfig(
            ip=modbus_data.get("ip"),
            port=modbus_data.get("port", 502),
            socket_slave_id=modbus_data.get("socket_slave_id", 1),
            station_slave_id=modbus_data.get("station_slave_id", 200),
        )

        # Create other configs with defaults
        registers = RegistersConfig(**data.get("registers", {}))
        defaults = DefaultsConfig(**data.get("defaults", {}))
        logging_cfg = LoggingConfig(**data.get("logging", {}))
        controls = ControlsConfig(**data.get("controls", {}))
        tibber = TibberConfig(**data.get("tibber", {}))

        # Handle schedule configuration
        schedule_data = data.get("schedule", {})
        if "items" in schedule_data:
            items = [ScheduleItem(**item) for item in schedule_data["items"]]
        else:
            items = []
        schedule = ScheduleConfig(items=items)

        # Create main config
        return cls(
            modbus=modbus_config,
            device_instance=data.get("device_instance", 0),
            registers=registers,
            defaults=defaults,
            logging=logging_cfg,
            schedule=schedule,
            tibber=tibber,
            controls=controls,
            poll_interval_ms=data.get("poll_interval_ms", 1000),
            timezone=data.get("timezone", "UTC"),
        )


# Default configuration path
CONFIG_PATH: str = os.path.join(
    os.path.dirname(__file__), "../alfen_driver_config.yaml"
)


def load_config(
    config_file: str = "alfen_driver_config.yaml", validate: bool = True
) -> Config:
    """Load and validate configuration from YAML file.

    This function loads the configuration from a YAML file and optionally
    validates it using the ConfigValidator. Validation ensures all required
    fields are present, values are within acceptable ranges, and relationships
    between settings are correct.

    Args:
        config_file: Path to YAML configuration file.
        validate: Whether to validate the configuration (default: True).

    Returns:
        Loaded and validated Config object.

    Raises:
        ConfigurationError: If config file cannot be loaded, parsed, or
            fails validation. The error message includes specific validation
            failures and suggestions for fixes.

    Example:
        ```python
        # Load with validation (recommended)
        config = load_config("alfen_driver_config.yaml")

        # Load without validation (for testing)
        config = load_config("test_config.yaml", validate=False)
        ```
    """
    try:
        # Check if file exists
        if not os.path.exists(config_file):
            # Try default location if custom file not found
            if config_file == "alfen_driver_config.yaml" and os.path.exists(
                CONFIG_PATH
            ):
                config_file = CONFIG_PATH
            else:
                raise FileNotFoundError(f"Configuration file not found: {config_file}")

        with open(config_file, encoding="utf-8") as f:
            config_dict = yaml.safe_load(f)

        if not isinstance(config_dict, dict):
            raise ConfigurationError(
                "Configuration file must contain a valid YAML dictionary"
            )

        # Validate configuration if requested
        if validate:
            from .config_validator import ConfigValidator

            validator = ConfigValidator()
            validator.validate_or_raise(config_dict)
            logger.info("Configuration validated successfully")

        # Create Config object from dictionary
        config = Config.from_dict(config_dict)

        logger.info(f"Configuration loaded from {config_file}")
        return config

    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {config_file}")
        raise ConfigurationError(str(e)) from e
    except yaml.YAMLError as e:
        logger.error(f"Error parsing configuration file: {e}")
        raise ConfigurationError(f"Invalid YAML in configuration file: {e}") from e
    except ConfigurationError:
        # Re-raise validation errors as-is (they already have detailed messages)
        raise
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        raise ConfigurationError(f"Failed to load configuration: {e}") from e


def parse_hhmm_to_minutes(timestr: Any) -> int:
    """Parse HH:MM time string to minutes since midnight.

    Args:
        timestr: Time string in HH:MM format.

    Returns:
        Minutes since midnight (0-1439).
    """
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
    """Load persisted configuration from disk.

    Returns:
        The loaded config dictionary, or None if file not found or invalid.

    Raises:
        OSError, json.JSONDecodeError: Handled and logged.
    """
    try:
        if not os.path.exists(config_file_path):
            return None
        with open(config_file_path, encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)
            return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to load persisted config: {e}")
        return None
