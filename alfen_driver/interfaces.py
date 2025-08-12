"""Interface definitions for dependency injection in the Alfen EV Charger Driver.

This module defines abstract interfaces that enable dependency injection and
improve testability by decoupling concrete implementations from business logic.
All interfaces follow the Interface Segregation Principle and define minimal,
focused contracts.

The interfaces cover key system dependencies:
    - Modbus communication (IModbusClient)
    - D-Bus integration (IDBusService)
    - Logging services (ILogger)
    - Time operations (ITimeProvider)
    - File system operations (IFileSystem)
    - Configuration management (IConfigProvider)

Example:
    ```python
    from alfen_driver.interfaces import IModbusClient, ILogger

    class AlfenDriver:
        def __init__(self, modbus_client: IModbusClient, logger: ILogger):
            self.modbus_client = modbus_client
            self.logger = logger
    ```
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


class IModbusClient(ABC):
    """Interface for Modbus TCP client operations.

    This interface abstracts Modbus communication to enable testing with mock
    clients and potential support for different Modbus libraries or protocols.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to the Modbus server.

        Returns:
            True if connection successful, False otherwise.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the Modbus connection."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the client is currently connected.

        Returns:
            True if connected, False otherwise.
        """
        pass

    @abstractmethod
    def read_holding_registers(self, address: int, count: int, slave: int) -> List[int]:
        """Read holding registers from the Modbus device.

        Args:
            address: Starting register address.
            count: Number of registers to read.
            slave: Modbus slave/unit identifier.

        Returns:
            List of register values.

        Raises:
            ModbusReadError: If the read operation fails.
        """
        pass

    @abstractmethod
    def write_register(self, address: int, value: int, slave: int) -> bool:
        """Write a single register to the Modbus device.

        Args:
            address: Register address to write.
            value: Value to write.
            slave: Modbus slave/unit identifier.

        Returns:
            True if write successful, False otherwise.

        Raises:
            ModbusWriteError: If the write operation fails.
        """
        pass

    @abstractmethod
    def write_registers(self, address: int, values: List[int], slave: int) -> bool:
        """Write multiple registers to the Modbus device.

        Args:
            address: Starting register address.
            values: List of values to write.
            slave: Modbus slave/unit identifier.

        Returns:
            True if write successful, False otherwise.

        Raises:
            ModbusWriteError: If the write operation fails.
        """
        pass

    @property
    @abstractmethod
    def host(self) -> str:
        """Get the Modbus server host address."""
        pass

    @property
    @abstractmethod
    def port(self) -> int:
        """Get the Modbus server port."""
        pass


class IDBusService(ABC):
    """Interface for D-Bus service operations.

    This interface abstracts D-Bus communication with the Victron Venus OS
    system, enabling testing without actual D-Bus dependencies.
    """

    @abstractmethod
    def add_path(self, path: str, value: Any) -> None:
        """Add a path to the D-Bus service.

        Args:
            path: The D-Bus path (e.g., "/Ac/Power").
            value: Initial value for the path.
        """
        pass

    @abstractmethod
    def set_value(self, path: str, value: Any) -> None:
        """Set a value for a D-Bus path.

        Args:
            path: The D-Bus path.
            value: Value to set.
        """
        pass

    @abstractmethod
    def get_value(self, path: str) -> Any:
        """Get the current value of a D-Bus path.

        Args:
            path: The D-Bus path.

        Returns:
            The current value.
        """
        pass

    @abstractmethod
    def register_callback(
        self, path: str, callback: Callable[[str, Any], None]
    ) -> None:
        """Register a callback for path changes.

        Args:
            path: The D-Bus path to monitor.
            callback: Function to call when value changes.
        """
        pass


class ILogger(ABC):
    """Interface for logging operations.

    This interface provides a simplified logging contract that supports both
    structured and traditional logging approaches.
    """

    @abstractmethod
    def debug(self, message: str, **kwargs: Any) -> None:
        """Log a debug message.

        Args:
            message: The log message.
            **kwargs: Additional structured data.
        """
        pass

    @abstractmethod
    def info(self, message: str, **kwargs: Any) -> None:
        """Log an info message.

        Args:
            message: The log message.
            **kwargs: Additional structured data.
        """
        pass

    @abstractmethod
    def warning(self, message: str, **kwargs: Any) -> None:
        """Log a warning message.

        Args:
            message: The log message.
            **kwargs: Additional structured data.
        """
        pass

    @abstractmethod
    def error(self, message: str, **kwargs: Any) -> None:
        """Log an error message.

        Args:
            message: The log message.
            **kwargs: Additional structured data.
        """
        pass

    @abstractmethod
    def exception(self, message: str, **kwargs: Any) -> None:
        """Log an exception with traceback.

        Args:
            message: The log message.
            **kwargs: Additional structured data.
        """
        pass


class ITimeProvider(ABC):
    """Interface for time operations.

    This interface enables testing of time-dependent functionality by providing
    a mockable time source.
    """

    @abstractmethod
    def now(self) -> float:
        """Get the current time as a timestamp.

        Returns:
            Current time as seconds since epoch.
        """
        pass

    @abstractmethod
    def sleep(self, duration: float) -> None:
        """Sleep for the specified duration.

        Args:
            duration: Sleep duration in seconds.
        """
        pass


class IFileSystem(ABC):
    """Interface for file system operations.

    This interface abstracts file system operations to enable testing without
    actual file I/O and to support different storage backends.
    """

    @abstractmethod
    def read_text(self, file_path: str) -> str:
        """Read text content from a file.

        Args:
            file_path: Path to the file.

        Returns:
            File content as string.

        Raises:
            FileNotFoundError: If file doesn't exist.
            IOError: If file cannot be read.
        """
        pass

    @abstractmethod
    def write_text(self, file_path: str, content: str) -> None:
        """Write text content to a file.

        Args:
            file_path: Path to the file.
            content: Content to write.

        Raises:
            IOError: If file cannot be written.
        """
        pass

    @abstractmethod
    def exists(self, file_path: str) -> bool:
        """Check if a file exists.

        Args:
            file_path: Path to check.

        Returns:
            True if file exists, False otherwise.
        """
        pass

    @abstractmethod
    def create_directory(self, dir_path: str) -> None:
        """Create a directory and any necessary parent directories.

        Args:
            dir_path: Directory path to create.

        Raises:
            IOError: If directory cannot be created.
        """
        pass


class IConfigProvider(ABC):
    """Interface for configuration management.

    This interface abstracts configuration loading and persistence,
    enabling testing with mock configurations and supporting different
    configuration sources.
    """

    @abstractmethod
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from the configured source.

        Returns:
            Configuration dictionary.

        Raises:
            ConfigurationError: If configuration cannot be loaded or is invalid.
        """
        pass

    @abstractmethod
    def save_config(self, config: Dict[str, Any]) -> None:
        """Save configuration to persistent storage.

        Args:
            config: Configuration dictionary to save.

        Raises:
            ConfigurationError: If configuration cannot be saved.
        """
        pass

    @abstractmethod
    def get_config_path(self) -> str:
        """Get the path to the configuration file.

        Returns:
            Path to configuration file.
        """
        pass


