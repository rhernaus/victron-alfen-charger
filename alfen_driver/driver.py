#!/usr/bin/env python3
"""Simplified Alfen EV Charger driver for Victron Venus OS."""

import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

sys.path.insert(
    1, os.path.join(os.path.dirname(__file__), "/opt/victronenergy/dbus-modbus-client")
)

from gi.repository import GLib  # noqa: E402
from pymodbus.client import ModbusTcpClient  # noqa: E402
from pymodbus.exceptions import ModbusException  # noqa: E402

from .config import Config, load_config  # noqa: E402
from .constants import (  # noqa: E402
    ChargingLimits,
    ModbusRegisters,
    PollingIntervals,
)
from .controls import (  # noqa: E402
    set_current,
    update_station_max_current,
)
from .dbus_utils import EVC_CHARGE, EVC_MODE, register_dbus_service  # noqa: E402
from .exceptions import ModbusError  # noqa: E402
from .logging_utils import (  # noqa: E402
    LogContext,
    get_logger,
    set_context,
    setup_root_logging,
)
from .logic import (  # noqa: E402
    compute_effective_current,
    get_complete_status,
    read_active_phases,
)
from .logic import (  # noqa: E402
    set_config as set_logic_config,
)
from .modbus_utils import (  # noqa: E402
    decode_64bit_float,
    read_holding_registers,
    read_modbus_string,
    reconnect,
)
from .persistence import PersistenceManager  # noqa: E402
from .session_manager import ChargingSessionManager  # noqa: E402

try:
    import dbus
except ImportError:  # pragma: no cover
    dbus = None


class MutableValue:
    """Simple mutable value wrapper for callbacks."""

    def __init__(self, value: Any) -> None:
        self.value = value


