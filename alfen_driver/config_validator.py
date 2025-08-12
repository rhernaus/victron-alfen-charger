"""Configuration validation for the Alfen EV Charger Driver.

This module provides comprehensive validation for driver configuration with
detailed error messages, suggestions for fixes, and automatic correction of
common issues where safe to do so.

The validator includes:
    - Type checking for all configuration fields
    - Range validation for numeric values
    - Format validation for IP addresses, timezones, etc.
    - Relationship validation between interdependent settings
    - Helpful error messages with correction suggestions
    - Optional auto-correction of minor issues

Example:
    ```python
    from alfen_driver.config_validator import ConfigValidator

    validator = ConfigValidator()
    config_dict = load_yaml("config.yaml")

    # Validate with detailed error reporting
    is_valid, errors = validator.validate(config_dict)
    if not is_valid:
        for error in errors:
            print(f"❌ {error}")

    # Or validate with exceptions
    validator.validate_or_raise(config_dict)
    ```
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pytz

from .exceptions import ConfigurationError


@dataclass
class ValidationError:
    """Represents a configuration validation error.

    Attributes:
        field: The configuration field path (e.g., "modbus.ip").
        message: Human-readable error message.
        value: The invalid value that was provided.
        suggestion: Optional suggestion for fixing the error.
        severity: Error severity level ("error", "warning", "info").
    """

    field: str
    message: str
    value: Any
    suggestion: Optional[str] = None
    severity: str = "error"


class ConfigValidator:
    """Comprehensive configuration validator for the Alfen driver.

    This validator performs multi-level validation including type checking,
    range validation, format validation, and cross-field relationship checks.
    It provides detailed error messages with suggestions for corrections.

    Attributes:
        _errors: List of validation errors found.
        _warnings: List of validation warnings.
        _auto_correct: Whether to automatically fix minor issues.
    """

    # Valid ranges for configuration values
    VALID_CURRENT_RANGE = (0.0, 80.0)  # Amperes
    VALID_VOLTAGE_RANGE = (100.0, 500.0)  # Volts
    VALID_PORT_RANGE = (1, 65535)
    VALID_SLAVE_ID_RANGE = (1, 247)
    VALID_POLL_INTERVAL_RANGE = (100, 60000)  # Milliseconds
    VALID_DEVICE_INSTANCE_RANGE = (0, 255)
    VALID_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

    def __init__(self, auto_correct: bool = False):
        """Initialize the configuration validator.

        Args:
            auto_correct: If True, automatically fix minor issues.
        """
        self._errors: List[ValidationError] = []
        self._warnings: List[ValidationError] = []
        self._auto_correct = auto_correct

    def validate(self, config: Dict[str, Any]) -> Tuple[bool, List[ValidationError]]:
        """Validate a configuration dictionary.

        Args:
            config: The configuration dictionary to validate.

        Returns:
            Tuple of (is_valid, errors) where is_valid is True if no errors,
            and errors is a list of ValidationError objects.

        Example:
            ```python
            validator = ConfigValidator()
            is_valid, errors = validator.validate(config_dict)
            if not is_valid:
                for error in errors:
                    print(f"Error in {error.field}: {error.message}")
                    if error.suggestion:
                        print(f"  Suggestion: {error.suggestion}")
            ```
        """
        self._errors = []
        self._warnings = []

        # Validate required top-level sections
        self._validate_required_sections(config)

        # Validate each section
        if "modbus" in config:
            self._validate_modbus_config(config["modbus"])

        if "registers" in config:
            self._validate_registers_config(config["registers"])

        if "defaults" in config:
            self._validate_defaults_config(config["defaults"])

        if "controls" in config:
            self._validate_controls_config(config["controls"])

        if "schedule" in config:
            self._validate_schedule_config(config["schedule"])

        if "logging" in config:
            self._validate_logging_config(config["logging"])

        # Validate global settings
        self._validate_global_settings(config)

        # Validate cross-field relationships
        self._validate_relationships(config)

        # Return results
        all_issues = self._errors + self._warnings
        return len(self._errors) == 0, all_issues

    def validate_or_raise(self, config: Dict[str, Any]) -> None:
        """Validate configuration and raise exception if invalid.

        Args:
            config: The configuration dictionary to validate.

        Raises:
            ConfigurationError: If configuration is invalid, with detailed
                error information in the exception message.

        Example:
            ```python
            validator = ConfigValidator()
            try:
                validator.validate_or_raise(config_dict)
                print("✅ Configuration is valid")
            except ConfigurationError as e:
                print(f"❌ Configuration error: {e}")
            ```
        """
        is_valid, errors = self.validate(config)
        if not is_valid:
            error_messages = []
            for error in errors:
                if error.severity == "error":
                    msg = f"• {error.field}: {error.message}"
                    if error.suggestion:
                        msg += f"\n  → {error.suggestion}"
                    error_messages.append(msg)

            if error_messages:
                full_message = "Configuration validation failed:\n" + "\n".join(
                    error_messages
                )
                raise ConfigurationError(full_message)

    def _add_error(
        self, field: str, message: str, value: Any, suggestion: Optional[str] = None
    ) -> None:
        """Add a validation error."""
        self._errors.append(
            ValidationError(
                field=field,
                message=message,
                value=value,
                suggestion=suggestion,
                severity="error",
            )
        )

    def _add_warning(
        self, field: str, message: str, value: Any, suggestion: Optional[str] = None
    ) -> None:
        """Add a validation warning."""
        self._warnings.append(
            ValidationError(
                field=field,
                message=message,
                value=value,
                suggestion=suggestion,
                severity="warning",
            )
        )

    def _validate_required_sections(self, config: Dict[str, Any]) -> None:
        """Validate that required configuration sections exist."""
        required_sections = ["modbus"]

        for section in required_sections:
            if section not in config:
                self._add_error(
                    section,
                    f"Required configuration section '{section}' is missing",
                    None,
                    f"Add a '{section}' section to your configuration file",
                )

    def _validate_modbus_config(self, modbus: Dict[str, Any]) -> None:
        """Validate Modbus configuration section."""
        # Validate IP address
        if "ip" not in modbus:
            self._add_error(
                "modbus.ip",
                "Modbus IP address is required",
                None,
                "Add 'ip' field with the Alfen charger's IP address (e.g., '192.168.1.100')",
            )
        else:
            ip = modbus["ip"]
            if not self._is_valid_ip(ip):
                self._add_error(
                    "modbus.ip",
                    f"Invalid IP address format: '{ip}'",
                    ip,
                    "Use a valid IPv4 address format (e.g., '192.168.1.100')",
                )

        # Validate port
        port = modbus.get("port", 502)
        if not isinstance(port, int):
            self._add_error(
                "modbus.port",
                f"Port must be an integer, got {type(port).__name__}",
                port,
                "Use an integer value between 1 and 65535 (default: 502)",
            )
        elif not self.VALID_PORT_RANGE[0] <= port <= self.VALID_PORT_RANGE[1]:
            self._add_error(
                "modbus.port",
                f"Port {port} is out of valid range {self.VALID_PORT_RANGE}",
                port,
                "Use standard Modbus TCP port 502 or a valid port number",
            )

        # Validate slave IDs
        socket_slave = modbus.get("socket_slave_id", 1)
        if not isinstance(socket_slave, int):
            self._add_error(
                "modbus.socket_slave_id",
                f"Socket slave ID must be integer, got {type(socket_slave).__name__}",
                socket_slave,
                "Use an integer value between 1 and 247 (typically 1 for Alfen)",
            )
        elif (
            not self.VALID_SLAVE_ID_RANGE[0]
            <= socket_slave
            <= self.VALID_SLAVE_ID_RANGE[1]
        ):
            self._add_error(
                "modbus.socket_slave_id",
                f"Socket slave ID {socket_slave} out of range {self.VALID_SLAVE_ID_RANGE[:2]}",
                socket_slave,
                "Use slave ID 1 for Alfen socket data",
            )

        station_slave = modbus.get("station_slave_id", 200)
        if not isinstance(station_slave, int):
            self._add_error(
                "modbus.station_slave_id",
                f"Station slave ID must be integer, got {type(station_slave).__name__}",
                station_slave,
                "Use an integer value between 1 and 247 (typically 200 for Alfen)",
            )
        elif (
            not self.VALID_SLAVE_ID_RANGE[0]
            <= station_slave
            <= self.VALID_SLAVE_ID_RANGE[1]
        ):
            self._add_error(
                "modbus.station_slave_id",
                f"Station slave ID {station_slave} out of range {self.VALID_SLAVE_ID_RANGE[:2]}",
                station_slave,
                "Use slave ID 200 for Alfen station control",
            )

    def _validate_registers_config(self, registers: Dict[str, Any]) -> None:
        """Validate register addresses configuration."""
        expected_registers = {
            "voltages": (
                0,
                65535,
                "Voltage register address (typically 306 for Alfen)",
            ),
            "currents": (
                0,
                65535,
                "Current register address (typically 320 for Alfen)",
            ),
            "power": (0, 65535, "Power register address (typically 344 for Alfen)"),
            "energy": (0, 65535, "Energy register address (typically 374 for Alfen)"),
            "status": (0, 65535, "Status register address (typically 1201 for Alfen)"),
            "amps_config": (
                0,
                65535,
                "Current setting register (typically 1210 for Alfen)",
            ),
            "phases": (0, 65535, "Phase setting register (typically 1215 for Alfen)"),
            "station_status": (
                0,
                65535,
                "Station status register (typically 1201 for Alfen)",
            ),
        }

        for reg_name, (min_val, max_val, description) in expected_registers.items():
            if reg_name in registers:
                value = registers[reg_name]
                if not isinstance(value, int):
                    self._add_error(
                        f"registers.{reg_name}",
                        f"Register address must be integer, got {type(value).__name__}",
                        value,
                        description,
                    )
                elif not min_val <= value <= max_val:
                    self._add_warning(
                        f"registers.{reg_name}",
                        f"Unusual register address {value}",
                        value,
                        description,
                    )

    def _validate_defaults_config(self, defaults: Dict[str, Any]) -> None:
        """Validate default values configuration."""
        # Validate intended current
        current = defaults.get("intended_set_current", 6.0)
        if not isinstance(current, (int, float)):
            self._add_error(
                "defaults.intended_set_current",
                f"Current must be a number, got {type(current).__name__}",
                current,
                "Use a numeric value in amperes (e.g., 6.0)",
            )
        elif not self.VALID_CURRENT_RANGE[0] <= current <= self.VALID_CURRENT_RANGE[1]:
            self._add_error(
                "defaults.intended_set_current",
                f"Current {current}A is out of valid range {self.VALID_CURRENT_RANGE}",
                current,
                f"Use current between {self.VALID_CURRENT_RANGE[0]}A-{self.VALID_CURRENT_RANGE[1]}A"
            )

        # Validate station max current
        max_current = defaults.get("station_max_current", 32.0)
        if not isinstance(max_current, (int, float)):
            self._add_error(
                "defaults.station_max_current",
                f"Max current must be a number, got {type(max_current).__name__}",
                max_current,
                "Use a numeric value in amperes (e.g., 32.0)",
            )
        elif (
            not self.VALID_CURRENT_RANGE[0]
            <= max_current
            <= self.VALID_CURRENT_RANGE[1]
        ):
            self._add_error(
                "defaults.station_max_current",
                f"Max current {max_current}A out of range {self.VALID_CURRENT_RANGE}",
                max_current,
                f"Use max current between {self.VALID_CURRENT_RANGE[0]}A-{self.VALID_CURRENT_RANGE[1]}A",
            )

    def _validate_controls_config(self, controls: Dict[str, Any]) -> None:
        """Validate control settings configuration."""
        # Validate max set current
        max_current = controls.get("max_set_current", 64.0)
        if not isinstance(max_current, (int, float)):
            self._add_error(
                "controls.max_set_current",
                f"Max set current must be a number, got {type(max_current).__name__}",
                max_current,
                "Use a numeric value in amperes",
            )
        elif (
            not self.VALID_CURRENT_RANGE[0]
            <= max_current
            <= self.VALID_CURRENT_RANGE[1]
        ):
            self._add_error(
                "controls.max_set_current",
                f"Max set current {max_current}A out of range {self.VALID_CURRENT_RANGE}",
                max_current,
                "Check your charger's maximum current rating",
            )

        # Validate current tolerance
        tolerance = controls.get("current_tolerance", 0.5)
        if not isinstance(tolerance, (int, float)):
            self._add_error(
                "controls.current_tolerance",
                f"Current tolerance must be a number, got {type(tolerance).__name__}",
                tolerance,
                "Use a numeric value in amperes (e.g., 0.5)",
            )
        elif tolerance < 0:
            self._add_error(
                "controls.current_tolerance",
                f"Current tolerance cannot be negative: {tolerance}",
                tolerance,
                "Use a positive tolerance value (e.g., 0.5A)",
            )
        elif tolerance > 5.0:
            self._add_warning(
                "controls.current_tolerance",
                f"Large current tolerance {tolerance}A may cause verification issues",
                tolerance,
                "Consider using a smaller tolerance (e.g., 0.5A to 1.0A)",
            )

    def _validate_schedule_config(self, schedule: Dict[str, Any]) -> None:
        """Validate schedule configuration."""
        items = schedule.get("items", [])

        if not isinstance(items, list):
            self._add_error(
                "schedule.items",
                f"Schedule items must be a list, got {type(items).__name__}",
                items,
                "Use a list of schedule configurations",
            )
            return

        for i, item in enumerate(items):
            if not isinstance(item, dict):
                self._add_error(
                    f"schedule.items[{i}]",
                    f"Schedule item must be a dictionary, got {type(item).__name__}",
                    item,
                    "Each schedule item should have 'active', 'days', and time settings",
                )
                continue

            # Validate schedule fields
            if "active" in item and not isinstance(item["active"], bool):
                self._add_error(
                    f"schedule.items[{i}].active",
                    f"Active flag must be boolean, got {type(item['active']).__name__}",
                    item["active"],
                    "Use true or false",
                )

            # Validate days
            if "days" in item:
                days = item["days"]
                if not isinstance(days, list):
                    self._add_error(
                        f"schedule.items[{i}].days",
                        f"Days must be a list, got {type(days).__name__}",
                        days,
                        "Use a list of day numbers (0=Monday, 6=Sunday)",
                    )
                else:
                    for day in days:
                        if not isinstance(day, int) or not 0 <= day <= 6:
                            self._add_error(
                                f"schedule.items[{i}].days",
                                f"Invalid day value: {day}",
                                day,
                                "Use day numbers 0-6 (0=Monday, 6=Sunday)",
                            )

            # Validate time format
            for time_field in ["start_time", "end_time"]:
                if time_field in item:
                    time_str = item[time_field]
                    if not self._is_valid_time_format(time_str):
                        self._add_error(
                            f"schedule.items[{i}].{time_field}",
                            f"Invalid time format: '{time_str}'",
                            time_str,
                            "Use HH:MM format (e.g., '08:00' or '22:30')",
                        )

    def _validate_logging_config(self, logging: Dict[str, Any]) -> None:
        """Validate logging configuration."""
        # Validate log level
        level = logging.get("level", "INFO")
        if not isinstance(level, str):
            self._add_error(
                "logging.level",
                f"Log level must be a string, got {type(level).__name__}",
                level,
                f"Use one of: {', '.join(self.VALID_LOG_LEVELS)}",
            )
        elif level.upper() not in self.VALID_LOG_LEVELS:
            self._add_error(
                "logging.level",
                f"Invalid log level: '{level}'",
                level,
                f"Use one of: {', '.join(self.VALID_LOG_LEVELS)}",
            )

        # Validate log file path
        if "file" in logging:
            file_path = logging["file"]
            if not isinstance(file_path, str):
                self._add_error(
                    "logging.file",
                    f"Log file path must be a string, got {type(file_path).__name__}",
                    file_path,
                    "Use a valid file path (e.g., '/var/log/alfen_driver.log')",
                )
            elif not file_path:
                self._add_error(
                    "logging.file",
                    "Log file path cannot be empty",
                    file_path,
                    "Specify a valid log file path or remove this field",
                )

    def _validate_global_settings(self, config: Dict[str, Any]) -> None:
        """Validate global configuration settings."""
        # Validate device instance
        if "device_instance" in config:
            instance = config["device_instance"]
            if not isinstance(instance, int):
                self._add_error(
                    "device_instance",
                    f"Device instance must be an integer, got {type(instance).__name__}",
                    instance,
                    "Use an integer value (typically 0 for first device)",
                )
            elif (
                not self.VALID_DEVICE_INSTANCE_RANGE[0]
                <= instance
                <= self.VALID_DEVICE_INSTANCE_RANGE[1]
            ):
                self._add_error(
                    "device_instance",
                    f"Device instance {instance} is out of valid range {self.VALID_DEVICE_INSTANCE_RANGE}",
                    instance,
                    "Use a value between 0 and 255",
                )

        # Validate poll interval
        if "poll_interval_ms" in config:
            interval = config["poll_interval_ms"]
            if not isinstance(interval, int):
                self._add_error(
                    "poll_interval_ms",
                    f"Poll interval must be an integer, got {type(interval).__name__}",
                    interval,
                    "Use an integer value in milliseconds (e.g., 1000 for 1 second)",
                )
            elif (
                not self.VALID_POLL_INTERVAL_RANGE[0]
                <= interval
                <= self.VALID_POLL_INTERVAL_RANGE[1]
            ):
                self._add_error(
                    "poll_interval_ms",
                    f"Poll interval {interval}ms is out of valid range {self.VALID_POLL_INTERVAL_RANGE}",
                    interval,
                    "Use a value between 100ms and 60000ms",
                )
            elif interval < 500:
                self._add_warning(
                    "poll_interval_ms",
                    f"Very short poll interval {interval}ms may cause high CPU usage",
                    interval,
                    "Consider using 1000ms or higher for normal operation",
                )

        # Validate timezone
        if "timezone" in config:
            tz = config["timezone"]
            if not isinstance(tz, str):
                self._add_error(
                    "timezone",
                    f"Timezone must be a string, got {type(tz).__name__}",
                    tz,
                    "Use a valid timezone string (e.g., 'Europe/Amsterdam')",
                )
            elif not self._is_valid_timezone(tz):
                self._add_error(
                    "timezone",
                    f"Invalid timezone: '{tz}'",
                    tz,
                    "Use a valid timezone like 'UTC', 'Europe/Amsterdam', or 'America/New_York'",
                )

    def _validate_relationships(self, config: Dict[str, Any]) -> None:
        """Validate relationships between configuration values."""
        # Check that intended current doesn't exceed max current
        if "defaults" in config and "controls" in config:
            intended = config["defaults"].get("intended_set_current", 6.0)
            max_set = config["controls"].get("max_set_current", 64.0)

            if isinstance(intended, (int, float)) and isinstance(max_set, (int, float)):
                if intended > max_set:
                    self._add_error(
                        "defaults.intended_set_current",
                        f"Intended current {intended}A exceeds max set current {max_set}A",
                        intended,
                        f"Reduce intended current to {max_set}A or less, or increase max_set_current",
                    )

        # Check that station max current is reasonable
        if "defaults" in config:
            station_max = config["defaults"].get("station_max_current", 32.0)
            if "controls" in config:
                max_set = config["controls"].get("max_set_current", 64.0)

                if isinstance(station_max, (int, float)) and isinstance(
                    max_set, (int, float)
                ):
                    if max_set > station_max:
                        self._add_warning(
                            "controls.max_set_current",
                            f"Max set current {max_set}A exceeds station max {station_max}A",
                            max_set,
                            f"The charger may limit current to {station_max}A",
                        )

    def _is_valid_ip(self, ip: str) -> bool:
        """Check if a string is a valid IP address."""
        try:
            ipaddress.ip_address(ip)
            return True
        except (ValueError, TypeError):
            return False

    def _is_valid_time_format(self, time_str: str) -> bool:
        """Check if a string is in HH:MM format (requires zero-padding)."""
        if not isinstance(time_str, str):
            return False
        # Require zero-padding: HH:MM format only
        pattern = r"^([0-1][0-9]|2[0-3]):([0-5][0-9])$"
        return bool(re.match(pattern, time_str))

    def _is_valid_timezone(self, tz: str) -> bool:
        """Check if a string is a valid timezone."""
        try:
            pytz.timezone(tz)
            return True
        except (pytz.UnknownTimeZoneError, AttributeError):
            return False

    def get_config_schema(self) -> Dict[str, Any]:
        """Get the expected configuration schema with descriptions.

        Returns:
            Dictionary describing the configuration schema with types and
            descriptions for each field.

        Example:
            ```python
            validator = ConfigValidator()
            schema = validator.get_config_schema()
            print(json.dumps(schema, indent=2))
            ```
        """
        return {
            "modbus": {
                "type": "object",
                "required": True,
                "description": "Modbus TCP connection settings",
                "fields": {
                    "ip": {
                        "type": "string",
                        "required": True,
                        "format": "ipv4",
                        "description": "IP address of the Alfen charger",
                        "example": "192.168.1.100",
                    },
                    "port": {
                        "type": "integer",
                        "required": False,
                        "default": 502,
                        "range": [1, 65535],
                        "description": "Modbus TCP port",
                    },
                    "socket_slave_id": {
                        "type": "integer",
                        "required": False,
                        "default": 1,
                        "range": [1, 247],
                        "description": "Slave ID for socket data (voltages, currents, power)",
                    },
                    "station_slave_id": {
                        "type": "integer",
                        "required": False,
                        "default": 200,
                        "range": [1, 247],
                        "description": "Slave ID for station control",
                    },
                },
            },
            "defaults": {
                "type": "object",
                "required": False,
                "description": "Default operational values",
                "fields": {
                    "intended_set_current": {
                        "type": "float",
                        "required": False,
                        "default": 6.0,
                        "range": [0.0, 80.0],
                        "unit": "amperes",
                        "description": "Default charging current",
                    },
                    "station_max_current": {
                        "type": "float",
                        "required": False,
                        "default": 32.0,
                        "range": [0.0, 80.0],
                        "unit": "amperes",
                        "description": "Maximum current the station can provide",
                    },
                },
            },
            "controls": {
                "type": "object",
                "required": False,
                "description": "Control and safety limits",
                "fields": {
                    "max_set_current": {
                        "type": "float",
                        "required": False,
                        "default": 64.0,
                        "range": [0.0, 80.0],
                        "unit": "amperes",
                        "description": "Maximum current that can be set",
                    },
                    "current_tolerance": {
                        "type": "float",
                        "required": False,
                        "default": 0.5,
                        "range": [0.0, 5.0],
                        "unit": "amperes",
                        "description": "Tolerance for current verification",
                    },
                },
            },
            "device_instance": {
                "type": "integer",
                "required": False,
                "default": 0,
                "range": [0, 255],
                "description": "Venus OS device instance number",
            },
            "poll_interval_ms": {
                "type": "integer",
                "required": False,
                "default": 1000,
                "range": [100, 60000],
                "unit": "milliseconds",
                "description": "Polling interval for reading charger data",
            },
            "timezone": {
                "type": "string",
                "required": False,
                "default": "UTC",
                "format": "timezone",
                "description": "Timezone for schedule operations",
                "example": "Europe/Amsterdam",
            },
        }
