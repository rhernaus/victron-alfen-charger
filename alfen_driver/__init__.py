"""Victron Energy integration driver for Alfen EV chargers."""

__version__ = "1.0.0"
__author__ = "Ron"
__email__ = "ron@example.com"

# Import core components that don't depend on system libraries
from .config import Config
from .exceptions import (
    AlfenDriverError,
    ChargingControlError,
    ConfigurationError,
    DBusError,
    ModbusConnectionError,
    ModbusReadError,
    ModbusVerificationError,
    ModbusWriteError,
    RetryExhaustedError,
    ServiceUnavailableError,
    SessionError,
    StatusMappingError,
    ValidationError,
)


# Lazy import for AlfenDriver to avoid system dependency issues
def get_driver() -> type:
    """Get the AlfenDriver class (lazy import to avoid system dependencies)."""
    from .driver import AlfenDriver

    return AlfenDriver


__all__ = [
    "get_driver",
    "Config",
    "AlfenDriverError",
    "ConfigurationError",
    "ModbusConnectionError",
    "ModbusReadError",
    "ModbusWriteError",
    "ModbusVerificationError",
    "DBusError",
    "StatusMappingError",
    "ChargingControlError",
    "ValidationError",
    "SessionError",
    "RetryExhaustedError",
    "ServiceUnavailableError",
]