class AlfenDriver:
    """Simplified Alfen EV charger driver."""

    def __init__(self) -> None:
        """Initialize the driver with configuration and components."""
        # Load configuration
        self.config: Config = load_config()

        # Setup logging
        setup_root_logging(self.config)
        self.logger = get_logger("alfen_driver")
        self.session_id = str(uuid.uuid4())[:8]

        set_context(
            LogContext(
                component="driver",
                session_id=self.session_id,
                device_instance=self.config.device_instance,
            )
        )

        # Initialize components
        self.persistence = PersistenceManager("/data/alfen_driver_config.json")
        self.session_manager = ChargingSessionManager()

        # Initialize Modbus client
        self.client = ModbusTcpClient(
            host=self.config.modbus.ip,
            port=self.config.modbus.port,
        )

        # Initialize state
        self._init_state()

        # Set config in logic module for Tibber access
        set_logic_config(self.config)

        # Setup D-Bus service
        self._setup_dbus()

        # Load static info
        self._load_static_info()

        # Restore persisted state
        self._restore_state()

        # Log current configuration settings at startup
        self._log_startup_settings()

        self.logger.info("Driver initialization complete")

    def _init_state(self) -> None:
        """Initialize driver state variables."""
        self.charging_start_time: float = 0
        self.insufficient_solar_start: float = (
            0  # Track when insufficient solar started
        )
        self.last_positive_set_time: float = 0.0
        self.last_current_set_time: float = 0
        self.last_sent_current: float = 0.0
        self.station_max_current: float = ChargingLimits.MAX_CURRENT
        self.current_update_counter: int = 0
        self.last_poll_time: float = 0
        self.active_phases: int = 3  # Default to 3-phase
        self.last_status: int = 0  # Track last status for change detection

        # Mutable values for D-Bus callbacks
        self.current_mode = MutableValue(self.persistence.mode)
        self.start_stop = MutableValue(self.persistence.start_stop)
        self.intended_set_current = MutableValue(self.persistence.set_current)

        # Schedule configuration (use schedule items or empty list)
        self.schedules = (
            self.config.schedule.items if hasattr(self.config, "schedule") else []
        )

    def _setup_dbus(self) -> None:
        """Setup D-Bus service and callbacks."""
        # Create service name - must use text prefix for D-Bus naming rules
        service_name = (
            f"com.victronenergy.evcharger.alfen_{self.config.device_instance}"
        )

        # Register the service with all required parameters
        # Note: register_dbus_service already adds /Mode, /StartStop, /SetCurrent paths
        self.service = register_dbus_service(
            service_name,
            self.config,
            EVC_MODE(self.current_mode.value),
            EVC_CHARGE(self.start_stop.value),
            self.intended_set_current.value,
            self.schedules,
            self.mode_callback,
            self.startstop_callback,
            self.set_current_callback,
        )

        # Update static paths (these are already created by register_dbus_service)
        self.service["/FirmwareVersion"] = "Unknown"
        self.service["/Serial"] = "Unknown"
        self.service["/ProductName"] = "Alfen EV Charger"

    def _load_static_info(self) -> None:
        """Load static information and configuration from the charger."""
        if not self.client.connect():
            self.logger.warning("Failed to connect for static info")
            return

        # Try to read static info, but don't fail if unavailable
        # Different Alfen models may have different register layouts
        try:
            # Try to read firmware version (string at registers 123-139)
            firmware = read_modbus_string(
                self.client,
                ModbusRegisters.FIRMWARE_VERSION_START,
                ModbusRegisters.FIRMWARE_VERSION_LENGTH,
                self.config.modbus.station_slave_id,
            )
            if firmware:
                self.service["/FirmwareVersion"] = firmware
                self.logger.info(f"Firmware version: {firmware}")
        except Exception as e:
            self.logger.debug(f"Could not read firmware version: {e}")

        try:
            # Try to read serial number
            serial = read_modbus_string(
                self.client,
                ModbusRegisters.SERIAL_NUMBER_START,
                ModbusRegisters.SERIAL_NUMBER_LENGTH,
                self.config.modbus.station_slave_id,
            )
            if serial:
                self.service["/Serial"] = serial
                self.logger.info(f"Serial number: {serial}")
        except Exception as e:
            self.logger.debug(f"Could not read serial number: {e}")

        try:
            # Try to read manufacturer
            manufacturer = read_modbus_string(
                self.client,
                ModbusRegisters.MANUFACTURER_START,
                ModbusRegisters.MANUFACTURER_LENGTH,
                self.config.modbus.station_slave_id,
            )
            if manufacturer:
                self.service["/ProductName"] = f"{manufacturer} EV Charger"
                self.logger.info(f"Manufacturer: {manufacturer}")
        except Exception as e:
            self.logger.debug(f"Could not read manufacturer: {e}")

        # Read operational parameters from charger
        self._read_charger_parameters()

    def _set_current_with_logging(
        self,
        effective_current: float,
        explanation: str,
        force_verify: bool = False,
        source: str = "Update",
    ) -> bool:
        """Set current with appropriate logging based on mode.

        Args:
            effective_current: The current to set
            explanation: Explanation of how current was calculated
            force_verify: Whether to verify the set operation
            source: Source of the update for logging

        Returns:
            True if current was set successfully
        """
        # Always show calculation details for Auto mode
        if self.current_mode.value == EVC_MODE.AUTO:
            self.logger.info(
                f"AUTO MODE: Setting current to {effective_current:.2f}A\n"
                f"  Calculation: {explanation}"
            )

        # Set the current
        success = set_current(
            self.client,
            self.config,
            effective_current,
            self.station_max_current,
            force_verify=force_verify,
        )

        if success:
            mode_name = EVC_MODE(self.current_mode.value).name
            if self.current_mode.value != EVC_MODE.AUTO:
                # Log for non-AUTO modes (AUTO already logged above)
                self.logger.info(
                    f"{source}: Set current to {effective_current:.2f}A ({mode_name})"
                )

        return success

    def _read_charger_parameters(self) -> None:
        """Read operational parameters from the charger."""
        # Read station max current
        try:
            self.station_max_current = update_station_max_current(
                self.client,
                self.config,
                self.service,
                self.config.defaults,
                self.logger,
            )
            self.logger.info(
                f"Station max current from charger: {self.station_max_current:.1f}A"
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to read station max current: {e}, "
                f"using default: {self.station_max_current:.1f}A"
            )

        # Read active phases
        try:
            self.active_phases = read_active_phases(self.client, self.config)
            self.logger.info(f"Active phases from charger: {self.active_phases}")
        except Exception as e:
            self.logger.warning(
                f"Failed to read active phases: {e}, "
                f"using default: {self.active_phases}"
            )

        # Read initial status
        try:
            self.last_status = get_complete_status(
                self.client,
                self.config,
                EVC_MODE(self.current_mode.value),
                self.active_phases,
            )
            self.service["/Status"] = self.last_status
            status_names = {
                0: "Disconnected",
                1: "Connected",
                2: "Charging",
                7: "Low SOC",
            }
            status_name = status_names.get(
                self.last_status, f"Unknown({self.last_status})"
            )
            self.logger.info(f"Initial charger status: {status_name}")
        except Exception as e:
            self.logger.warning(f"Failed to read initial status: {e}")

    def _restore_state(self) -> None:
        """Restore persisted state and session data."""
        # Restore session manager state
        session_state = self.persistence.get_section("session")
        if session_state:
            self.session_manager.restore_state(session_state)

        # Restore charging session state
        self.charging_start_time = self.persistence.get("charging_start_time", 0)
        self.insufficient_solar_start = self.persistence.get(
            "insufficient_solar_start", 0
        )

        self.logger.info("Restored persisted state")

    def _log_startup_settings(self) -> None:
        """Log current configuration settings at startup."""
        mode_str = EVC_MODE(self.current_mode.value).name
        charge_str = EVC_CHARGE(self.start_stop.value).name
        status_names = {0: "Disconnected", 1: "Connected", 2: "Charging", 7: "Low SOC"}
        status_str = status_names.get(self.last_status, f"Unknown({self.last_status})")

        self.logger.info(
            f"=== Current Settings at Startup ===\n"
            f"  EV Status: {status_str}\n"
            f"  Mode: {mode_str}\n"
            f"  Charging: {charge_str}\n"
            f"  Intended Current: {self.intended_set_current.value:.2f}A\n"
            f"  Station Max Current (from charger): {self.station_max_current:.2f}A\n"
            f"  Active Phases (from charger): {self.active_phases}\n"
            f"  Modbus IP: {self.config.modbus.ip}:{self.config.modbus.port}\n"
            f"  Device Instance: {self.config.device_instance}\n"
            f"  Min Battery SOC: From Victron settings\n"
            f"  Min Charge Duration: "
            f"{self.config.controls.min_charge_duration_seconds}s\n"
            f"  Schedules Configured: {len(self.schedules)} active"
        )

        # Log schedule details if any are configured
        if self.schedules:
            for idx, schedule in enumerate(self.schedules):
                if schedule.enabled:
                    self.logger.info(
                        f"  Schedule {idx+1}: {schedule.start} - {schedule.end}, "
                        f"Days: {bin(schedule.days_mask)[2:].zfill(7)}"
                    )

    def _persist_state(self) -> None:
        """Persist current state to disk."""
        self.persistence.update(
            {
                "mode": self.current_mode.value,
                "start_stop": self.start_stop.value,
                "set_current": self.intended_set_current.value,
                "charging_start_time": self.charging_start_time,
                "insufficient_solar_start": self.insufficient_solar_start,
            }
        )

        # Save session state
        self.persistence.set_section("session", self.session_manager.get_state())

        self.persistence.save_state()

    def _apply_current_change(
        self,
        change_source: str,
        requested_current: Optional[float] = None,
        force_verify: bool = True,
    ) -> bool:
        """
        Apply current change from callbacks - consolidated logic.

        Args:
            change_source: Source of the change (mode, startstop, setcurrent)
            requested_current: Requested current value (for setcurrent callback)
            force_verify: Whether to force verification of the change

        Returns:
            True if change was applied successfully
        """
        now = time.time()

        # Read active phases from charger
        self.active_phases = read_active_phases(self.client, self.config)

        # Compute effective current based on mode and state
        (
            effective_current,
            explanation,
            self.insufficient_solar_start,
            low_soc,
        ) = compute_effective_current(
            EVC_MODE(self.current_mode.value),
            EVC_CHARGE(self.start_stop.value),
            self.intended_set_current.value,
            self.station_max_current,
            now,
            self.schedules,
            0.0,  # ev_power - will be set in AUTO mode
            self.config.timezone,
            self.insufficient_solar_start,
            self.config.controls.min_charge_duration_seconds,
            self.active_phases,
        )

        # Apply the current setting
        if self._set_current_with_logging(
            effective_current,
            explanation,
            force_verify=force_verify,
            source=change_source,
        ):
            self.last_current_set_time = now
            self.last_sent_current = effective_current
            if effective_current >= ChargingLimits.MIN_CURRENT:
                self.last_positive_set_time = now

            # Build log message
            mode_name = EVC_MODE(self.current_mode.value).name
            log_msg = (
                f"{change_source} applied current: "
                f"{effective_current:.2f} A ({mode_name})"
            )

            # Add clamping info if applicable
            if (
                requested_current is not None
                and abs(effective_current - requested_current) > 0.01
            ):
                log_msg += f" (adjusted from {requested_current:.2f} A)"

            log_msg += f". Reason: {explanation}"
            self.logger.info(log_msg)
            return True
        else:
            self.logger.warning(f"Failed to apply current on {change_source}")
            return False

    def mode_callback(self, path: str, value: Any) -> bool:
        """Handle mode change callback."""
        try:
            self.current_mode.value = int(value)
            self._persist_state()

            # Apply immediate current change for MANUAL mode
            if self.current_mode.value == EVC_MODE.MANUAL.value:
                self._apply_current_change("Mode change", force_verify=True)

            self.logger.info(
                f"Mode changed to {EVC_MODE(self.current_mode.value).name}"
            )
            return True
        except (ValueError, TypeError):
            return False

    def startstop_callback(self, path: str, value: Any) -> bool:
        """Handle start/stop change callback."""
        try:
            self.start_stop.value = int(value)
            self._persist_state()

            # Apply current change based on mode
            target = (
                self.intended_set_current.value
                if self.start_stop.value == EVC_CHARGE.ENABLED.value
                else 0.0
            )
            self._apply_current_change("StartStop change", target, force_verify=True)

            action = (
                "enabled"
                if self.start_stop.value == EVC_CHARGE.ENABLED.value
                else "disabled"
            )
            self.logger.info(f"Charging {action}")
            return True
        except (ValueError, TypeError):
            return False

    def set_current_callback(self, path: str, value: Any) -> bool:
        """Handle set current callback."""
        try:
            requested = max(0.0, min(ChargingLimits.MAX_CURRENT, float(value)))

            # Update station max current
            self.station_max_current = update_station_max_current(
                self.client,
                self.config,
                self.service,
                self.config.defaults,
                self.logger,
            )

            self.intended_set_current.value = requested
            self.service["/SetCurrent"] = round(self.intended_set_current.value, 1)
            self._persist_state()

            # Apply immediate change for MANUAL mode
            if self.current_mode.value == EVC_MODE.MANUAL.value:
                self._apply_current_change(
                    "SetCurrent change", requested, force_verify=True
                )

            self.logger.info(
                f"SetCurrent changed to {self.intended_set_current.value:.2f} A"
            )
            return True
        except (ValueError, ModbusException) as e:
            self.logger.error(f"Set current error: {e}")
            if isinstance(e, ModbusException):
                reconnect(self.client, self.logger)
            return False

    def fetch_raw_data(self) -> Dict[str, Optional[List[int]]]:
        """Fetch raw data from Modbus registers."""
        raw_data: Dict[str, Optional[List[int]]] = {}

        # Try to read socket data (slave ID 1) - usually more reliable
        try:
            # Read voltages
            raw_data["voltages"] = read_holding_registers(
                self.client,
                ModbusRegisters.VOLTAGES_L1,
                6,
                self.config.modbus.socket_slave_id,
            )
        except Exception as e:
            self.logger.debug(f"Could not read voltages: {e}")
            raw_data["voltages"] = None

        try:
            # Read currents
            raw_data["currents"] = read_holding_registers(
                self.client,
                ModbusRegisters.CURRENTS_L1,
                6,
                self.config.modbus.socket_slave_id,
            )
        except Exception as e:
            self.logger.debug(f"Could not read currents: {e}")
            raw_data["currents"] = None

        try:
            # Read power
            raw_data["power"] = read_holding_registers(
                self.client,
                ModbusRegisters.ACTIVE_POWER_TOTAL,
                8,
                self.config.modbus.socket_slave_id,
            )
        except Exception as e:
            self.logger.debug(f"Could not read power: {e}")
            raw_data["power"] = None

        try:
            # Read energy
            raw_data["energy"] = read_holding_registers(
                self.client,
                ModbusRegisters.METER_ACTIVE_ENERGY_TOTAL,
                4,
                self.config.modbus.socket_slave_id,
            )
        except Exception as e:
            self.logger.debug(f"Could not read energy: {e}")
            raw_data["energy"] = None

        try:
            # Read socket status (slave ID 1) - Mode 3 state
            raw_data["socket_status"] = read_holding_registers(
                self.client,
                ModbusRegisters.SOCKET_MODE3_STATE,
                5,  # 5 registers for the state string
                self.config.modbus.socket_slave_id,
            )
        except Exception as e:
            self.logger.debug(f"Could not read socket status: {e}")
            raw_data["socket_status"] = None

        # Check if we got any data at all
        if all(v is None for v in raw_data.values()):
            raise ModbusError("read", "Failed to read any Modbus data")

        return raw_data

    def process_logic(self, raw_data: Dict[str, Optional[List[int]]]) -> None:
        """Process business logic based on raw data."""
        # Get power and energy values
        power_w = 0.0
        energy_kwh = 0.0

        power_data = raw_data.get("power")
        if power_data and len(power_data) >= 4:
            try:
                power_w = decode_64bit_float(power_data[:4])
            except Exception as e:
                self.logger.debug(f"Could not decode power: {e}")
                power_w = 0.0

        energy_data = raw_data.get("energy")
        if energy_data and len(energy_data) >= 4:
            try:
                energy_kwh = decode_64bit_float(energy_data) / 1000.0
            except Exception as e:
                self.logger.debug(f"Could not decode energy: {e}")
                energy_kwh = 0.0

        # Update session manager
        self.session_manager.update(power_w, energy_kwh)

        # Get and update status
        try:
            current_status = get_complete_status(
                self.client,
                self.config,
                EVC_MODE(self.current_mode.value),
                self.active_phases,
            )

            # Update D-Bus status
            self.service["/Status"] = current_status

            # Log status changes
            if current_status != self.last_status:
                status_names = {
                    0: "Disconnected",
                    1: "Connected",
                    2: "Charging",
                    7: "Low SOC",
                }
                old_name = status_names.get(
                    self.last_status, f"Unknown({self.last_status})"
                )
                new_name = status_names.get(
                    current_status, f"Unknown({current_status})"
                )

                self.logger.info(f"EV Status changed: {old_name} -> {new_name}")

                # Log additional context for connection
                if current_status == 1 and self.last_status == 0:
                    self.logger.info("Car connected, waiting for charging to start")
                    # Note: Battery SOC check is handled in Auto mode calculation
                elif current_status == 0 and self.last_status > 0:
                    self.logger.info("Car disconnected")
                elif current_status == 2 and self.last_status < 2:
                    self.logger.info("Charging started")
                elif current_status < 2 and self.last_status == 2:
                    self.logger.info("Charging stopped")

                self.last_status = current_status
        except Exception as e:
            self.logger.debug(f"Failed to read charger status: {e}")

        # Simple session detection based on power
        was_charging = self.charging_start_time > 0
        is_charging = power_w > 100  # Charging if power > 100W

        # Detect session changes based on power
        if not was_charging and is_charging:
            # New session started
            self.charging_start_time = time.time()
            self._persist_state()
            self.logger.info("Power-based: New charging session started")
        elif was_charging and not is_charging:
            # Session ended
            self.charging_start_time = 0
            self._persist_state()
            self.logger.info("Power-based: Charging session ended")

    def update_dbus_paths(self, raw_data: Dict[str, Optional[List[int]]]) -> None:
        """Update D-Bus paths with fetched data."""
        # Update voltages
        voltages = raw_data.get("voltages")
        if voltages and len(voltages) >= 8:
            self.service["/Ac/L1/Voltage"] = round(decode_64bit_float(voltages[:4]), 1)
            self.service["/Ac/L2/Voltage"] = round(decode_64bit_float(voltages[2:6]), 1)
            self.service["/Ac/L3/Voltage"] = round(decode_64bit_float(voltages[4:8]), 1)

        # Update currents
        currents = raw_data.get("currents")
        if currents and len(currents) >= 8:
            self.service["/Ac/L1/Current"] = round(decode_64bit_float(currents[:4]), 2)
            self.service["/Ac/L2/Current"] = round(decode_64bit_float(currents[2:6]), 2)
            self.service["/Ac/L3/Current"] = round(decode_64bit_float(currents[4:8]), 2)

        # Update power
        power = raw_data.get("power")
        if power and len(power) >= 10:
            total_power = decode_64bit_float(power[:4])
            self.service["/Ac/Power"] = round(total_power, 0)
            self.service["/Ac/L1/Power"] = round(decode_64bit_float(power[2:6]), 0)
            self.service["/Ac/L2/Power"] = round(decode_64bit_float(power[4:8]), 0)
            self.service["/Ac/L3/Power"] = round(decode_64bit_float(power[6:10]), 0)

        # Update energy
        energy = raw_data.get("energy")
        if energy and len(energy) >= 4:
            energy_kwh = decode_64bit_float(energy) / 1000.0
            self.service["/Ac/Energy/Forward"] = round(energy_kwh, 3)

        # Update session stats
        stats = self.session_manager.get_session_stats()
        duration_min = stats.get("session_duration_min", 0)
        if isinstance(duration_min, (int, float)):
            self.service["/ChargingTime"] = int(duration_min * 60)
        else:
            self.service["/ChargingTime"] = 0

    def apply_controls(self) -> None:
        """Apply control logic based on current mode."""
        now = time.time()

        # Force update if watchdog interval has elapsed
        force_update = (
            now - self.last_current_set_time
            >= self.config.controls.watchdog_interval_seconds
        )

        # Get current power for AUTO mode
        try:
            ev_power = self.service["/Ac/Power"]
        except (KeyError, AttributeError):
            ev_power = 0.0

        # Read active phases from charger
        self.active_phases = read_active_phases(self.client, self.config)

        # Compute and apply effective current
        (
            effective_current,
            explanation,
            self.insufficient_solar_start,
            low_soc,
        ) = compute_effective_current(
            EVC_MODE(self.current_mode.value),
            EVC_CHARGE(self.start_stop.value),
            self.intended_set_current.value,
            self.station_max_current,
            now,
            self.schedules,
            ev_power,
            self.config.timezone,
            self.insufficient_solar_start,
            self.config.controls.min_charge_duration_seconds,
            self.active_phases,
            self.last_positive_set_time,
        )

        # Update if different from last sent OR if watchdog interval elapsed
        if abs(effective_current - self.last_sent_current) > 0.1 or force_update:
            source = "Watchdog update" if force_update else "Polling update"
            if self._set_current_with_logging(
                effective_current, explanation, force_verify=False, source=source
            ):
                self.last_current_set_time = now
                self.last_sent_current = effective_current
                if effective_current >= ChargingLimits.MIN_CURRENT:
                    self.last_positive_set_time = now
                watchdog_note = " (Watchdog update)" if force_update else ""
                self.logger.debug(
                    f"Applied control current: {effective_current:.2f} A. "
                    f"{explanation}{watchdog_note}"
                )

    def poll(self) -> bool:
        """Main polling loop iteration."""
        try:
            # Ensure connection
            if not self.client.is_socket_open():
                if not self.client.connect():
                    raise ModbusError("connection", "Failed to connect to Modbus TCP")

            # Fetch data
            raw_data = self.fetch_raw_data()

            # Process logic
            self.process_logic(raw_data)

            # Update D-Bus
            self.update_dbus_paths(raw_data)

            # Apply controls
            self.apply_controls()

            # Persist state periodically
            if time.time() - self.last_poll_time > 60:  # Every minute
                self._persist_state()
                self.last_poll_time = time.time()

            return True

        except ModbusException as e:
            self.logger.error(f"Modbus error in poll: {e}")
            reconnect(self.client, self.logger)
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error in poll: {e}")
            return False

    def run(self) -> None:
        """Run the main driver loop."""
        # Schedule first poll
        GLib.timeout_add(PollingIntervals.DEFAULT, self.poll)

        # Start main loop
        mainloop = GLib.MainLoop()
        mainloop.run()
