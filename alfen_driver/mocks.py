"""Mock implementations of interfaces for testing.

This module provides mock implementations of all interfaces defined in the
interfaces module. These mocks are designed for testing scenarios and provide
controllable behavior for unit testing the driver without real hardware.

The mock implementations include:
    - MockModbusClient: Simulates Modbus TCP communication
    - MockDBusService: Simulates D-Bus service operations
    - MockLogger: Captures log messages for verification
    - MockTimeProvider: Provides controllable time for testing
    - MockFileSystem: In-memory file system for testing
    - MockConfigProvider: Provides test configurations
    - MockChargingController: Simulates charging control operations
    - MockScheduleManager: Simulates schedule management

Example:
    ```python
    from alfen_driver.mocks import MockModbusClient, MockDBusService

    # Create mock instances for testing
    mock_modbus = MockModbusClient(connected=True)
    mock_modbus.set_register_values(306, [0x4316, 0x8000])  # 230.5V

    mock_dbus = MockDBusService("test.service")

    # Use in driver testing
    driver = InjectableAlfenDriver(mock_modbus, mock_dbus, ...)
    ```
"""

import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from .exceptions import ModbusReadError, ModbusWriteError
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


class MockModbusClient(IModbusClient):
    """Mock Modbus client for testing.

    This mock client simulates Modbus TCP communication without requiring
    actual hardware. It maintains an internal register map that can be
    programmed with expected values for testing different scenarios.

    Attributes:
        _connected: Whether the client is connected.
        _registers: Dictionary mapping (slave_id, address) to register values.
        _read_count: Counter for read operations (for verification).
        _write_count: Counter for write operations (for verification).
        _fail_next_read: If True, the next read will fail.
        _fail_next_write: If True, the next write will fail.
    """

    def __init__(
        self, connected: bool = False, host: str = "mock.host", port: int = 502
    ):
        """Initialize mock Modbus client.

        Args:
            connected: Initial connection state.
            host: Mock host address.
            port: Mock port number.
        """
        self._connected = connected
        self._host = host
        self._port = port
        self._registers: Dict[tuple, List[int]] = {}
        self._read_count = 0
        self._write_count = 0
        self._fail_next_read = False
        self._fail_next_write = False

    def connect(self) -> bool:
        """Simulate connection establishment."""
        self._connected = True
        return True

    def close(self) -> None:
        """Simulate connection closing."""
        self._connected = False

    def is_connected(self) -> bool:
        """Check connection status."""
        return self._connected

    def read_holding_registers(self, address: int, count: int, slave: int) -> List[int]:
        """Simulate reading holding registers."""
        self._read_count += 1

        if self._fail_next_read:
            self._fail_next_read = False
            raise ModbusReadError(address, count, slave, "Mock read failure")

        # Return stored values or zeros
        result = []
        for i in range(count):
            key = (slave, address + i)
            if key in self._registers:
                result.extend(self._registers[key])
            else:
                result.append(0)

        return result[:count]

    def write_register(self, address: int, value: int, slave: int) -> bool:
        """Simulate writing a single register."""
        self._write_count += 1

        if self._fail_next_write:
            self._fail_next_write = False
            raise ModbusWriteError(address, value, slave, "Mock write failure")

        self._registers[(slave, address)] = [value]
        return True

    def write_registers(self, address: int, values: List[int], slave: int) -> bool:
        """Simulate writing multiple registers."""
        self._write_count += 1

        if self._fail_next_write:
            self._fail_next_write = False
            raise ModbusWriteError(address, values, slave, "Mock write failure")

        for i, value in enumerate(values):
            self._registers[(slave, address + i)] = [value]
        return True

    @property
    def host(self) -> str:
        """Get mock host address."""
        return self._host

    @property
    def port(self) -> int:
        """Get mock port."""
        return self._port

    # Test helper methods
    def set_register_values(
        self, address: int, values: List[int], slave: int = 1
    ) -> None:
        """Set expected register values for testing."""
        for i, value in enumerate(values):
            self._registers[(slave, address + i)] = [value]

    def fail_next_read(self) -> None:
        """Make the next read operation fail."""
        self._fail_next_read = True

    def fail_next_write(self) -> None:
        """Make the next write operation fail."""
        self._fail_next_write = True

    def get_read_count(self) -> int:
        """Get the number of read operations performed."""
        return self._read_count

    def get_write_count(self) -> int:
        """Get the number of write operations performed."""
        return self._write_count


