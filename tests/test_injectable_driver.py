"""Tests for the injectable Alfen driver with dependency injection.

This test module demonstrates the improved testability achieved through
dependency injection. Tests use mock implementations to verify driver
behavior without requiring actual hardware or system dependencies.
"""

from unittest.mock import Mock

from alfen_driver.config import Config
from alfen_driver.dbus_utils import EVC_CHARGE, EVC_MODE, EVC_STATUS
from alfen_driver.di_container import DIContainer, ServiceLifetime
from alfen_driver.di_setup import configure_test_container
from alfen_driver.injectable_driver import InjectableAlfenDriver
from alfen_driver.mocks import (
    MockChargingController,
    MockDBusService,
    MockLogger,
    MockModbusClient,
    MockTimeProvider,
)


class TestInjectableDriverInitialization:
    """Test driver initialization with dependency injection."""

    def test_driver_initialization_with_di_container(self) -> None:
        """Test driver can be initialized through DI container."""
        # Arrange
        container = configure_test_container()

        # Act
        driver = container.resolve(InjectableAlfenDriver)

        # Assert
        assert driver is not None
        assert driver.session_id is not None
        assert driver.modbus_client is not None
        assert driver.dbus_service is not None
        assert driver.logger is not None

    def test_driver_initialization_with_manual_injection(self) -> None:
        """Test driver can be initialized with manual dependency injection."""
        # Arrange
        mock_modbus = MockModbusClient(connected=True)
        mock_dbus = MockDBusService("test.service")
        mock_logger = MockLogger()
        mock_time = MockTimeProvider()
        config = Config.from_dict(
            {
                "modbus": {"ip": "test", "port": 502},
                "defaults": {"intended_set_current": 6.0, "station_max_current": 32.0},
                "device_instance": 0,
                "poll_interval_ms": 1000,
            }
        )

        # Act
        driver = InjectableAlfenDriver(
            modbus_client=mock_modbus,
            dbus_service=mock_dbus,
            logger=mock_logger,
            time_provider=mock_time,
            config=config,
        )

        # Assert
        assert driver.modbus_client == mock_modbus
        assert driver.dbus_service == mock_dbus
        assert driver.logger == mock_logger
        assert driver.time_provider == mock_time
        assert driver.config == config

    def test_driver_initializes_dbus_paths(self) -> None:
        """Test driver correctly initializes D-Bus paths."""
        # Arrange
        container = configure_test_container()
        mock_dbus = container.resolve(MockDBusService)

        # Act
        container.resolve(InjectableAlfenDriver)

        # Assert
        paths = mock_dbus.get_all_paths()
        assert "/Ac/Power" in paths
        assert "/Ac/Energy/Forward" in paths
        assert "/Current" in paths
        assert "/Status" in paths
        assert "/Mode" in paths
        assert paths["/Status"] == EVC_STATUS.DISCONNECTED.value

    def test_driver_logs_initialization(self) -> None:
        """Test driver logs initialization properly."""
        # Arrange
        container = configure_test_container()
        mock_logger = container.resolve(MockLogger)

        # Act
        container.resolve(InjectableAlfenDriver)

        # Assert
        assert mock_logger.has_message("Injectable Alfen driver initialized", "INFO")
        assert mock_logger.has_message("D-Bus paths initialized successfully", "INFO")


class TestDriverDataReading:
    """Test driver data reading functionality with mocks."""

    def test_read_voltages(self) -> None:
        """Test reading voltage values from Modbus."""
        # Arrange
        container = configure_test_container()
        mock_modbus = container.resolve(MockModbusClient)
        mock_dbus = container.resolve(MockDBusService)

        # Set mock voltage values (230V per phase)
        mock_modbus.set_register_values(306, [0x4366, 0x6666] * 3, slave=1)

        driver = container.resolve(InjectableAlfenDriver)

        # Act
        driver._update_charger_data()

        # Assert
        voltage = mock_dbus.get_value("/Ac/Voltage")
        assert voltage is not None
        assert voltage > 0

    def test_read_currents(self) -> None:
        """Test reading current values from Modbus."""
        # Arrange
        container = configure_test_container()
        mock_modbus = container.resolve(MockModbusClient)
        mock_dbus = container.resolve(MockDBusService)

        # Set mock current values (10A per phase)
        mock_modbus.set_register_values(320, [0x4120, 0x0000] * 3, slave=1)

        driver = container.resolve(InjectableAlfenDriver)

        # Act
        driver._update_charger_data()

        # Assert
        current = mock_dbus.get_value("/Ac/Current")
        assert current is not None
        assert mock_modbus.get_read_count() > 0

    def test_handle_modbus_read_error(self) -> None:
        """Test driver handles Modbus read errors gracefully."""
        # Arrange
        container = configure_test_container()
        mock_modbus = container.resolve(MockModbusClient)
        mock_logger = container.resolve(MockLogger)

        # Configure mock to fail
        mock_modbus.fail_next_read()

        driver = container.resolve(InjectableAlfenDriver)

        # Act
        driver._update_charger_data()

        # Assert
        assert mock_logger.has_message("Failed to update charger data", "ERROR")


