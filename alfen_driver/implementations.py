"""Concrete implementations of interfaces for dependency injection.

This module provides concrete implementations of all interfaces defined in the
interfaces module. These implementations wrap the existing driver functionality
to enable dependency injection while maintaining backward compatibility.

The implementations include:
    - ModbusTcpClientWrapper: Wraps pymodbus.ModbusTcpClient
    - DBusServiceWrapper: Wraps vedbus.VeDbusService
    - StructuredLoggerWrapper: Wraps the structured logging system
    - SystemTimeProvider: Provides real system time
    - FileSystemProvider: Provides real file system operations
    - YamlConfigProvider: Loads YAML configuration files

Example:
    ```python
    from alfen_driver.implementations import ModbusTcpClientWrapper
    from alfen_driver.interfaces import IModbusClient

    # Direct instantiation
    client = ModbusTcpClientWrapper("192.168.1.100", 502)

    # Or via dependency injection
    container.register(IModbusClient, ModbusTcpClientWrapper)
    ```
"""

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, cast

import yaml
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
from pymodbus.pdu import ModbusResponse

from .config import Config
from .exceptions import (
    ChargingControlError,
    ConfigurationError,
    DBusError,
    ModbusReadError,
    ModbusWriteError,
)
from .interfaces import (
    IChargingController,
    IConfigProvider,
    IDBusService,
    IFileSystem,
    ILogger,
    IModbusClient,
    ITimeProvider,
)
from .logging_utils import get_logger


class ModbusTcpClientWrapper(IModbusClient):
    """Wrapper for pymodbus.ModbusTcpClient implementing IModbusClient.

    This wrapper provides a clean interface over the pymodbus library,
    handling error conversion and providing consistent behavior for
    dependency injection.

    Attributes:
        _client: The underlying pymodbus.ModbusTcpClient instance.
        _host: The Modbus server host address.
        _port: The Modbus server port.
    """

    def __init__(self, host: str, port: int = 502):
        """Initialize the Modbus client wrapper.

        Args:
            host: IP address or hostname of the Modbus server.
            port: TCP port for Modbus communication (default: 502).
        """
        self._host = host
        self._port = port
        self._client = ModbusTcpClient(host=host, port=port)

    def connect(self) -> bool:
        """Establish connection to the Modbus server."""
        try:
            return bool(self._client.connect())
        except Exception:
            return False

    def close(self) -> None:
        """Close the Modbus connection."""
        try:
            self._client.close()
        except Exception as e:
            # Log close errors but don't raise
            import logging

            logging.getLogger(__name__).debug(f"Error closing Modbus client: {e}")

    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        return bool(getattr(self._client, "is_socket_open", lambda: False)())

    def read_holding_registers(self, address: int, count: int, slave: int) -> List[int]:
        """Read holding registers from the Modbus device."""
        try:
            result = cast(
                ModbusResponse,
                self._client.read_holding_registers(address, count, slave=slave),
            )
            if result.isError():
                raise ModbusReadError(address, count, slave, str(result))
            return list(result.registers)
        except ModbusException as e:
            raise ModbusReadError(address, count, slave, str(e)) from e

    def write_register(self, address: int, value: int, slave: int) -> bool:
        """Write a single register to the Modbus device."""
        try:
            result = cast(
                ModbusResponse,
                self._client.write_register(address, value, slave=slave),
            )
            if result.isError():
                raise ModbusWriteError(address, value, slave, str(result))
            return True
        except ModbusException as e:
            raise ModbusWriteError(address, value, slave, str(e)) from e

    def write_registers(self, address: int, values: List[int], slave: int) -> bool:
        """Write multiple registers to the Modbus device."""
        try:
            result = cast(
                ModbusResponse,
                self._client.write_registers(address, values, slave=slave),
            )
            if result.isError():
                raise ModbusWriteError(address, values, slave, str(result))
            return True
        except ModbusException as e:
            raise ModbusWriteError(address, values, slave, str(e)) from e

    @property
    def host(self) -> str:
        """Get the Modbus server host address."""
        return self._host

    @property
    def port(self) -> int:
        """Get the Modbus server port."""
        return self._port


