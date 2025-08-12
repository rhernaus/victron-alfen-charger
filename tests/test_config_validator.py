"""Tests for configuration validation.

This module tests the configuration validator to ensure it properly
validates all configuration fields, provides helpful error messages,
and catches common configuration mistakes.
"""


import pytest

from alfen_driver.config_validator import ConfigValidator
from alfen_driver.exceptions import ConfigurationError


class TestConfigValidatorBasics:
    """Test basic validation functionality."""

    def test_valid_minimal_config(self):
        """Test validation passes for minimal valid configuration."""
        # Arrange
        validator = ConfigValidator()
        config = {"modbus": {"ip": "192.168.1.100"}}

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is True
        assert len(errors) == 0

    def test_missing_required_section(self):
        """Test validation fails when required section is missing."""
        # Arrange
        validator = ConfigValidator()
        config = {}  # Missing modbus section

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        assert any(e.field == "modbus" for e in errors)
        assert any("Required configuration section" in e.message for e in errors)

    def test_validate_or_raise_with_valid_config(self):
        """Test validate_or_raise doesn't raise for valid config."""
        # Arrange
        validator = ConfigValidator()
        config = {"modbus": {"ip": "192.168.1.100"}}

        # Act & Assert (should not raise)
        validator.validate_or_raise(config)

    def test_validate_or_raise_with_invalid_config(self):
        """Test validate_or_raise raises ConfigurationError for invalid config."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {
                # Missing required 'ip' field
            }
        }

        # Act & Assert
        with pytest.raises(ConfigurationError) as exc_info:
            validator.validate_or_raise(config)

        assert "Configuration validation failed" in str(exc_info.value)
        assert "modbus.ip" in str(exc_info.value)


class TestModbusValidation:
    """Test Modbus configuration validation."""

    def test_missing_ip_address(self):
        """Test validation fails when IP address is missing."""
        # Arrange
        validator = ConfigValidator()
        config = {"modbus": {"port": 502}}

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        error = next((e for e in errors if e.field == "modbus.ip"), None)
        assert error is not None
        assert "required" in error.message.lower()
        assert error.suggestion is not None

    def test_invalid_ip_address_format(self):
        """Test validation fails for invalid IP address format."""
        # Arrange
        validator = ConfigValidator()
        invalid_ips = ["not.an.ip", "256.256.256.256", "192.168.1", "charger.local", ""]

        for ip in invalid_ips:
            config = {"modbus": {"ip": ip}}

            # Act
            is_valid, errors = validator.validate(config)

            # Assert
            assert is_valid is False, f"Should fail for IP: {ip}"
            assert any("Invalid IP address" in e.message for e in errors)

    def test_valid_ip_addresses(self):
        """Test validation passes for valid IP addresses."""
        # Arrange
        validator = ConfigValidator()
        valid_ips = ["192.168.1.100", "10.0.0.1", "172.16.0.1", "127.0.0.1"]

        for ip in valid_ips:
            config = {"modbus": {"ip": ip}}

            # Act
            is_valid, errors = validator.validate(config)

            # Assert
            assert is_valid is True, f"Should pass for IP: {ip}"

    def test_invalid_port_range(self):
        """Test validation fails for port outside valid range."""
        # Arrange
        validator = ConfigValidator()
        invalid_ports = [0, -1, 65536, 100000]

        for port in invalid_ports:
            config = {"modbus": {"ip": "192.168.1.100", "port": port}}

            # Act
            is_valid, errors = validator.validate(config)

            # Assert
            assert is_valid is False, f"Should fail for port: {port}"
            assert any("out of valid range" in e.message for e in errors)

    def test_invalid_slave_ids(self):
        """Test validation fails for invalid slave IDs."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {
                "ip": "192.168.1.100",
                "socket_slave_id": 0,  # Invalid: too low
                "station_slave_id": 248,  # Invalid: too high
            }
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        assert any("socket_slave_id" in e.field for e in errors)
        assert any("station_slave_id" in e.field for e in errors)


