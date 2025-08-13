"""Tests for custom exceptions."""

from alfen_driver.exceptions import (
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


class TestAlfenDriverError:
    """Tests for the base AlfenDriverError class."""

    def test_basic_error(self) -> None:
        """Test basic error creation."""
        error = AlfenDriverError("Test message")
        assert str(error) == "Test message"
        assert error.message == "Test message"
        assert error.details is None

    def test_error_with_details(self) -> None:
        """Test error with details."""
        error = AlfenDriverError("Test message", "Additional details")
        assert str(error) == "Test message: Additional details"
        assert error.message == "Test message"
        assert error.details == "Additional details"

    def test_error_inheritance(self) -> None:
        """Test that it's a proper Exception subclass."""
        error = AlfenDriverError("Test")
        assert isinstance(error, Exception)


class TestConfigurationError:
    """Tests for ConfigurationError."""

    def test_basic_config_error(self) -> None:
        """Test basic configuration error."""
        error = ConfigurationError("Invalid config")
        assert "Invalid config" in str(error)
        assert error.config_field is None
        assert error.config_value is None

    def test_config_error_with_field(self) -> None:
        """Test configuration error with field context."""
        error = ConfigurationError("Invalid value", config_field="modbus.port")
        assert "Invalid value" in str(error)
        assert "field 'modbus.port'" in str(error)
        assert error.config_field == "modbus.port"

    def test_config_error_with_field_and_value(self) -> None:
        """Test configuration error with field and value context."""
        error = ConfigurationError(
            "Invalid value", config_field="modbus.port", config_value="invalid"
        )
        assert "Invalid value" in str(error)
        assert "field 'modbus.port'" in str(error)
        assert "with value 'invalid'" in str(error)
        assert error.config_field == "modbus.port"
        assert error.config_value == "invalid"


class TestModbusErrors:
    """Tests for Modbus-related errors."""

    def test_connection_error(self) -> None:
        """Test Modbus connection error."""
        error = ModbusConnectionError("connection", "Failed to connect to 192.168.1.100:502")
        assert "Modbus connection failed" in str(error)
        assert "192.168.1.100:502" in str(error)

    def test_connection_error_with_details(self) -> None:
        """Test Modbus connection error with details."""
        error = ModbusConnectionError("connection", "Timeout occurred at 192.168.1.100:502")
        assert "Modbus connection failed" in str(error)
        assert "192.168.1.100:502" in str(error)

    def test_read_error(self) -> None:
        """Test Modbus read error."""
        error = ModbusReadError("read", address=123, slave_id=1)
        assert "Modbus read failed" in str(error)
        assert "address 123" in str(error)
        assert "slave 1" in str(error)

    def test_write_error(self) -> None:
        """Test Modbus write error."""
        error = ModbusWriteError("write", address=456, slave_id=200)
        assert "Modbus write failed" in str(error)
        assert "address 456" in str(error)
        assert "slave 200" in str(error)

    def test_verification_error(self) -> None:
        """Test Modbus verification error."""
        error = ModbusVerificationError("verification", "mismatch", address=None, slave_id=None)
        assert "Modbus verification failed" in str(error)


class TestDBusError:
    """Tests for D-Bus errors."""

    def test_basic_dbus_error(self) -> None:
        """Test basic D-Bus error."""
        error = DBusError("com.victronenergy.evcharger.test")
        assert "D-Bus error for service 'com.victronenergy.evcharger.test'" in str(
            error
        )
        assert error.service_name == "com.victronenergy.evcharger.test"
        assert error.path is None

    def test_dbus_error_with_path(self) -> None:
        """Test D-Bus error with path."""
        error = DBusError("com.victronenergy.evcharger.test", "/Status")
        assert "com.victronenergy.evcharger.test" in str(error)
        assert "at path '/Status'" in str(error)
        assert error.path == "/Status"


class TestStatusMappingError:
    """Tests for status mapping errors."""

    def test_status_mapping_error(self) -> None:
        """Test status mapping error."""
        error = StatusMappingError("Failed to map status value: 'UNKNOWN_STATUS'")
        assert "Failed to map status value" in str(error)


class TestChargingControlError:
    """Tests for charging control errors."""

    def test_charging_control_error(self) -> None:
        """Test charging control error."""
        error = ChargingControlError("Charging control 'set_current' failed for current 15.5A")
        assert "Charging control" in str(error)


class TestValidationError:
    """Tests for validation errors."""

    def test_validation_error(self) -> None:
        """Test validation error."""
        error = ValidationError("current_setting", -5.0, "must be non-negative")
        expected = (
            "Validation failed for 'current_setting': value '-5.0' "
            "violates constraint 'must be non-negative'"
        )
        assert expected in str(error)
        assert error.field_name == "current_setting"
        assert error.value == -5.0
        assert error.constraint == "must be non-negative"


class TestSessionError:
    """Tests for session errors."""

    def test_basic_session_error(self) -> None:
        """Test basic session error."""
        error = SessionError("Charging session error")
        assert "Charging session error" in str(error)

    def test_session_error_with_id(self) -> None:
        """Test session error with session ID."""
        error = SessionError("Charging session error (session: session_123)")
        assert "Charging session error (session: session_123)" in str(error)


class TestRetryExhaustedError:
    """Tests for retry exhausted errors."""

    def test_basic_retry_exhausted(self) -> None:
        """Test basic retry exhausted error."""
        error = RetryExhaustedError("Operation 'test_operation' failed after 3 attempts")
        assert "failed after 3 attempts" in str(error)

    def test_retry_exhausted_with_last_error(self) -> None:
        """Test retry exhausted with last error."""
        original_error = ValueError("Original error")
        error = RetryExhaustedError(
            "Operation 'test_operation' failed after 5 attempts", str(original_error)
        )
        assert "failed after 5 attempts" in str(error)
        assert "Original error" in str(error)


class TestServiceUnavailableError:
    """Tests for service unavailable errors."""

    def test_service_unavailable_error(self) -> None:
        """Test service unavailable error."""
        error = ServiceUnavailableError("Service 'test_service' is unavailable")
        assert "Service 'test_service' is unavailable" in str(error)