class MockDBusService(IDBusService):
    """Mock D-Bus service for testing.

    This mock service simulates D-Bus operations without requiring actual
    D-Bus system access. It maintains an internal dictionary of paths and
    values that can be inspected during testing.
    """

    def __init__(self, service_name: str = "mock.service"):
        """Initialize mock D-Bus service.

        Args:
            service_name: Mock service name.
        """
        self._service_name = service_name
        self._paths: Dict[str, Any] = {}
        self._callbacks: Dict[str, List[Callable]] = defaultdict(list)
        self._path_access_count: Dict[str, int] = defaultdict(int)

    def add_path(self, path: str, value: Any) -> None:
        """Add a path to the mock service."""
        self._paths[path] = value
        self._path_access_count[path] = 0

    def set_value(self, path: str, value: Any) -> None:
        """Set a value for a path."""
        old_value = self._paths.get(path)
        self._paths[path] = value
        self._path_access_count[path] += 1

        # Trigger callbacks if value changed
        if old_value != value and path in self._callbacks:
            for callback in self._callbacks[path]:
                callback(path, value)

    def get_value(self, path: str) -> Any:
        """Get the current value of a path."""
        self._path_access_count[path] += 1
        return self._paths.get(path)

    def register_callback(self, path: str, callback: Callable) -> None:
        """Register a callback for path changes."""
        self._callbacks[path].append(callback)

    # Test helper methods
    def get_all_paths(self) -> Dict[str, Any]:
        """Get all registered paths and values."""
        return self._paths.copy()

    def get_access_count(self, path: str) -> int:
        """Get the number of times a path was accessed."""
        return self._path_access_count.get(path, 0)

    def trigger_callback(self, path: str, value: Any) -> None:
        """Manually trigger callbacks for testing."""
        if path in self._callbacks:
            for callback in self._callbacks[path]:
                callback(path, value)