class TestDefaultsValidation:
    """Test defaults configuration validation."""

    def test_invalid_current_values(self):
        """Test validation fails for current values out of range."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {"ip": "192.168.1.100"},
            "defaults": {
                "intended_set_current": -5.0,  # Negative not allowed
                "station_max_current": 100.0,  # Too high
            },
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        assert any("intended_set_current" in e.field for e in errors)
        assert any("station_max_current" in e.field for e in errors)

    def test_non_numeric_current_values(self):
        """Test validation fails for non-numeric current values."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {"ip": "192.168.1.100"},
            "defaults": {
                "intended_set_current": "16A",  # String not allowed
                "station_max_current": None,  # None not allowed
            },
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        assert any("must be a number" in e.message for e in errors)


class TestControlsValidation:
    """Test controls configuration validation."""

    def test_negative_tolerance(self):
        """Test validation fails for negative tolerance."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {"ip": "192.168.1.100"},
            "controls": {"current_tolerance": -0.5},
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        assert any("cannot be negative" in e.message for e in errors)

    def test_large_tolerance_warning(self):
        """Test validation warns about large tolerance values."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {"ip": "192.168.1.100"},
            "controls": {"current_tolerance": 10.0},  # Very large tolerance
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        # Should be valid but with warning
        assert is_valid is True
        warnings = [e for e in errors if e.severity == "warning"]
        assert len(warnings) > 0
        assert any("Large current tolerance" in w.message for w in warnings)


class TestScheduleValidation:
    """Test schedule configuration validation."""

    def test_invalid_time_format(self):
        """Test validation fails for invalid time format."""
        # Arrange
        validator = ConfigValidator()
        invalid_times = ["8:00", "25:00", "12:60", "noon", ""]

        for time_str in invalid_times:
            config = {
                "modbus": {"ip": "192.168.1.100"},
                "schedule": {
                    "items": [
                        {"active": True, "start_time": time_str, "end_time": "23:00"}
                    ]
                },
            }

            # Act
            is_valid, errors = validator.validate(config)

            # Assert
            assert is_valid is False, f"Should fail for time: {time_str}"
            assert any("Invalid time format" in e.message for e in errors)

    def test_valid_time_formats(self):
        """Test validation passes for valid time formats."""
        # Arrange
        validator = ConfigValidator()
        valid_times = ["00:00", "08:00", "12:30", "23:59"]

        for time_str in valid_times:
            config = {
                "modbus": {"ip": "192.168.1.100"},
                "schedule": {
                    "items": [
                        {
                            "active": True,
                            "start_time": time_str,
                            "end_time": "23:00",
                            "days": [0, 1, 2, 3, 4],
                        }
                    ]
                },
            }

            # Act
            is_valid, errors = validator.validate(config)

            # Assert
            assert is_valid is True, f"Should pass for time: {time_str}"

    def test_invalid_day_values(self):
        """Test validation fails for invalid day values."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {"ip": "192.168.1.100"},
            "schedule": {
                "items": [
                    {
                        "active": True,
                        "days": [-1, 7, 10],  # Invalid day numbers
                        "start_time": "08:00",
                        "end_time": "18:00",
                    }
                ]
            },
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        assert any("Invalid day value" in e.message for e in errors)


class TestGlobalSettingsValidation:
    """Test global settings validation."""

    def test_invalid_device_instance(self):
        """Test validation fails for device instance out of range."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {"ip": "192.168.1.100"},
            "device_instance": 256,  # Max is 255
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        assert any("device_instance" in e.field for e in errors)
        assert any("out of valid range" in e.message for e in errors)

    def test_very_short_poll_interval_warning(self):
        """Test validation warns about very short poll intervals."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {"ip": "192.168.1.100"},
            "poll_interval_ms": 100,  # Very short
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is True  # Valid but with warning
        warnings = [e for e in errors if e.severity == "warning"]
        assert len(warnings) > 0
        assert any("high CPU usage" in w.message for w in warnings)

    def test_invalid_timezone(self):
        """Test validation fails for invalid timezone."""
        # Arrange
        validator = ConfigValidator()
        invalid_timezones = ["Invalid/Zone", "GMT+1", ""]

        for tz in invalid_timezones:
            config = {"modbus": {"ip": "192.168.1.100"}, "timezone": tz}

            # Act
            is_valid, errors = validator.validate(config)

            # Assert
            assert is_valid is False, f"Should fail for timezone: {tz}"
            assert any("Invalid timezone" in e.message for e in errors)

    def test_valid_timezones(self):
        """Test validation passes for valid timezones."""
        # Arrange
        validator = ConfigValidator()
        valid_timezones = ["UTC", "Europe/Amsterdam", "America/New_York", "Asia/Tokyo"]

        for tz in valid_timezones:
            config = {"modbus": {"ip": "192.168.1.100"}, "timezone": tz}

            # Act
            is_valid, errors = validator.validate(config)

            # Assert
            assert is_valid is True, f"Should pass for timezone: {tz}"


class TestRelationshipValidation:
    """Test validation of relationships between configuration values."""

    def test_intended_exceeds_max_current(self):
        """Test validation fails when intended current exceeds max."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {"ip": "192.168.1.100"},
            "defaults": {"intended_set_current": 40.0},
            "controls": {"max_set_current": 32.0},
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        error = next((e for e in errors if "exceeds max" in e.message), None)
        assert error is not None
        assert error.suggestion is not None

    def test_max_set_exceeds_station_max_warning(self):
        """Test validation warns when max set exceeds station max."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {"ip": "192.168.1.100"},
            "defaults": {"station_max_current": 32.0},
            "controls": {"max_set_current": 40.0},
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is True  # Valid but with warning
        warnings = [e for e in errors if e.severity == "warning"]
        assert len(warnings) > 0
        assert any("exceeds station max" in w.message for w in warnings)


class TestLoggingValidation:
    """Test logging configuration validation."""

    def test_invalid_log_level(self):
        """Test validation fails for invalid log level."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {"ip": "192.168.1.100"},
            "logging": {"level": "VERBOSE"},  # Not a valid Python log level
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        assert any("Invalid log level" in e.message for e in errors)

    def test_valid_log_levels(self):
        """Test validation passes for valid log levels."""
        # Arrange
        validator = ConfigValidator()
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

        for level in valid_levels:
            config = {"modbus": {"ip": "192.168.1.100"}, "logging": {"level": level}}

            # Act
            is_valid, errors = validator.validate(config)

            # Assert
            assert is_valid is True, f"Should pass for level: {level}"

    def test_empty_log_file_path(self):
        """Test validation fails for empty log file path."""
        # Arrange
        validator = ConfigValidator()
        config = {"modbus": {"ip": "192.168.1.100"}, "logging": {"file": ""}}

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        assert any("cannot be empty" in e.message for e in errors)


