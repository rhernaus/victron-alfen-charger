"""Dependency-injected version of the Alfen EV Charger Driver.

This module provides a refactored version of the main AlfenDriver class that
uses dependency injection for improved testability and modularity. The driver
accepts all dependencies through its constructor, making it easy to test with
mock implementations.

Key improvements over the original driver:
    - Constructor dependency injection
    - Interface-based dependencies
    - Improved separation of concerns
    - Enhanced testability
    - Better error handling and logging
    - Configuration flexibility

Example:
    ```python
    from alfen_driver.injectable_driver import InjectableAlfenDriver
    from alfen_driver.implementations import *
    from alfen_driver.di_container import DIContainer

    # Manual dependency injection
    driver = InjectableAlfenDriver(
        modbus_client=ModbusTcpClientWrapper("192.168.1.100", 502),
        dbus_service=DBusServiceWrapper("com.victronenergy.evcharger.alfen_0"),
        logger=StructuredLoggerWrapper("alfen_driver"),
        time_provider=SystemTimeProvider(),
        config=load_config()
    )

    # Or via DI container
    container = DIContainer()
    # ... register services ...
    driver = container.resolve(InjectableAlfenDriver)
    ```
"""

from typing import Any, Dict, List, Optional

from .config import Config
from .dbus_utils import EVC_CHARGE, EVC_MODE, EVC_STATUS
from .di_container import injectable
from .exceptions import AlfenDriverError, ChargingControlError, DBusError
from .interfaces import (
    IChargingController,
    IDBusService,
    ILogger,
    IModbusClient,
    IScheduleManager,
    ITimeProvider,
)
from .logic import is_within_any_schedule
from .modbus_utils import decode_64bit_float, decode_floats


