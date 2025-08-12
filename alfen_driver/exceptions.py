"""Simplified exception classes for the Alfen EV Charger Driver.

This module defines a streamlined exception hierarchy for the Alfen driver,
focusing on practical error handling for an embedded system.

The exception hierarchy:
    AlfenDriverError (base)
    ├── ConfigurationError
    ├── ModbusError (combines connection/read/write/verification)
    ├── DBusError
    └── ValidationError
"""

from typing import Any, Optional


class AlfenDriverError(Exception):
    """Base exception for all Alfen driver related errors."""

    def __init__(self, message: str, details: Optional[str] = None) -> None:
        """Initialize the exception with a message and optional details."""
        self.message = message
        self.details = details
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Format the exception message with details if available."""
        if self.details:
            return f"{self.message}: {self.details}"
        return self.message


class ConfigurationError(AlfenDriverError):
    """Raised when there are configuration-related errors."""

    def __init__(
        self,
        message: str,
        config_field: Optional[str] = None,
        config_value: Optional[Any] = None,
    ) -> None:
        """Initialize with configuration context."""
        self.config_field = config_field
        self.config_value = config_value

        details = None
        if config_field:
            details = f"field '{config_field}'"
            if config_value is not None:
                details += f" with value '{config_value}'"

        super().__init__(message, details)


class ModbusError(AlfenDriverError):
    """Raised for all Modbus-related errors (connection, read, write, verification)."""

    def __init__(
        self,
        operation: str,
        details: Optional[str] = None,
        address: Optional[int] = None,
        slave_id: Optional[int] = None,
    ) -> None:
        """Initialize with Modbus operation context."""
        self.operation = operation
        self.address = address
        self.slave_id = slave_id

        message = f"Modbus {operation} failed"
        if address is not None:
            message += f" at address {address}"
        if slave_id is not None:
            message += f" (slave {slave_id})"

        super().__init__(message, details)


# Compatibility aliases for existing code
ModbusConnectionError = ModbusError
ModbusReadError = ModbusError
ModbusWriteError = ModbusError
ModbusVerificationError = ModbusError


class DBusError(AlfenDriverError):
    """Raised when D-Bus operations fail."""

    def __init__(
        self,
        service_name: str,
        path: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        """Initialize with D-Bus context."""
        self.service_name = service_name
        self.path = path

        message = f"D-Bus error for service '{service_name}'"
        if path:
            message += f" at path '{path}'"

        super().__init__(message, details)


class ValidationError(AlfenDriverError):
    """Raised when data validation fails."""

    def __init__(
        self,
        field_name: str,
        value: Any,
        constraint: str,
        details: Optional[str] = None,
    ) -> None:
        """Initialize with validation context."""
        self.field_name = field_name
        self.value = value
        self.constraint = constraint
        message = (
            f"Validation failed for '{field_name}': value '{value}' "
            f"violates constraint '{constraint}'"
        )
        super().__init__(message, details)


# Additional compatibility aliases for minimal code changes
ChargingControlError = AlfenDriverError
StatusMappingError = AlfenDriverError
SessionError = AlfenDriverError
RetryExhaustedError = AlfenDriverError
ServiceUnavailableError = AlfenDriverError
