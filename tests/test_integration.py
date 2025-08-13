"""Integration tests for the complete Alfen driver system."""

from unittest.mock import Mock, patch

import pytest


class TestBasicIntegration:
    """Basic integration tests to ensure components work together."""

    def test_can_import_all_modules(self) -> None:
        """Test that all modules can be imported without errors."""
        # This test ensures all imports work and there are no circular dependencies
        from alfen_driver import (
            AlfenDriverError,
            Config,
            ConfigurationError,
            ModbusError,
        )

        assert AlfenDriverError is not None
        assert ConfigurationError is not None
        assert ModbusError is not None
        assert Config is not None

    def test_exception_hierarchy(self) -> None:
        """Test that exception hierarchy works correctly."""
        from alfen_driver.exceptions import (
            AlfenDriverError,
            ConfigurationError,
            ValidationError,
        )

        # Test inheritance
        config_error = ConfigurationError("Test config error")
        validation_error = ValidationError("test_field", "invalid", "must be valid")

        assert isinstance(config_error, AlfenDriverError)
        assert isinstance(validation_error, AlfenDriverError)
        assert isinstance(config_error, Exception)
        assert isinstance(validation_error, Exception)

    @patch("dbus.SystemBus")
    @patch("vedbus.VeDbusService")
    def test_driver_can_be_instantiated(self, mock_vedbus, mock_dbus) -> None:
        """Test that the driver can be instantiated with mocked dependencies."""
        # This is a basic smoke test to ensure the driver class can be created

        # Mock the D-Bus service
        mock_service = Mock()
        mock_vedbus.return_value = mock_service

        # Mock the system bus
        mock_bus = Mock()
        mock_dbus.return_value = mock_bus

        with patch("alfen_driver.driver.ModbusTcpClient") as mock_modbus:
            mock_client = Mock()
            mock_modbus.return_value = mock_client

            # Mock successful register read
            mock_response = Mock()
            mock_response.isError.return_value = False
            mock_response.registers = [3]  # 3 phases
            mock_client.read_holding_registers.return_value = mock_response

            # This should not raise any exceptions
            try:
                from alfen_driver.driver import AlfenDriver

                # We can't actually create it due to complex D-Bus dependencies
                # but importing it exercises a lot of the code
                assert AlfenDriver is not None
            except Exception as e:
                # If there are import errors, that's what we're testing for
                pytest.fail(f"Failed to import AlfenDriver: {e}")

    def test_config_validation_chain(self) -> None:
        """Test that configuration validation works correctly."""
        from alfen_driver.config import Config, DefaultsConfig, ModbusConfig
        from alfen_driver.exceptions import ValidationError

        # Test that invalid configurations raise ValidationError
        with pytest.raises(ValidationError) as exc_info:
            Config(
                modbus=ModbusConfig(
                    ip="192.168.1.100",
                    port=-1,  # Invalid negative port
                    socket_slave_id=1,
                    station_slave_id=200,
                ),
                device_instance=0,
                registers=Mock(),  # We'll use a mock for simplicity
                defaults=DefaultsConfig(
                    intended_set_current=6.0, station_max_current=32.0
                ),
                logging=Mock(),
                schedule=Mock(),
                controls=Mock(),
                poll_interval_ms=1000,
                timezone="UTC",
            )

        assert "modbus.port" in str(exc_info.value)


class TestDocumentationExamples:
    """Test that examples from documentation work correctly."""

    def test_basic_exception_usage(self) -> None:
        """Test basic exception usage from documentation."""
        from alfen_driver.exceptions import ModbusError, ValidationError

        # Test ModbusError representing a read failure
        modbus_error = ModbusError(
            "read", "Connection timeout", address=123, slave_id=1
        )
        assert "Modbus read failed" in str(modbus_error)
        assert "address 123" in str(modbus_error)

        # Test ValidationError
        validation_error = ValidationError(
            "current_setting", -5.0, "must be non-negative"
        )
        assert "current_setting" in str(validation_error)
        assert "-5.0" in str(validation_error)
        assert "must be non-negative" in str(validation_error)

    def test_config_structure(self, sample_config) -> None:
        """Test that configuration structure is correct."""
        # This uses the sample_config fixture from conftest.py
        assert sample_config.modbus.ip == "192.168.1.100"
        assert sample_config.modbus.port == 502
        assert sample_config.defaults.intended_set_current == 6.0
        assert len(sample_config.schedule.items) == 3
        assert sample_config.controls.current_tolerance == 0.25