@injectable
class InjectableAlfenDriver:
    """Dependency-injected Alfen EV Charger Driver.

    This class implements the main driver logic with all dependencies injected
    through the constructor. This design enables comprehensive testing with mock
    dependencies and provides better separation of concerns.

    Attributes:
        modbus_client: Interface for Modbus TCP communication.
        dbus_service: Interface for D-Bus communication with Venus OS.
        logger: Interface for logging operations.
        time_provider: Interface for time operations (enables time mocking).
        config: Driver configuration.
        charging_controller: Interface for charging control operations.
        schedule_manager: Interface for schedule management.
    """

    def __init__(
        self,
        modbus_client: IModbusClient,
        dbus_service: IDBusService,
        logger: ILogger,
        time_provider: ITimeProvider,
        config: Config,
        charging_controller: Optional[IChargingController] = None,
        schedule_manager: Optional[IScheduleManager] = None,
    ):
        """Initialize the injectable Alfen driver.

        Args:
            modbus_client: Modbus TCP client for charger communication.
            dbus_service: D-Bus service for Venus OS integration.
            logger: Logger for operation tracking and debugging.
            time_provider: Time provider for time-dependent operations.
            config: Driver configuration containing all parameters.
            charging_controller: Optional charging controller (created if None).
            schedule_manager: Optional schedule manager (created if None).
        """
        self.modbus_client = modbus_client
        self.dbus_service = dbus_service
        self.logger = logger
        self.time_provider = time_provider
        self.config = config

        # Initialize charging controller if not provided
        self.charging_controller: IChargingController
        if charging_controller is None:
            from .implementations import ChargingControllerImpl

            self.charging_controller = ChargingControllerImpl(
                modbus_client, config, logger
            )
        else:
            self.charging_controller = charging_controller

        self.schedule_manager = schedule_manager

        # Driver state
        self.session_id = self._generate_session_id()
        self.charging_start_time: float = 0
        self.last_current_set_time: float = 0
        self.session_start_energy_kwh: float = 0
        self.last_sent_current: float = -1.0
        self.last_sent_phases: int = 3
        self.station_max_current: float = config.defaults.station_max_current

        # Initialize D-Bus paths
        self._initialize_dbus_paths()

        # Log initialization
        self.logger.info(
            "Injectable Alfen driver initialized",
            session_id=self.session_id,
            modbus_host=modbus_client.host,
            modbus_port=modbus_client.port,
        )

    def _generate_session_id(self) -> str:
        """Generate a unique session ID for this driver instance."""
        import uuid

        return str(uuid.uuid4())[:8]

    def _initialize_dbus_paths(self) -> None:
        """Initialize D-Bus paths for Venus OS integration."""
        try:
            # Power and energy paths
            self.dbus_service.add_path("/Ac/Power", 0.0)
            self.dbus_service.add_path("/Ac/Energy/Forward", 0.0)
            self.dbus_service.add_path("/Ac/Voltage", 0.0)
            self.dbus_service.add_path("/Ac/Current", 0.0)

            # Charging control paths
            self.dbus_service.add_path("/Current", 0.0)
            self.dbus_service.add_path("/SetCurrent", 0.0)
            self.dbus_service.add_path(
                "/MaxCurrent", self.config.defaults.station_max_current
            )

            # Status and mode paths
            self.dbus_service.add_path("/Status", EVC_STATUS.DISCONNECTED.value)
            self.dbus_service.add_path("/Mode", EVC_MODE.AUTO.value)
            self.dbus_service.add_path("/StartStop", EVC_CHARGE.DISABLED.value)

            # Information paths
            self.dbus_service.add_path("/ProductName", "Alfen EV Charger")
            self.dbus_service.add_path("/FirmwareVersion", "Unknown")
            self.dbus_service.add_path("/Serial", "Unknown")
            self.dbus_service.add_path("/Model", "Alfen")

            # Position (required by Venus OS)
            self.dbus_service.add_path("/Position", self.config.device_instance)

            # Register mode change callback
            self.dbus_service.register_callback("/Mode", self._on_mode_changed)
            self.dbus_service.register_callback(
                "/StartStop", self._on_start_stop_changed
            )

            self.logger.info(
                "D-Bus paths initialized successfully",
                device_instance=self.config.device_instance,
            )

        except Exception as e:
            self.logger.error("Failed to initialize D-Bus paths", error=str(e))
            raise DBusError("Failed to initialize D-Bus service", str(e)) from e

    def start(self) -> None:
        """Start the driver main loop.

        This method establishes the Modbus connection and begins the main
        operational loop that reads data from the charger and updates D-Bus.
        """
        try:
            # Connect to Modbus
            if not self.modbus_client.connect():
                raise AlfenDriverError("Failed to connect to Modbus server")

            self.logger.info(
                "Driver started successfully",
                modbus_connected=self.modbus_client.is_connected(),
            )

            # Start main loop
            self._main_loop()

        except KeyboardInterrupt:
            self.logger.info("Driver stopped by user")
        except Exception as e:
            self.logger.exception("Driver startup failed", error=str(e))
            raise
        finally:
            self._cleanup()

    def _main_loop(self) -> None:
        """Main operational loop for the driver."""
        poll_interval = self.config.poll_interval_ms / 1000.0

        while True:
            loop_start_time = self.time_provider.now()

            try:
                # Update charger data
                self._update_charger_data()

                # Process control logic
                self._process_control_logic()

                # Calculate sleep time for consistent polling
                elapsed = self.time_provider.now() - loop_start_time
                sleep_time = max(0, poll_interval - elapsed)

                if sleep_time > 0:
                    self.time_provider.sleep(sleep_time)

            except KeyboardInterrupt:
                break
            except Exception as e:
                self.logger.error("Error in main loop", error=str(e))
                self.time_provider.sleep(poll_interval)

    def _update_charger_data(self) -> None:
        """Read current data from the charger and update D-Bus."""
        try:
            # Read electrical measurements
            voltages = self._read_voltages()
            currents = self._read_currents()
            power = self._read_power()
            energy = self._read_energy()

            # Update D-Bus with measurements
            if voltages:
                avg_voltage = sum(voltages) / len(voltages)
                self.dbus_service.set_value("/Ac/Voltage", round(avg_voltage, 1))

            if currents:
                total_current = sum(currents)
                self.dbus_service.set_value("/Ac/Current", round(total_current, 2))
                self.dbus_service.set_value("/Current", round(total_current, 2))

            if power is not None:
                self.dbus_service.set_value("/Ac/Power", round(power, 0))

            if energy is not None:
                energy_kwh = energy / 1000.0  # Convert Wh to kWh
                self.dbus_service.set_value("/Ac/Energy/Forward", round(energy_kwh, 3))

            # Read and update status
            status = self._read_status()
            if status is not None:
                self.dbus_service.set_value("/Status", status)

        except Exception as e:
            self.logger.error("Failed to update charger data", error=str(e))

    def _read_voltages(self) -> Optional[List[float]]:
        """Read voltage measurements from the charger."""
        try:
            registers = self.modbus_client.read_holding_registers(
                self.config.registers.voltages, 6, self.config.modbus.socket_slave_id
            )
            return decode_floats(registers, 3)
        except Exception as e:
            self.logger.debug("Failed to read voltages", error=str(e))
            return None

    def _read_currents(self) -> Optional[List[float]]:
        """Read current measurements from the charger."""
        try:
            registers = self.modbus_client.read_holding_registers(
                self.config.registers.currents, 6, self.config.modbus.socket_slave_id
            )
            return decode_floats(registers, 3)
        except Exception as e:
            self.logger.debug("Failed to read currents", error=str(e))
            return None

    def _read_power(self) -> Optional[float]:
        """Read power measurement from the charger."""
        try:
            registers = self.modbus_client.read_holding_registers(
                self.config.registers.power, 2, self.config.modbus.socket_slave_id
            )
            return decode_floats(registers, 1)[0]
        except Exception as e:
            self.logger.debug("Failed to read power", error=str(e))
            return None

    def _read_energy(self) -> Optional[float]:
        """Read energy counter from the charger."""
        try:
            registers = self.modbus_client.read_holding_registers(
                self.config.registers.energy, 4, self.config.modbus.socket_slave_id
            )
            return decode_64bit_float(registers)
        except Exception as e:
            self.logger.debug("Failed to read energy", error=str(e))
            return None

    def _read_status(self) -> Optional[int]:
        """Read and map charging status from the charger."""
        try:
            # Implementation would depend on specific status mapping
            # This is a simplified version
            currents = self._read_currents()
            if currents and any(c > 0.1 for c in currents):
                return EVC_STATUS.CHARGING.value
            else:
                return EVC_STATUS.DISCONNECTED.value
        except Exception as e:
            self.logger.debug("Failed to read status", error=str(e))
            return None

    def _process_control_logic(self) -> None:
        """Process charging control logic based on mode and schedules."""
        try:
            current_time = self.time_provider.now()
            current_mode = self.dbus_service.get_value("/Mode")
            start_stop = self.dbus_service.get_value("/StartStop")

            # Skip control if charging is disabled
            if start_stop == EVC_CHARGE.DISABLED.value:
                return

            # Determine effective current based on mode and schedule
            if current_mode == EVC_MODE.MANUAL.value:
                effective_current = self.config.defaults.intended_set_current
            elif current_mode == EVC_MODE.AUTO.value:
                effective_current = self._calculate_auto_current()
            elif current_mode == EVC_MODE.SCHEDULED.value:
                effective_current = self._calculate_scheduled_current(current_time)
            else:
                effective_current = 0.0

            # Apply current setting if changed significantly
            if abs(effective_current - self.last_sent_current) > 0.1:
                self._set_charging_current(effective_current)

        except Exception as e:
            self.logger.error("Failed to process control logic", error=str(e))

    def _calculate_auto_current(self) -> float:
        """Calculate current for auto mode (solar excess)."""
        # Simplified implementation - would integrate with Venus OS energy flow
        try:
            # This would typically read from Venus OS D-Bus to get:
            # - Solar production
            # - House consumption
            # - Grid usage
            # Then calculate available excess for charging

            # For now, return configured current
            return self.config.defaults.intended_set_current
        except Exception as e:
            self.logger.error("Failed to calculate auto current", error=str(e))
            return 0.0

    def _calculate_scheduled_current(self, current_time: float) -> float:
        """Calculate current for scheduled mode."""
        try:
            if self.schedule_manager:
                within_schedule = self.schedule_manager.is_within_schedule(current_time)
            else:
                within_schedule = is_within_any_schedule(
                    self.config.schedule.items, current_time, self.config.timezone
                )

            if within_schedule:
                return self.config.defaults.intended_set_current
            else:
                return 0.0

        except Exception as e:
            self.logger.error("Failed to calculate scheduled current", error=str(e))
            return 0.0

    def _set_charging_current(self, current: float) -> None:
        """Set the charging current via the charging controller."""
        try:
            success = self.charging_controller.set_charging_current(
                current, verify=True
            )
            if success:
                self.last_sent_current = current
                self.last_current_set_time = self.time_provider.now()
                self.dbus_service.set_value("/SetCurrent", round(current, 2))

                self.logger.info(
                    "Charging current updated", current=current, success=success
                )
            else:
                self.logger.warning("Failed to set charging current", current=current)
        except ChargingControlError as e:
            self.logger.error("Charging control error", current=current, error=str(e))
        except Exception as e:
            self.logger.error(
                "Unexpected error setting current", current=current, error=str(e)
            )

    def _on_mode_changed(self, path: str, value: Any) -> None:
        """Handle charging mode changes from D-Bus."""
        try:
            mode_name = EVC_MODE(value).name
            self.logger.info("Charging mode changed", mode=mode_name, value=value)
        except Exception as e:
            self.logger.error("Invalid mode value", value=value, error=str(e))

    def _on_start_stop_changed(self, path: str, value: Any) -> None:
        """Handle start/stop changes from D-Bus."""
        try:
            charge_name = EVC_CHARGE(value).name
            self.logger.info("Start/stop changed", charge=charge_name, value=value)

            if value == EVC_CHARGE.DISABLED.value:
                # Stop charging immediately
                self._set_charging_current(0.0)
        except Exception as e:
            self.logger.error("Invalid start/stop value", value=value, error=str(e))

    def _cleanup(self) -> None:
        """Clean up resources on shutdown."""
        try:
            # Stop charging
            if self.modbus_client.is_connected():
                self._set_charging_current(0.0)

            # Close Modbus connection
            self.modbus_client.close()

            self.logger.info("Driver cleanup completed", session_id=self.session_id)
        except Exception as e:
            self.logger.error("Error during cleanup", error=str(e))

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive driver status for monitoring.

        Returns:
            Dictionary containing current driver status, including:
            - Connection status
            - Current measurements
            - Configuration
            - Session information
        """
        try:
            charging_status = self.charging_controller.get_charging_status()

            return {
                "session_id": self.session_id,
                "modbus_connected": self.modbus_client.is_connected(),
                "charging_status": charging_status,
                "last_current_set": self.last_sent_current,
                "station_max_current": self.station_max_current,
                "config": {
                    "modbus_host": self.modbus_client.host,
                    "modbus_port": self.modbus_client.port,
                    "device_instance": self.config.device_instance,
                    "poll_interval_ms": self.config.poll_interval_ms,
                },
                "uptime_seconds": self.time_provider.now() - self.charging_start_time,
            }
        except Exception as e:
            self.logger.error("Failed to get driver status", error=str(e))
            return {"session_id": self.session_id, "error": str(e), "status": "error"}
