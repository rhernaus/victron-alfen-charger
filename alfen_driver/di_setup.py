"""Dependency injection setup and configuration for the Alfen driver.

This module provides configuration functions for setting up the dependency
injection container with appropriate service registrations for both production
and testing environments.

The module includes:
    - Production container configuration
    - Test container configuration with mocks
    - Helper functions for common registration patterns
    - Examples of different DI scenarios

Example:
    ```python
    from alfen_driver.di_setup import configure_production_container
    from alfen_driver.injectable_driver import InjectableAlfenDriver

    # Configure for production
    container = configure_production_container("alfen_driver_config.yaml")

    # Resolve the driver with all dependencies injected
    driver = container.resolve(InjectableAlfenDriver)
    driver.start()
    ```
"""

from typing import Any, Optional, Tuple, cast

from .config import Config, load_config
from .di_container import DIContainer, ServiceLifetime
from .implementations import (
    ChargingControllerImpl,
    DBusServiceWrapper,
    FileSystemProvider,
    ModbusTcpClientWrapper,
    StructuredLoggerWrapper,
    SystemTimeProvider,
    YamlConfigProvider,
)
from .injectable_driver import InjectableAlfenDriver
from .interfaces import (
    IChargingController,
    IConfigProvider,
    IDBusService,
    IFileSystem,
    ILogger,
    IModbusClient,
    IScheduleManager,
    ITimeProvider,
)
from .mocks import (
    MockChargingController,
    MockConfigProvider,
    MockDBusService,
    MockFileSystem,
    MockLogger,
    MockModbusClient,
    MockScheduleManager,
    MockTimeProvider,
)


def configure_production_container(
    config_file: str = "alfen_driver_config.yaml", device_instance: int = 0
) -> DIContainer:
    """Configure DI container for production use.

    This function sets up the dependency injection container with real
    implementations suitable for production deployment on a Victron Venus OS
    system with an actual Alfen EV charger.

    Args:
        config_file: Path to the YAML configuration file.
        device_instance: Device instance number for D-Bus service.

    Returns:
        Configured DIContainer ready for production use.

    Example:
        ```python
        # Basic production setup
        container = configure_production_container()
        driver = container.resolve(InjectableAlfenDriver)

        # Custom configuration file
        container = configure_production_container(
            config_file="/data/custom_config.yaml",
            device_instance=1
        )
        ```
    """
    container = DIContainer()

    # Load configuration
    config = load_config(config_file)
    container.register_instance(Config, config)

    # Register core services as singletons
    container.register_factory(
        IModbusClient,
        lambda: ModbusTcpClientWrapper(config.modbus.ip, config.modbus.port),
        ServiceLifetime.SINGLETON,
    )

    container.register_factory(
        IDBusService,
        lambda: DBusServiceWrapper(
            f"com.victronenergy.evcharger.alfen_{device_instance}", device_instance
        ),
        ServiceLifetime.SINGLETON,
    )

    container.register_factory(
        ILogger,
        lambda: StructuredLoggerWrapper("alfen_driver", config),
        ServiceLifetime.SINGLETON,
    )

    # Register utility services
    container.register(ITimeProvider, SystemTimeProvider, ServiceLifetime.SINGLETON)
    container.register(IFileSystem, FileSystemProvider, ServiceLifetime.SINGLETON)

    container.register_factory(
        IConfigProvider,
        lambda: YamlConfigProvider(config_file),
        ServiceLifetime.SINGLETON,
    )

    # Register business logic services
    container.register(
        IChargingController, ChargingControllerImpl, ServiceLifetime.SINGLETON
    )

    # Register the injectable driver
    container.register(
        InjectableAlfenDriver, InjectableAlfenDriver, ServiceLifetime.SINGLETON
    )

    return container