class DBusServiceWrapper(IDBusService):
    """Wrapper for vedbus.VeDbusService implementing IDBusService.

    This wrapper provides a testable interface over the Victron D-Bus service
    implementation, handling errors and providing consistent behavior.
    """

    def __init__(self, service_name: str, device_instance: int = 0):
        """Initialize the D-Bus service wrapper.

        Args:
            service_name: The D-Bus service name.
            device_instance: The device instance number.
        """
        self._service_name = service_name
        self._device_instance = device_instance
        self._paths: Dict[str, Any] = {}
        self._callbacks: Dict[str, Any] = {}

        # Try to import and initialize vedbus
        self._service = None
        try:
            import vedbus

            self._service = vedbus.VeDbusService(service_name)
        except ImportError:
            # D-Bus not available (testing environment)
            pass
        except Exception as e:
            raise DBusError(service_name, details=str(e)) from e

    def add_path(self, path: str, value: Any) -> None:
        """Add a path to the D-Bus service."""
        self._paths[path] = value

        if self._service:
            try:
                self._service.add_path(path, value)
            except Exception as e:
                raise DBusError(self._service_name, path, str(e)) from e

    def set_value(self, path: str, value: Any) -> None:
        """Set a value for a D-Bus path."""
        self._paths[path] = value

        if self._service:
            try:
                self._service[path] = value
            except Exception as e:
                raise DBusError(self._service_name, path, str(e)) from e

    def get_value(self, path: str) -> Any:
        """Get the current value of a D-Bus path."""
        if self._service:
            try:
                return self._service[path]
            except Exception as e:
                raise DBusError(self._service_name, path, str(e)) from e

        return self._paths.get(path)

    def register_callback(
        self, path: str, callback: Callable[[str, Any], None]
    ) -> None:
        """Register a callback for path changes."""
        self._callbacks[path] = callback

        if self._service and hasattr(self._service, "add_path"):
            try:
                self._service.add_path(
                    path, None, writeable=True, onchangecallback=callback
                )
            except Exception as e:
                raise DBusError(self._service_name, path, str(e)) from e


class StructuredLoggerWrapper(ILogger):
    """Wrapper for StructuredLogger implementing ILogger interface.

    This wrapper provides a simplified interface over the structured logging
    system for dependency injection purposes.
    """

    def __init__(self, name: str, config: Optional[Config] = None):
        """Initialize the logger wrapper.

        Args:
            name: Logger name.
            config: Optional configuration for structured logging.
        """
        self._logger = get_logger(name, config)

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log a debug message."""
        self._logger.debug(message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log an info message."""
        self._logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log a warning message."""
        self._logger.warning(message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log an error message."""
        self._logger.error(message, **kwargs)

    def exception(self, message: str, **kwargs: Any) -> None:
        """Log an exception with traceback."""
        self._logger.exception(message, **kwargs)


class SystemTimeProvider(ITimeProvider):
    """System time provider implementing ITimeProvider.

    This implementation provides real system time operations.
    For testing, this can be replaced with a mock implementation
    that provides controlled time values.
    """

    def now(self) -> float:
        """Get the current time as a timestamp."""
        return time.time()

    def sleep(self, duration: float) -> None:
        """Sleep for the specified duration."""
        time.sleep(duration)


