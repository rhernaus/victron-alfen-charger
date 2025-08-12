"""Custom exception classes for the Alfen EV Charger Driver.

This module defines a comprehensive hierarchy of exceptions for the Alfen driver,
providing detailed error information and context for debugging and error handling.

The exception hierarchy follows the pattern:
    AlfenDriverError (base)
    ├── ConfigurationError
    ├── ModbusConnectionError
    ├── ModbusReadError
    ├── ModbusWriteError
    ├── ModbusVerificationError
    ├── DBusError
    ├── StatusMappingError
    ├── ChargingControlError
    ├── ValidationError
    ├── SessionError
    ├── RetryExhaustedError
    └── ServiceUnavailableError

Example:
    ```python
    from alfen_driver.exceptions import ModbusReadError, ConfigurationError

    try:
        # Modbus operation that might fail
        registers = read_modbus_registers(client, address, count, slave_id)
    except ModbusReadError as e:
        logger.error(f"Failed to read registers: {e}")
        logger.debug(f"Address: {e.address}, Count: {e.count}, Slave: {e.slave_id}")
    ```
"""

from typing import Any, Optional


class AlfenDriverError(Exception):
    """Base exception for all Alfen driver related errors.

    This is the root exception class for all driver-specific errors. It provides
    a consistent interface for error handling with structured error information.

    Attributes:
        message (str): The primary error message describing what went wrong.
        details (Optional[str]): Additional contextual information about the error.

    Example:
        ```python
        raise AlfenDriverError("Operation failed", "Connection timeout after 5 seconds")
        ```
    """

    def __init__(self, message: str, details: Optional[str] = None) -> None:
        """Initialize the exception with a message and optional details.

        Args:
            message: The primary error message describing what went wrong.
            details: Additional contextual information about the error.
        """
        self.message = message
        self.details = details
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Format the exception message with details if available.

        Returns:
            Formatted error message combining message and details.
        """
        if self.details:
            return f"{self.message}: {self.details}"
        return self.message


class ConfigurationError(AlfenDriverError):
    """Raised when there are configuration-related errors.

    This exception is used when configuration files are invalid, missing required
    fields, contain invalid values, or fail validation checks.

    Attributes:
        config_field (Optional[str]): The specific configuration field that
            caused the error.
        config_value (Optional[Any]): The invalid value that was provided.

    Example:
        ```python
        raise ConfigurationError(
            "Invalid port number",
            config_field="modbus.port",
            config_value=-1
        )
        ```
    """

    def __init__(
        self,
        message: str,
        config_field: Optional[str] = None,
        config_value: Optional[Any] = None,
    ) -> None:
        """Initialize with configuration context.

        Args:
            message: The primary error message.
            config_field: The specific configuration field that caused the error.
            config_value: The invalid value that was provided.
        """
        self.config_field = config_field
        self.config_value = config_value

        details = None
        if config_field:
            details = f"field '{config_field}'"
            if config_value is not None:
                details += f" with value '{config_value}'"

        super().__init__(message, details)


class ModbusConnectionError(AlfenDriverError):
    """Raised when Modbus TCP connection fails.

    This exception occurs when the driver cannot establish a connection to the
    Alfen charger's Modbus TCP interface, typically due to network issues,
    incorrect IP address/port, or the charger being unreachable.

    Attributes:
        host (str): The IP address or hostname that failed to connect.
        port (int): The port number that was attempted.

    Example:
        ```python
        raise ModbusConnectionError(
            "192.168.1.100",
            502,
            "Connection timeout after 10 seconds"
        )
        ```
    """

    def __init__(self, host: str, port: int, details: Optional[str] = None) -> None:
        """Initialize with connection details.

        Args:
            host: The IP address or hostname that failed to connect.
            port: The port number that was attempted.
            details: Additional information about the connection failure.
        """
        self.host = host
        self.port = port
        message = f"Failed to connect to Modbus server at {host}:{port}"
        super().__init__(message, details)


class ModbusReadError(AlfenDriverError):
    """Raised when Modbus read operations fail.

    This exception occurs when attempting to read holding registers from the Alfen
    charger via Modbus TCP. Common causes include network timeouts, invalid register
    addresses, or the charger being in an unresponsive state.

    Attributes:
        address (int): The starting register address that was being read.
        count (int): The number of registers that were requested.
        slave_id (int): The Modbus slave ID that was targeted.

    Example:
        ```python
        try:
            registers = client.read_holding_registers(123, 5, slave=1)
        except ModbusReadError as e:
            logger.error(f"Read failed: {e}")
            logger.debug(f"Failed at address {e.address} for {e.count} registers")
        ```
    """

    def __init__(
        self, address: int, count: int, slave_id: int, details: Optional[str] = None
    ) -> None:
        """Initialize with read operation details.

        Args:
            address: The starting register address that was being read.
            count: The number of registers that were requested.
            slave_id: The Modbus slave ID that was targeted.
            details: Additional information about the read failure.
        """
        self.address = address
        self.count = count
        self.slave_id = slave_id
        message = (
            f"Failed to read {count} registers from address {address} "
            f"(slave {slave_id})"
        )
        super().__init__(message, details)


class ModbusWriteError(AlfenDriverError):
    """Raised when Modbus write operations fail.

    This exception occurs when attempting to write values to holding registers
    in the Alfen charger. This typically happens when trying to set charging
    current, number of phases, or other control parameters.

    Attributes:
        address (int): The register address that was being written to.
        value (Any): The value that was being written.
        slave_id (int): The Modbus slave ID that was targeted.

    Example:
        ```python
        try:
            client.write_register(1210, current_value, slave=200)
        except ModbusWriteError as e:
            logger.error(f"Write failed: {e}")
            logger.debug(f"Could not write {e.value} to address {e.address}")
        ```
    """

    def __init__(
        self, address: int, value: Any, slave_id: int, details: Optional[str] = None
    ) -> None:
        """Initialize with write operation details.

        Args:
            address: The register address that was being written to.
            value: The value that was being written.
            slave_id: The Modbus slave ID that was targeted.
            details: Additional information about the write failure.
        """
        self.address = address
        self.value = value
        self.slave_id = slave_id
        message = (
            f"Failed to write value '{value}' to address {address} (slave {slave_id})"
        )
        super().__init__(message, details)


class ModbusVerificationError(AlfenDriverError):
    """Raised when Modbus write verification fails.

    This exception occurs when a write operation appears to succeed, but a
    subsequent read shows that the actual value doesn't match what was written.
    This is used to ensure that critical parameters like charging current are
    properly applied by the charger.

    Attributes:
        expected (Any): The value that was expected to be written.
        actual (Any): The actual value that was read back.
        tolerance (float): The tolerance that was used for comparison.

    Example:
        ```python
        try:
            write_and_verify_current(client, 16.0, tolerance=0.25)
        except ModbusVerificationError as e:
            logger.error(f"Verification failed: expected {e.expected}, got {e.actual}")
        ```
    """

    def __init__(
        self,
        expected: Any,
        actual: Any,
        tolerance: float,
        details: Optional[str] = None,
    ) -> None:
        """Initialize with verification details.

        Args:
            expected: The value that was expected to be written.
            actual: The actual value that was read back.
            tolerance: The tolerance that was used for comparison.
            details: Additional information about the verification failure.
        """
        self.expected = expected
        self.actual = actual
        self.tolerance = tolerance
        message = (
            f"Write verification failed: expected {expected}, got {actual} "
            f"(tolerance: {tolerance})"
        )
        super().__init__(message, details)


class DBusError(AlfenDriverError):
    """Raised when D-Bus operations fail.

    This exception occurs when the driver cannot communicate with the Victron
    Venus OS system via D-Bus. This includes service registration failures,
    path publication errors, or communication timeouts with the Venus OS.

    Attributes:
        service_name (str): The D-Bus service name that encountered the error.
        path (Optional[str]): The specific D-Bus path involved in the error.

    Example:
        ```python
        try:
            service = VeDbusService("com.victronenergy.evcharger.alfen_0")
        except DBusError as e:
            logger.error(f"D-Bus service registration failed: {e}")
        ```
    """

    def __init__(
        self,
        service_name: str,
        path: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        """Initialize with D-Bus context.

        Args:
            service_name: The D-Bus service name that encountered the error.
            path: The specific D-Bus path involved in the error.
            details: Additional information about the D-Bus error.
        """
        self.service_name = service_name
        self.path = path

        message = f"D-Bus error for service '{service_name}'"
        if path:
            message += f" at path '{path}'"

        super().__init__(message, details)


class StatusMappingError(AlfenDriverError):
    """Raised when status mapping fails.

    This exception occurs when the driver receives a status value from the Alfen
    charger that cannot be mapped to a corresponding Victron EVC_STATUS enum value.
    This typically happens with unknown status codes or firmware differences.

    Attributes:
        raw_status (Any): The raw status value that could not be mapped.

    Example:
        ```python
        try:
            victron_status = map_alfen_to_victron_status("UNKNOWN_STATUS")
        except StatusMappingError as e:
            logger.warning(f"Unknown status received: {e.raw_status}")
            victron_status = EVC_STATUS.DISCONNECTED  # Fallback
        ```
    """

    def __init__(self, raw_status: Any, details: Optional[str] = None) -> None:
        """Initialize with status mapping context.

        Args:
            raw_status: The raw status value that could not be mapped.
            details: Additional information about the mapping failure.
        """
        self.raw_status = raw_status
        message = f"Failed to map status value: '{raw_status}'"
        super().__init__(message, details)


class ChargingControlError(AlfenDriverError):
    """Raised when charging control operations fail.

    This exception occurs when the driver cannot properly control charging
    parameters such as current setting, phase configuration, or charging
    mode changes. This may be due to hardware limits, safety constraints,
    or communication errors.

    Attributes:
        operation (str): The charging control operation that failed.
        current_value (float): The current value involved in the operation.

    Example:
        ```python
        try:
            set_charging_current(client, 32.0)
        except ChargingControlError as e:
            logger.error(f"Cannot set current: {e}")
            logger.debug(f"Operation: {e.operation}, Value: {e.current_value}A")
        ```
    """

    def __init__(
        self, operation: str, current_value: float, details: Optional[str] = None
    ) -> None:
        """Initialize with charging control context.

        Args:
            operation: The charging control operation that failed.
            current_value: The current value involved in the operation.
            details: Additional information about the control failure.
        """
        self.operation = operation
        self.current_value = current_value
        message = f"Charging control '{operation}' failed for current {current_value}A"
        super().__init__(message, details)


class ValidationError(AlfenDriverError):
    """Raised when data validation fails.

    This exception is used throughout the driver when input validation fails,
    such as invalid configuration values, out-of-range parameters, or malformed
    data structures. It provides detailed context about what validation failed.

    Attributes:
        field_name (str): The name of the field that failed validation.
        value (Any): The actual value that was provided.
        constraint (str): Description of the constraint that was violated.

    Example:
        ```python
        def validate_current(current: float) -> None:
            if current < 0 or current > 64:
                raise ValidationError(
                    "current", current, "must be between 0 and 64 amperes"
                )
        ```
    """

    def __init__(
        self,
        field_name: str,
        value: Any,
        constraint: str,
        details: Optional[str] = None,
    ) -> None:
        """Initialize with validation context.

        Args:
            field_name: The name of the field that failed validation.
            value: The actual value that was provided.
            constraint: Description of the constraint that was violated.
            details: Additional information about the validation failure.
        """
        self.field_name = field_name
        self.value = value
        self.constraint = constraint
        message = (
            f"Validation failed for '{field_name}': value '{value}' "
            f"violates constraint '{constraint}'"
        )
        super().__init__(message, details)


class SessionError(AlfenDriverError):
    """Raised when charging session operations fail.

    This exception occurs when there are problems with charging session
    management, such as session tracking errors, energy measurement issues,
    or session state synchronization problems.

    Attributes:
        session_id (Optional[str]): The identifier of the charging session.

    Example:
        ```python
        try:
            start_charging_session(vehicle_id)
        except SessionError as e:
            logger.error(f"Session management failed: {e}")
            if e.session_id:
                cleanup_session(e.session_id)
        ```
    """

    def __init__(
        self, session_id: Optional[str] = None, details: Optional[str] = None
    ) -> None:
        """Initialize with session context.

        Args:
            session_id: The identifier of the charging session.
            details: Additional information about the session error.
        """
        self.session_id = session_id
        message = "Charging session error"
        if session_id:
            message += f" (session: {session_id})"
        super().__init__(message, details)


class RetryExhaustedError(AlfenDriverError):
    """Raised when retry attempts are exhausted.

    This exception is thrown by the error recovery system when an operation
    has been retried the maximum number of times without success. It provides
    context about what operation failed and preserves the last error encountered.

    Attributes:
        operation (str): The name of the operation that was being retried.
        attempts (int): The total number of attempts that were made.
        last_error (Optional[Exception]): The last exception that caused the failure.

    Example:
        ```python
        try:
            retry_modbus_operation(read_registers, max_retries=3)
        except RetryExhaustedError as e:
            logger.error(f"Giving up on {e.operation} after {e.attempts} attempts")
            if e.last_error:
                logger.debug(f"Last error: {e.last_error}")
        ```
    """

    def __init__(
        self, operation: str, attempts: int, last_error: Optional[Exception] = None
    ) -> None:
        """Initialize with retry context.

        Args:
            operation: The name of the operation that was being retried.
            attempts: The total number of attempts that were made.
            last_error: The last exception that caused the failure.
        """
        self.operation = operation
        self.attempts = attempts
        self.last_error = last_error

        message = f"Operation '{operation}' failed after {attempts} attempts"
        details = str(last_error) if last_error else None
        super().__init__(message, details)


class ServiceUnavailableError(AlfenDriverError):
    """Raised when a required service is unavailable.

    This exception occurs when the driver cannot access a required external
    service, such as the Venus OS system service, network services, or other
    dependencies that are necessary for normal operation.

    Attributes:
        service_name (str): The name of the service that is unavailable.

    Example:
        ```python
        try:
            connect_to_venus_os()
        except ServiceUnavailableError as e:
            logger.error(f"Cannot connect to {e.service_name}")
            enter_degraded_mode()
        ```
    """

    def __init__(self, service_name: str, details: Optional[str] = None) -> None:
        """Initialize with service context.

        Args:
            service_name: The name of the service that is unavailable.
            details: Additional information about the service unavailability.
        """
        self.service_name = service_name
        message = f"Service '{service_name}' is unavailable"
        super().__init__(message, details)