class TestChargingControl:
    """Test charging control functionality with dependency injection."""

    def test_set_charging_current(self) -> None:
        """Test setting charging current through controller."""
        # Arrange
        container = configure_test_container()
        container.resolve(MockChargingController)
        mock_dbus = container.resolve(MockDBusService)
        mock_logger = container.resolve(MockLogger)

        driver = container.resolve(InjectableAlfenDriver)

        # Act
        driver._set_charging_current(10.0)

        # Assert
        assert mock_dbus.get_value("/SetCurrent") == 10.0
        assert mock_logger.has_message("Charging current updated", "INFO")

    def test_mode_change_callback(self) -> None:
        """Test mode change callback handling."""
        # Arrange
        container = configure_test_container()
        mock_dbus = container.resolve(MockDBusService)
        mock_logger = container.resolve(MockLogger)

        container.resolve(InjectableAlfenDriver)

        # Act
        mock_dbus.trigger_callback("/Mode", EVC_MODE.MANUAL.value)

        # Assert
        assert mock_logger.has_message("Charging mode changed", "INFO")

    def test_start_stop_callback(self) -> None:
        """Test start/stop callback handling."""
        # Arrange
        container = configure_test_container()
        mock_dbus = container.resolve(MockDBusService)
        mock_logger = container.resolve(MockLogger)

        container.resolve(InjectableAlfenDriver)

        # Act
        mock_dbus.trigger_callback("/StartStop", EVC_CHARGE.DISABLED.value)

        # Assert
        assert mock_logger.has_message("Start/stop changed", "INFO")

    def test_calculate_auto_current(self) -> None:
        """Test auto mode current calculation."""
        # Arrange
        container = configure_test_container()
        driver = container.resolve(InjectableAlfenDriver)

        # Act
        current = driver._calculate_auto_current()

        # Assert
        assert current == driver.config.defaults.intended_set_current

    def test_calculate_scheduled_current(self) -> None:
        """Test scheduled mode current calculation."""
        # Arrange
        container = configure_test_container()
        mock_time = container.resolve(MockTimeProvider)
        mock_time.set_time(1000.0)

        driver = container.resolve(InjectableAlfenDriver)

        # Act
        current = driver._calculate_scheduled_current(mock_time.now())

        # Assert
        assert isinstance(current, float)
        assert current >= 0


class TestDriverStatus:
    """Test driver status reporting functionality."""

    def test_get_driver_status(self) -> None:
        """Test getting comprehensive driver status."""
        # Arrange
        container = configure_test_container()
        mock_modbus = container.resolve(MockModbusClient)
        mock_modbus.connect()

        driver = container.resolve(InjectableAlfenDriver)

        # Act
        status = driver.get_status()

        # Assert
        assert "session_id" in status
        assert "modbus_connected" in status
        assert "charging_status" in status
        assert "config" in status
        assert status["modbus_connected"] is True

    def test_get_status_with_error(self) -> None:
        """Test status reporting when errors occur."""
        # Arrange
        container = configure_test_container()
        mock_controller = container.resolve(MockChargingController)
        mock_logger = container.resolve(MockLogger)

        # Make controller fail
        mock_controller.fail_next_operation()

        driver = container.resolve(InjectableAlfenDriver)
        driver.charging_controller = Mock(side_effect=Exception("Test error"))

        # Act
        status = driver.get_status()

        # Assert
        assert "error" in status
        assert status["status"] == "error"
        assert mock_logger.has_message("Failed to get driver status", "ERROR")