class FileSystemProvider(IFileSystem):
    """Real file system provider implementing IFileSystem.

    This implementation provides access to the actual file system.
    For testing, this can be replaced with an in-memory implementation.
    """

    def read_text(self, file_path: str) -> str:
        """Read text content from a file."""
        try:
            with open(file_path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            raise
        except Exception as e:
            raise OSError(f"Failed to read {file_path}: {e}") from e

    def write_text(self, file_path: str, content: str) -> None:
        """Write text content to a file."""
        try:
            # Ensure directory exists
            directory = os.path.dirname(file_path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            raise OSError(f"Failed to write {file_path}: {e}") from e

    def exists(self, file_path: str) -> bool:
        """Check if a file exists."""
        return os.path.exists(file_path)

    def create_directory(self, dir_path: str) -> None:
        """Create a directory and any necessary parent directories."""
        try:
            os.makedirs(dir_path, exist_ok=True)
        except Exception as e:
            raise OSError(f"Failed to create directory {dir_path}: {e}") from e


class YamlConfigProvider(IConfigProvider):
    """YAML configuration provider implementing IConfigProvider.

    This implementation loads configuration from YAML files and provides
    JSON persistence for runtime configuration changes.
    """

    def __init__(self, config_file_path: str, logger: Optional[ILogger] = None):
        """Initialize the YAML config provider.

        Args:
            config_file_path: Path to the YAML configuration file.
            logger: Optional logger for configuration operations.
        """
        self._config_path = config_file_path
        self._logger = logger or StructuredLoggerWrapper("config_provider")
        self._file_system = FileSystemProvider()

    def load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        if not self._file_system.exists(self._config_path):
            raise ConfigurationError(
                f"Configuration file not found: {self._config_path}"
            )

        try:
            content = self._file_system.read_text(self._config_path)
            config = yaml.safe_load(content)

            if not isinstance(config, dict):
                raise ConfigurationError(
                    f"Configuration must be a dictionary, got {type(config)}"
                )

            self._logger.info(
                "Configuration loaded successfully", config_path=self._config_path
            )
            return config

        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML in {self._config_path}: {e}") from e
        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration: {e}") from e

    def save_config(self, config: Dict[str, Any]) -> None:
        """Save configuration to JSON file (for runtime persistence)."""
        # Save as JSON for runtime persistence (YAML for human editing)
        json_path = self._config_path.replace(".yaml", ".json").replace(".yml", ".json")

        try:
            content = json.dumps(config, indent=2, sort_keys=True)
            self._file_system.write_text(json_path, content)

            self._logger.info("Configuration saved successfully", config_path=json_path)
        except Exception as e:
            raise ConfigurationError(f"Failed to save configuration: {e}") from e

    def get_config_path(self) -> str:
        """Get the path to the configuration file."""
        return self._config_path


class ChargingControllerImpl(IChargingController):
    """Charging controller implementation.

    This implementation provides the core charging control functionality
    using the injected Modbus client and configuration.
    """

    def __init__(self, modbus_client: IModbusClient, config: Config, logger: ILogger):
        """Initialize the charging controller.

        Args:
            modbus_client: Modbus client for communication with charger.
            config: Driver configuration.
            logger: Logger for operations.
        """
        self._modbus_client = modbus_client
        self._config = config
        self._logger = logger

    def set_charging_current(self, current: float, verify: bool = True) -> bool:
        """Set the charging current."""
        try:
            # Validate current range
            if current < 0 or current > self._config.controls.max_set_current:
                raise ChargingControlError(
                    "set_current",
                    current,
                    f"Current must be between 0 and "
                    f"{self._config.controls.max_set_current}A",
                )

            # Convert current to register value (implementation specific)
            register_value = int(current * 10)  # Example: 12.5A -> 125

            # Write to charger
            success = self._modbus_client.write_register(
                self._config.registers.amps_config,
                register_value,
                self._config.modbus.station_slave_id,
            )

            if verify and success:
                # Verify the setting was applied
                actual_values = self._modbus_client.read_holding_registers(
                    self._config.registers.amps_config,
                    1,
                    self._config.modbus.station_slave_id,
                )
                actual_current = actual_values[0] / 10.0

                if (
                    abs(actual_current - current)
                    > self._config.controls.current_tolerance
                ):
                    raise ChargingControlError(
                        "verify_current",
                        current,
                        f"Verification failed: expected {current}A, "
                        f"got {actual_current}A",
                    )

            self._logger.info(
                "Charging current set successfully", current=current, verified=verify
            )
            return success

        except Exception as e:
            self._logger.error(
                "Failed to set charging current", current=current, error=str(e)
            )
            if isinstance(e, ChargingControlError):
                raise
            raise ChargingControlError("set_current", current, str(e)) from e

    def set_phase_count(self, phases: int, verify: bool = True) -> bool:
        """Set the number of charging phases."""
        try:
            if phases not in [1, 3]:
                raise ChargingControlError(
                    "set_phases", phases, "Phases must be 1 or 3"
                )

            # Write phase setting
            success = self._modbus_client.write_register(
                self._config.registers.phases,
                phases,
                self._config.modbus.station_slave_id,
            )

            if verify and success:
                # Verify the setting
                actual_values = self._modbus_client.read_holding_registers(
                    self._config.registers.phases,
                    1,
                    self._config.modbus.station_slave_id,
                )
                if actual_values[0] != phases:
                    raise ChargingControlError(
                        "verify_phases",
                        phases,
                        f"Verification failed: expected {phases}, "
                        f"got {actual_values[0]}",
                    )

            self._logger.info(
                "Phase count set successfully", phases=phases, verified=verify
            )
            return success

        except Exception as e:
            self._logger.error("Failed to set phase count", phases=phases, error=str(e))
            if isinstance(e, ChargingControlError):
                raise
            raise ChargingControlError("set_phases", phases, str(e)) from e

    def get_charging_status(self) -> Dict[str, Any]:
        """Get the current charging status."""
        try:
            # Read status registers
            voltages = self._modbus_client.read_holding_registers(
                self._config.registers.voltages, 6, self._config.modbus.socket_slave_id
            )

            currents = self._modbus_client.read_holding_registers(
                self._config.registers.currents, 6, self._config.modbus.socket_slave_id
            )

            power = self._modbus_client.read_holding_registers(
                self._config.registers.power, 2, self._config.modbus.socket_slave_id
            )

            # Process the raw values (simplified)
            from .modbus_utils import decode_floats

            voltage_values = decode_floats(voltages, 3)
            current_values = decode_floats(currents, 3)
            power_value = decode_floats(power, 1)[0] if power else 0.0

            status = {
                "voltages": {
                    "L1": voltage_values[0],
                    "L2": voltage_values[1],
                    "L3": voltage_values[2],
                },
                "currents": {
                    "L1": current_values[0],
                    "L2": current_values[1],
                    "L3": current_values[2],
                },
                "power": power_value,
                "total_current": sum(current_values),
                "status": (
                    "charging" if any(i > 0.1 for i in current_values) else "idle"
                ),
            }

            return status

        except Exception as e:
            self._logger.error("Failed to get charging status", error=str(e))
            # Return minimal status on error
            return {
                "voltages": {"L1": 0, "L2": 0, "L3": 0},
                "currents": {"L1": 0, "L2": 0, "L3": 0},
                "power": 0,
                "total_current": 0,
                "status": "error",
            }

    def start_charging(self) -> bool:
        """Start the charging process."""
        # Implementation depends on charger-specific start mechanism
        return self.set_charging_current(self._config.defaults.intended_set_current)

    def stop_charging(self) -> bool:
        """Stop the charging process."""
        return self.set_charging_current(0.0)