class MockLogger(ILogger):
    """Mock logger for testing.

    This mock logger captures all log messages for verification in tests
    without actually writing to files or console.
    """

    def __init__(self) -> None:
        """Initialize mock logger."""
        self.messages: List[Dict[str, Any]] = []

    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        """Internal method to capture log messages."""
        self.messages.append(
            {
                "level": level,
                "message": message,
                "context": kwargs,
                "timestamp": time.time(),
            }
        )

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log a debug message."""
        self._log("DEBUG", message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log an info message."""
        self._log("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log a warning message."""
        self._log("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log an error message."""
        self._log("ERROR", message, **kwargs)

    def exception(self, message: str, **kwargs: Any) -> None:
        """Log an exception with traceback."""
        self._log("EXCEPTION", message, **kwargs)

    # Test helper methods
    def get_messages(self, level: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get logged messages, optionally filtered by level."""
        if level:
            return [m for m in self.messages if m["level"] == level]
        return self.messages.copy()

    def clear(self) -> None:
        """Clear all logged messages."""
        self.messages.clear()

    def has_message(self, text: str, level: Optional[str] = None) -> bool:
        """Check if a message containing text was logged."""
        messages = self.get_messages(level)
        return any(text in m["message"] for m in messages)


class MockTimeProvider(ITimeProvider):
    """Mock time provider for testing time-dependent functionality.

    This mock allows controlling time progression in tests without
    actually waiting. Time can be advanced manually or automatically.
    """

    def __init__(self, initial_time: float = 0.0):
        """Initialize mock time provider.

        Args:
            initial_time: Initial timestamp (default: 0.0).
        """
        self._current_time = initial_time
        self._sleep_calls: List[float] = []
        self._auto_advance = False

    def now(self) -> float:
        """Get the current mock time."""
        return self._current_time

    def sleep(self, duration: float) -> None:
        """Simulate sleep by advancing time."""
        self._sleep_calls.append(duration)
        if self._auto_advance:
            self._current_time += duration

    # Test helper methods
    def advance(self, seconds: float) -> None:
        """Manually advance the mock time."""
        self._current_time += seconds

    def set_time(self, timestamp: float) -> None:
        """Set the mock time to a specific value."""
        self._current_time = timestamp

    def set_auto_advance(self, enabled: bool) -> None:
        """Enable/disable automatic time advancement on sleep."""
        self._auto_advance = enabled

    def get_sleep_calls(self) -> List[float]:
        """Get list of sleep durations that were called."""
        return self._sleep_calls.copy()


class MockFileSystem(IFileSystem):
    """Mock file system for testing.

    This mock provides an in-memory file system for testing file operations
    without actual disk I/O.
    """

    def __init__(self) -> None:
        """Initialize mock file system."""
        self._files: Dict[str, str] = {}
        self._directories: set[str] = set()

    def read_text(self, file_path: str) -> str:
        """Read text content from mock file."""
        if file_path not in self._files:
            raise FileNotFoundError(f"Mock file not found: {file_path}")
        return self._files[file_path]

    def write_text(self, file_path: str, content: str) -> None:
        """Write text content to mock file."""
        self._files[file_path] = content

        # Add parent directories
        import os

        directory = os.path.dirname(file_path)
        if directory:
            self._directories.add(directory)

    def exists(self, file_path: str) -> bool:
        """Check if mock file exists."""
        return file_path in self._files or file_path in self._directories

    def create_directory(self, dir_path: str) -> None:
        """Create mock directory."""
        self._directories.add(dir_path)

    # Test helper methods
    def add_file(self, file_path: str, content: str) -> None:
        """Add a file to the mock file system."""
        self._files[file_path] = content

    def get_all_files(self) -> Dict[str, str]:
        """Get all files in the mock file system."""
        return self._files.copy()

    def clear(self) -> None:
        """Clear all files and directories."""
        self._files.clear()
        self._directories.clear()


class MockConfigProvider(IConfigProvider):
    """Mock configuration provider for testing.

    This mock provides test configurations without requiring actual
    configuration files.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize mock config provider.

        Args:
            config: Initial configuration dictionary.
        """
        self._config = config or self._get_default_config()
        self._config_path = "/mock/config.yaml"
        self._save_count = 0

    def load_config(self) -> Dict[str, Any]:
        """Return the mock configuration."""
        return self._config.copy()

    def save_config(self, config: Dict[str, Any]) -> None:
        """Save configuration (mock operation)."""
        self._config = config.copy()
        self._save_count += 1

    def get_config_path(self) -> str:
        """Get mock configuration path."""
        return self._config_path

    # Test helper methods
    def set_config(self, config: Dict[str, Any]) -> None:
        """Set the mock configuration."""
        self._config = config

    def get_save_count(self) -> int:
        """Get the number of times config was saved."""
        return self._save_count

    @staticmethod
    def _get_default_config() -> Dict[str, Any]:
        """Get default test configuration."""
        return {
            "modbus": {
                "ip": "192.168.1.100",
                "port": 502,
                "socket_slave_id": 1,
                "station_slave_id": 200,
            },
            "defaults": {"intended_set_current": 6.0, "station_max_current": 32.0},
            "controls": {"max_set_current": 32.0, "current_tolerance": 0.5},
            "device_instance": 0,
            "poll_interval_ms": 1000,
            "timezone": "UTC",
        }


class MockChargingController(IChargingController):
    """Mock charging controller for testing.

    This mock simulates charging control operations without actual
    hardware interaction.
    """

    def __init__(self) -> None:
        """Initialize mock charging controller."""
        self._current_setting = 0.0
        self._phase_setting = 3
        self._is_charging = False
        self._fail_next_operation = False
        self._verify_success = True

    def set_charging_current(self, current: float, verify: bool = True) -> bool:
        """Set mock charging current."""
        if self._fail_next_operation:
            self._fail_next_operation = False
            return False

        self._current_setting = current
        if current > 0:
            self._is_charging = True
        else:
            self._is_charging = False

        return self._verify_success if verify else True

    def set_phase_count(self, phases: int, verify: bool = True) -> bool:
        """Set mock phase count."""
        if self._fail_next_operation:
            self._fail_next_operation = False
            return False

        if phases not in [1, 3]:
            return False

        self._phase_setting = phases
        return self._verify_success if verify else True

    def get_charging_status(self) -> Dict[str, Any]:
        """Get mock charging status."""
        return {
            "voltages": {"L1": 230.0, "L2": 230.0, "L3": 230.0},
            "currents": {
                "L1": self._current_setting if self._is_charging else 0,
                "L2": (
                    self._current_setting
                    if self._is_charging and self._phase_setting == 3
                    else 0
                ),
                "L3": (
                    self._current_setting
                    if self._is_charging and self._phase_setting == 3
                    else 0
                ),
            },
            "power": (
                self._current_setting * 230.0 * self._phase_setting
                if self._is_charging
                else 0
            ),
            "total_current": self._current_setting
            * (self._phase_setting if self._is_charging else 0),
            "status": "charging" if self._is_charging else "idle",
        }

    def start_charging(self) -> bool:
        """Start mock charging."""
        self._is_charging = True
        if self._current_setting == 0:
            self._current_setting = 6.0  # Default current
        return True

    def stop_charging(self) -> bool:
        """Stop mock charging."""
        self._is_charging = False
        self._current_setting = 0.0
        return True

    # Test helper methods
    def fail_next_operation(self) -> None:
        """Make the next operation fail."""
        self._fail_next_operation = True

    def set_verify_success(self, success: bool) -> None:
        """Set whether verification succeeds."""
        self._verify_success = success


class MockScheduleManager(IScheduleManager):
    """Mock schedule manager for testing.

    This mock simulates schedule management without actual time checking.
    """

    def __init__(self, within_schedule: bool = False):
        """Initialize mock schedule manager.

        Args:
            within_schedule: Initial schedule state.
        """
        self._within_schedule = within_schedule
        self._next_change: Optional[float] = None
        self._schedules: Dict[int, Dict[str, Any]] = {}

    def is_within_schedule(self, current_time: Optional[float] = None) -> bool:
        """Check if within mock schedule."""
        return self._within_schedule

    def get_next_schedule_change(self) -> Optional[float]:
        """Get next mock schedule change time."""
        return self._next_change

    def update_schedule(
        self, schedule_index: int, schedule_data: Dict[str, Any]
    ) -> None:
        """Update mock schedule."""
        self._schedules[schedule_index] = schedule_data

    # Test helper methods
    def set_within_schedule(self, within: bool) -> None:
        """Set schedule state for testing."""
        self._within_schedule = within

    def set_next_change(self, timestamp: Optional[float]) -> None:
        """Set next schedule change time."""
        self._next_change = timestamp

    def get_schedules(self) -> Dict[int, Dict[str, Any]]:
        """Get all configured schedules."""
        return self._schedules.copy()
