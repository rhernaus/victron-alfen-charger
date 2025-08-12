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
    RetryExhaustedException,
    ServiceUnavailableError,
    SessionError,
    StatusMappingError,
    ValidationError,
)


class TestAlfenDriverError:
    """Tests for the base AlfenDriverError class."""

    def test_basic_error(self):
        """Test basic error creation."""
        error = AlfenDriverError("Test message")
        assert str(error) == "Test message"
        assert error.message == "Test message"
        assert error.details is None

    def test_error_with_details(self):
        """Test error with details."""
        error = AlfenDriverError("Test message", "Additional details")
        assert str(error) == "Test message: Additional details"
        assert error.message == "Test message"
        assert error.details == "Additional details"

    def test_error_inheritance(self):
        """Test that it's a proper Exception subclass."""
        error = AlfenDriverError("Test")
        assert isinstance(error, Exception)


class TestConfigurationError:
    """Tests for ConfigurationError."""

    def test_basic_config_error(self):
        """Test basic configuration error."""
        error = ConfigurationError("Invalid config")
        assert "Invalid config" in str(error)
        assert error.config_field is None
        assert error.config_value is None

    def test_config_error_with_field(self):
        """Test configuration error with field context."""
        error = ConfigurationError("Invalid value", config_field="modbus.port")
        assert "Invalid value" in str(error)
        assert "field 'modbus.port'" in str(error)
        assert error.config_field == "modbus.port"

    def test_config_error_with_field_and_value(self):
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

    def test_connection_error(self):
        """Test Modbus connection error."""
        error = ModbusConnectionError("192.168.1.100", 502)
        assert "Failed to connect to Modbus server at 192.168.1.100:502" in str(error)
        assert error.host == "192.168.1.100"
        assert error.port == 502

    def test_connection_error_with_details(self):
        """Test Modbus connection error with details."""
        error = ModbusConnectionError("192.168.1.100", 502, "Timeout occurred")
        assert "192.168.1.100:502" in str(error)
        assert "Timeout occurred" in str(error)

    def test_read_error(self):
        """Test Modbus read error."""
        error = ModbusReadError(123, 5, 1)
        assert "Failed to read 5 registers from address 123 (slave 1)" in str(error)
        assert error.address == 123
        assert error.count == 5
        assert error.slave_id == 1

    def test_write_error(self):
        """Test Modbus write error."""
        error = ModbusWriteError(456, 12.5, 200)
        assert "Failed to write value '12.5' to address 456 (slave 200)" in str(error)
        assert error.address == 456
        assert error.value == 12.5
        assert error.slave_id == 200

    def test_verification_error(self):
        """Test Modbus verification error."""
        error = ModbusVerificationError(10.0, 9.5, 0.1)
        assert (
            "Write verification failed: expected 10.0, got 9.5 (tolerance: 0.1)"
            in str(error)
        )
        assert error.expected == 10.0
        assert error.actual == 9.5
        assert error.tolerance == 0.1


class TestDBusError:
    """Tests for D-Bus errors."""

    def test_basic_dbus_error(self):
        """Test basic D-Bus error."""
        error = DBusError("com.victronenergy.evcharger.test")
        assert "D-Bus error for service 'com.victronenergy.evcharger.test'" in str(
            error
        )
        assert error.service_name == "com.victronenergy.evcharger.test"
        assert error.path is None

    def test_dbus_error_with_path(self):
        """Test D-Bus error with path."""
        error = DBusError("com.victronenergy.evcharger.test", "/Status")
        assert "com.victronenergy.evcharger.test" in str(error)
        assert "at path '/Status'" in str(error)
        assert error.path == "/Status"


class TestStatusMappingError:
    """Tests for status mapping errors."""

    def test_status_mapping_error(self):
        """Test status mapping error."""
        error = StatusMappingError("UNKNOWN_STATUS")
        assert "Failed to map status value: 'UNKNOWN_STATUS'" in str(error)
        assert error.raw_status == "UNKNOWN_STATUS"


class TestChargingControlError:
    """Tests for charging control errors."""

    def test_charging_control_error(self):
        """Test charging control error."""
        error = ChargingControlError("set_current", 15.5)
        assert "Charging control 'set_current' failed for current 15.5A" in str(error)
        assert error.operation == "set_current"
        assert error.current_value == 15.5


class TestValidationError:
    """Tests for validation errors."""

    def test_validation_error(self):
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

    def test_basic_session_error(self):
        """Test basic session error."""
        error = SessionError()
        assert "Charging session error" in str(error)
        assert error.session_id is None

    def test_session_error_with_id(self):
        """Test session error with session ID."""
        error = SessionError("session_123")
        assert "Charging session error (session: session_123)" in str(error)
        assert error.session_id == "session_123"


class TestRetryExhaustedException:
    """Tests for retry exhausted exceptions."""

    def test_basic_retry_exhausted(self):
        """Test basic retry exhausted exception."""
        error = RetryExhaustedException("test_operation", 3)
        assert "Operation 'test_operation' failed after 3 attempts" in str(error)
        assert error.operation == "test_operation"
        assert error.attempts == 3
        assert error.last_error is None

    def test_retry_exhausted_with_last_error(self):
        """Test retry exhausted with last error."""
        original_error = ValueError("Original error")
        error = RetryExhaustedException("test_operation", 5, original_error)
        assert "Operation 'test_operation' failed after 5 attempts" in str(error)
        assert "Original error" in str(error)
        assert error.last_error == original_error


class TestServiceUnavailableError:
    """Tests for service unavailable errors."""

    def test_service_unavailable_error(self):
        """Test service unavailable error."""
        error = ServiceUnavailableError("test_service")
        assert "Service 'test_service' is unavailable" in str(error)
        assert error.service_name == "test_service"