class TestConfigSchema:
    """Test configuration schema documentation."""

    def test_get_config_schema(self):
        """Test getting configuration schema."""
        # Arrange
        validator = ConfigValidator()

        # Act
        schema = validator.get_config_schema()

        # Assert
        assert "modbus" in schema
        assert schema["modbus"]["required"] is True
        assert "fields" in schema["modbus"]
        assert "ip" in schema["modbus"]["fields"]
        assert schema["modbus"]["fields"]["ip"]["required"] is True

    def test_schema_completeness(self):
        """Test schema covers all major configuration sections."""
        # Arrange
        validator = ConfigValidator()

        # Act
        schema = validator.get_config_schema()

        # Assert
        expected_sections = ["modbus", "defaults", "controls"]
        for section in expected_sections:
            assert section in schema, f"Schema missing section: {section}"


class TestErrorMessages:
    """Test quality of error messages and suggestions."""

    def test_error_messages_have_suggestions(self):
        """Test that error messages include helpful suggestions."""
        # Arrange
        validator = ConfigValidator()
        config = {
            "modbus": {
                # Missing IP
                "port": "not_a_number",  # Wrong type
                "socket_slave_id": 500,  # Out of range
            }
        }

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False

        # Check that errors have suggestions
        for error in errors:
            if error.severity == "error":
                assert (
                    error.suggestion is not None
                ), f"Error '{error.field}' missing suggestion"
                assert (
                    len(error.suggestion) > 0
                ), f"Error '{error.field}' has empty suggestion"

    def test_error_messages_are_specific(self):
        """Test that error messages are specific and actionable."""
        # Arrange
        validator = ConfigValidator()
        config = {"modbus": {"ip": "999.999.999.999"}}

        # Act
        is_valid, errors = validator.validate(config)

        # Assert
        assert is_valid is False
        error = errors[0]

        # Check message specificity
        assert "999.999.999.999" in error.message  # Shows the bad value
        assert "Invalid IP address" in error.message  # Describes the problem
        assert "192.168" in error.suggestion  # Gives example of correct format