class IChargingController(ABC):
    """Interface for charging control operations.

    This interface abstracts the core charging control functionality,
    enabling testing of business logic without actual hardware interaction.
    """

    @abstractmethod
    def set_charging_current(self, current: float, verify: bool = True) -> bool:
        """Set the charging current.

        Args:
            current: Desired charging current in amperes.
            verify: Whether to verify the setting was applied.

        Returns:
            True if current was set successfully.

        Raises:
            ChargingControlError: If current cannot be set.
        """
        pass

    @abstractmethod
    def set_phase_count(self, phases: int, verify: bool = True) -> bool:
        """Set the number of charging phases (always 3-phase only).

        Args:
            phases: Number of phases (ignored, always uses 3).
            verify: Whether to verify the setting was applied.

        Returns:
            True if phases were set successfully.

        Raises:
            ChargingControlError: If phases cannot be set.
        """
        pass

    @abstractmethod
    def get_charging_status(self) -> Dict[str, Any]:
        """Get the current charging status.

        Returns:
            Dictionary containing charging status information including:
            - status: Current charging status
            - current: Current charging current
            - voltage: Current voltage readings
            - power: Current power consumption
            - energy: Total energy consumed
        """
        pass

    @abstractmethod
    def start_charging(self) -> bool:
        """Start the charging process.

        Returns:
            True if charging started successfully.
        """
        pass

    @abstractmethod
    def stop_charging(self) -> bool:
        """Stop the charging process.

        Returns:
            True if charging stopped successfully.
        """
        pass


class IScheduleManager(ABC):
    """Interface for schedule management operations.

    This interface manages charging schedules and time-based control logic.
    """

    @abstractmethod
    def is_within_schedule(self, current_time: Optional[float] = None) -> bool:
        """Check if current time is within any active schedule.

        Args:
            current_time: Optional timestamp to check. Uses current time if None.

        Returns:
            True if within an active schedule.
        """
        pass

    @abstractmethod
    def get_next_schedule_change(self) -> Optional[float]:
        """Get the timestamp of the next schedule state change.

        Returns:
            Timestamp of next change, or None if no upcoming changes.
        """
        pass

    @abstractmethod
    def update_schedule(
        self, schedule_index: int, schedule_data: Dict[str, Any]
    ) -> None:
        """Update a specific schedule.

        Args:
            schedule_index: Index of schedule to update (0-2).
            schedule_data: New schedule configuration.
        """
        pass