def configure_test_container(
    use_real_config: bool = False, config_dict: Optional[dict] = None
) -> DIContainer:
    """Configure DI container for testing.

    This function sets up the dependency injection container with mock
    implementations suitable for unit testing without hardware dependencies.

    Args:
        use_real_config: If True, loads real configuration file.
        config_dict: Optional configuration dictionary for testing.

    Returns:
        Configured DIContainer with mock implementations.

    Example:
        ```python
        # Basic test setup with mocks
        container = configure_test_container()

        # Test with custom configuration
        test_config = {
            'modbus': {'ip': 'test.host', 'port': 502},
            'defaults': {'intended_set_current': 10.0}
        }
        container = configure_test_container(config_dict=test_config)

        # Get mock services for test manipulation
        mock_modbus = container.resolve(IModbusClient)
        mock_modbus.set_register_values(306, [0x4316, 0x8000])
        ```
    """
    container = DIContainer()

    # Configure test configuration
    if use_real_config:
        config = load_config("alfen_driver_config.yaml")
    elif config_dict:
        config = Config.from_dict(config_dict)
    else:
        # Use default test configuration
        config = Config.from_dict(MockConfigProvider._get_default_config())

    container.register_instance(Config, config)

    # Register mock services
    container.register(IModbusClient, MockModbusClient, ServiceLifetime.SINGLETON)
    container.register(IDBusService, MockDBusService, ServiceLifetime.SINGLETON)
    container.register(ILogger, MockLogger, ServiceLifetime.SINGLETON)
    container.register(ITimeProvider, MockTimeProvider, ServiceLifetime.SINGLETON)
    container.register(IFileSystem, MockFileSystem, ServiceLifetime.SINGLETON)
    container.register(IConfigProvider, MockConfigProvider, ServiceLifetime.SINGLETON)
    container.register(
        IChargingController, MockChargingController, ServiceLifetime.SINGLETON
    )
    container.register(IScheduleManager, MockScheduleManager, ServiceLifetime.SINGLETON)

    # Register the injectable driver
    container.register(
        InjectableAlfenDriver, InjectableAlfenDriver, ServiceLifetime.TRANSIENT
    )

    return container


def configure_hybrid_container(
    use_real_modbus: bool = False,
    use_real_dbus: bool = False,
    config_file: str = "alfen_driver_config.yaml",
) -> DIContainer:
    """Configure DI container with mix of real and mock implementations.

    This function is useful for integration testing where you want to test
    with some real components while mocking others.

    Args:
        use_real_modbus: If True, use real Modbus client.
        use_real_dbus: If True, use real D-Bus service.
        config_file: Path to configuration file.

    Returns:
        Configured DIContainer with hybrid implementations.

    Example:
        ```python
        # Test with real Modbus but mock D-Bus
        container = configure_hybrid_container(use_real_modbus=True)

        # Test with real D-Bus but mock Modbus
        container = configure_hybrid_container(use_real_dbus=True)
        ```
    """
    container = DIContainer()

    # Load configuration
    config = load_config(config_file)
    container.register_instance(Config, config)

    # Conditionally register real or mock Modbus
    if use_real_modbus:
        container.register_factory(
            IModbusClient,
            lambda: ModbusTcpClientWrapper(config.modbus.ip, config.modbus.port),
            ServiceLifetime.SINGLETON,
        )
    else:
        container.register(IModbusClient, MockModbusClient, ServiceLifetime.SINGLETON)

    # Conditionally register real or mock D-Bus
    if use_real_dbus:
        container.register_factory(
            IDBusService,
            lambda: DBusServiceWrapper("com.victronenergy.evcharger.alfen_test", 99),
            ServiceLifetime.SINGLETON,
        )
    else:
        container.register(IDBusService, MockDBusService, ServiceLifetime.SINGLETON)

    # Always use mock logger and time provider for testing
    container.register(ILogger, MockLogger, ServiceLifetime.SINGLETON)
    container.register(ITimeProvider, MockTimeProvider, ServiceLifetime.SINGLETON)

    # Use real file system and config provider
    container.register(IFileSystem, FileSystemProvider, ServiceLifetime.SINGLETON)
    container.register_factory(
        IConfigProvider,
        lambda: YamlConfigProvider(config_file),
        ServiceLifetime.SINGLETON,
    )

    # Use real charging controller
    container.register(
        IChargingController, ChargingControllerImpl, ServiceLifetime.SINGLETON
    )

    # Register driver
    container.register(
        InjectableAlfenDriver, InjectableAlfenDriver, ServiceLifetime.SINGLETON
    )

    return container