class TestDriverCleanup:
    """Test driver cleanup and resource management."""

    def test_cleanup_on_shutdown(self) -> None:
        """Test driver properly cleans up resources on shutdown."""
        # Arrange
        container = configure_test_container()
        mock_modbus = container.resolve(MockModbusClient)
        mock_modbus.connect()
        mock_logger = container.resolve(MockLogger)

        driver = container.resolve(InjectableAlfenDriver)

        # Act
        driver._cleanup()

        # Assert
        assert not mock_modbus.is_connected()
        assert mock_logger.has_message("Driver cleanup completed", "INFO")

    def test_cleanup_sets_current_to_zero(self) -> None:
        """Test cleanup stops charging by setting current to zero."""
        # Arrange
        container = configure_test_container()
        mock_modbus = container.resolve(MockModbusClient)
        mock_modbus.connect()
        mock_controller = container.resolve(MockChargingController)

        driver = container.resolve(InjectableAlfenDriver)
        driver._set_charging_current(10.0)

        # Act
        driver._cleanup()

        # Assert
        status = mock_controller.get_charging_status()
        assert status["total_current"] == 0
        assert status["status"] == "idle"


class TestDependencyInjectionBenefits:
    """Test scenarios demonstrating DI benefits for testing."""

    def test_time_dependent_behavior(self) -> None:
        """Test time-dependent behavior with mock time provider."""
        # Arrange
        container = configure_test_container()
        mock_time = container.resolve(MockTimeProvider)
        mock_time.set_time(1000.0)

        driver = container.resolve(InjectableAlfenDriver)

        # Act - Simulate time progression
        initial_time = mock_time.now()
        mock_time.advance(60.0)  # Advance 60 seconds
        later_time = mock_time.now()

        # Assert
        assert later_time == initial_time + 60.0
        assert driver.time_provider.now() == later_time

    def test_modbus_failure_scenarios(self) -> None:
        """Test various Modbus failure scenarios."""
        # Arrange
        container = configure_test_container()
        mock_modbus = container.resolve(MockModbusClient)
        mock_logger = container.resolve(MockLogger)

        driver = container.resolve(InjectableAlfenDriver)

        # Test connection failure
        mock_modbus.close()
        assert not mock_modbus.is_connected()

        # Test read failure
        mock_modbus.connect()
        mock_modbus.fail_next_read()
        driver._update_charger_data()

        # Assert
        assert mock_logger.has_message("Failed to update charger data", "ERROR")

    def test_isolated_component_testing(self) -> None:
        """Test driver components in isolation using mocks."""
        # Arrange
        mock_modbus = MockModbusClient()
        mock_dbus = MockDBusService()
        mock_logger = MockLogger()
        mock_time = MockTimeProvider()
        mock_controller = MockChargingController()

        config = Config.from_dict(
            {
                "modbus": {"ip": "test", "port": 502},
                "defaults": {"intended_set_current": 16.0, "station_max_current": 32.0},
                "device_instance": 0,
                "poll_interval_ms": 500,
            }
        )

        # Act - Test specific component interaction
        driver = InjectableAlfenDriver(
            modbus_client=mock_modbus,
            dbus_service=mock_dbus,
            logger=mock_logger,
            time_provider=mock_time,
            config=config,
            charging_controller=mock_controller,
        )

        # Test charging control in isolation
        driver._set_charging_current(16.0)

        # Assert
        assert mock_dbus.get_value("/SetCurrent") == 16.0
        status = mock_controller.get_charging_status()
        assert status["status"] == "charging"

    def test_swappable_implementations(self) -> None:
        """Test ability to swap implementations for different test scenarios."""
        # Arrange
        container = DIContainer()

        # Register test-specific implementations
        container.register(MockModbusClient, MockModbusClient)
        container.register(MockDBusService, MockDBusService)
        container.register(MockLogger, MockLogger)
        container.register(MockTimeProvider, MockTimeProvider)

        config = Config.from_dict({"modbus": {"ip": "test", "port": 502}})
        container.register_instance(Config, config)

        # Create custom mock with specific behavior
        class FailingModbusClient(MockModbusClient):
            def connect(self) -> bool:
                return False  # Always fail to connect

        # Swap implementation for specific test
        container.register(
            MockModbusClient, FailingModbusClient, ServiceLifetime.TRANSIENT
        )

        # Act
        mock_modbus = container.resolve(MockModbusClient)

        # Assert
        assert not mock_modbus.connect()  # Custom behavior verified