def create_minimal_container() -> DIContainer:
    """Create a minimal container with only essential services.

    This is useful for quick testing or when you want to manually
    configure specific services.

    Returns:
        Minimal DIContainer with basic mock services.

    Example:
        ```python
        container = create_minimal_container()

        # Manually register specific implementations
        container.register_instance(IModbusClient, my_custom_client)
        container.register(ILogger, MyCustomLogger)

        driver = container.resolve(InjectableAlfenDriver)
        ```
    """
    container = DIContainer()

    # Minimal test configuration
    config = Config.from_dict(
        {
            "modbus": {"ip": "localhost", "port": 502},
            "defaults": {"intended_set_current": 6.0},
            "device_instance": 0,
            "poll_interval_ms": 1000,
        }
    )
    container.register_instance(Config, config)

    # Register only essential mocks
    container.register(IModbusClient, MockModbusClient)
    container.register(IDBusService, MockDBusService)
    container.register(ILogger, MockLogger)
    container.register(ITimeProvider, MockTimeProvider)

    return container


# Example usage functions
def example_production_setup() -> None:
    """Example of setting up the driver for production use."""
    # Configure container for production
    container = configure_production_container(
        config_file="/data/alfen_driver_config.yaml", device_instance=0
    )

    # Resolve driver with all dependencies automatically injected
    driver = container.resolve(InjectableAlfenDriver)

    # Start the driver
    driver.start()


def example_test_setup() -> Tuple[InjectableAlfenDriver, DIContainer]:
    """Example of setting up the driver for testing."""
    # Configure container for testing
    container = configure_test_container()

    # Get mock services for test setup
    mock_modbus: MockModbusClient = container.resolve(IModbusClient)
    mock_logger: MockLogger = container.resolve(ILogger)
    mock_time: MockTimeProvider = container.resolve(ITimeProvider)

    # Configure mock behavior
    mock_modbus.set_register_values(306, [0x4316, 0x8000])  # Voltage: 230.5V
    mock_time.set_auto_advance(True)

    # Create driver instance
    driver = cast(InjectableAlfenDriver, container.resolve(InjectableAlfenDriver))

    # Run test scenarios
    driver.get_status()

    # Verify behavior through mocks
    assert mock_logger.has_message("Injectable Alfen driver initialized")
    assert mock_modbus.get_read_count() > 0

    return driver, container


def example_custom_registration() -> InjectableAlfenDriver:
    """Example of custom service registration."""
    container = DIContainer()

    # Register a custom logger implementation
    class CustomLogger(ILogger):
        def debug(self, message: str, **kwargs: Any) -> None:
            print(f"[DEBUG] {message}")

        def info(self, message: str, **kwargs: Any) -> None:
            print(f"[INFO] {message}")

        def warning(self, message: str, **kwargs: Any) -> None:
            print(f"[WARN] {message}")

        def error(self, message: str, **kwargs: Any) -> None:
            print(f"[ERROR] {message}")

        def exception(self, message: str, **kwargs: Any) -> None:
            print(f"[EXCEPTION] {message}")

    # Register custom implementation
    container.register(ILogger, CustomLogger, ServiceLifetime.SINGLETON)

    # Register other services
    container.register(IModbusClient, MockModbusClient)
    container.register(IDBusService, MockDBusService)
    container.register(ITimeProvider, SystemTimeProvider)

    # Create configuration
    config = Config.from_dict(MockConfigProvider._get_default_config())
    container.register_instance(Config, config)

    # Resolve driver with custom logger
    driver = cast(InjectableAlfenDriver, container.resolve(InjectableAlfenDriver))

    return driver
