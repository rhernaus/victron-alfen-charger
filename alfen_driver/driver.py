#!/usr/bin/env python3
"""Simplified Alfen EV Charger driver for Victron Venus OS."""

import dataclasses
import os
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

sys.path.insert(
    1, os.path.join(os.path.dirname(__file__), "/opt/victronenergy/dbus-modbus-client")
)

import yaml  # noqa: E402
from gi.repository import GLib  # noqa: E402
from pymodbus.client import ModbusTcpClient  # noqa: E402
from pymodbus.exceptions import ModbusException  # noqa: E402

from .config import CONFIG_PATH, Config, load_config  # noqa: E402
from .config_validator import ConfigValidator  # noqa: E402
from .constants import (  # noqa: E402
    ChargingLimits,
    ModbusRegisters,
    PollingIntervals,
)
from .controls import (  # noqa: E402
    set_current,
    update_station_max_current,
)
from .dbus_utils import (  # noqa: E402
    EVC_CHARGE,
    EVC_MODE,
    EVC_STATUS,  # noqa: E402
    register_dbus_service,
)
from .exceptions import ModbusError  # noqa: E402
from .logging_utils import (  # noqa: E402
    LogContext,
    get_logger,
    set_context,
    setup_root_logging,
)
from .logic import (  # noqa: E402
    apply_mode_specific_status,  # noqa: E402
    compute_effective_current,
    get_complete_status,
    read_active_phases,
)
from .logic import (  # noqa: E402
    set_config as set_logic_config,
)
from .modbus_utils import (  # noqa: E402
    decode_32bit_float,
    decode_64bit_float,
    read_holding_registers,
    read_modbus_string,
    reconnect,
)
from .persistence import PersistenceManager  # noqa: E402
from .session_manager import ChargingSessionManager  # noqa: E402
from .tibber import get_hourly_overview_text  # noqa: E402

try:
    import dbus
except ImportError:  # pragma: no cover
    dbus = None


class MutableValue:
    """Simple mutable value wrapper for callbacks."""

    def __init__(self, value: Any) -> None:
        self.value = value


class AlfenDriver:
    """Simplified Alfen EV Charger driver."""

    def __init__(self) -> None:
        """Initialize the driver with configuration and components."""
        # Load configuration
        self.config: Config = load_config()

        # Track config file path for persistence
        self.config_file_path = self._determine_config_file_path()

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

        # HTTP status snapshot state for web server readers
        self.status_lock = threading.Lock()
        # Initialize a non-empty default snapshot so /api/status is useful before first poll
        self.status_snapshot: Dict[str, Any] = {
            "mode": int(self.current_mode.value),
            "start_stop": int(self.start_stop.value),
            "set_current": float(self.intended_set_current.value),
            "station_max_current": float(self.station_max_current),
            "status": 0,
            "ac_current": 0.0,
            "ac_power": 0.0,
            "energy_forward_kwh": 0.0,
            "l1_voltage": 0.0,
            "l2_voltage": 0.0,
            "l3_voltage": 0.0,
            "l1_current": 0.0,
            "l2_current": 0.0,
            "l3_current": 0.0,
            "l1_power": 0.0,
            "l2_power": 0.0,
            "l3_power": 0.0,
            "active_phases": 0,
            "charging_time_sec": 0,
            "charging_time": 0,
            "firmware": "",
            "serial": "",
            "product_name": "Alfen EV Charger",
            "device_instance": int(self.config.device_instance),
            "session": {},
        }

        self.logger.info("Driver initialization complete")

    def _merge_status_snapshot(self, updates: Dict[str, Any]) -> None:
        """Safely merge partial updates into the HTTP status snapshot.

        This ensures the web UI reflects immediate state changes (like
        mode/start-stop/current) even if Modbus polling is failing or has
        not yet run again.
        """
        try:
            with self.status_lock:
                current = dict(self.status_snapshot)
                current.update(updates)
                self.status_snapshot = current
        except Exception as exc:
            # Do not allow snapshot issues to interrupt callbacks; log at debug
            self.logger.debug(f"snapshot merge failed: {exc}")

    def _determine_config_file_path(self) -> str:
        """Determine the active configuration file path used by the driver."""
        local_path = os.path.join(os.getcwd(), "alfen_driver_config.yaml")
        if os.path.exists(local_path):
            return local_path
        if os.path.exists(CONFIG_PATH):
            return os.path.abspath(CONFIG_PATH)
        # Fallback to local path
        return local_path

    def get_config_dict(self) -> Dict[str, Any]:
        """Return the current configuration as a dictionary."""
        return dataclasses.asdict(self.config)

    def apply_config_from_dict(self, new_config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Validate, persist, and apply a new configuration at runtime.

        Returns a result dict: { ok: bool, error: Optional[str] }
        """
        try:
            # Validate
            validator = ConfigValidator()
            validator.validate_or_raise(new_config_dict)

            # Persist to YAML (atomically)
            temp_path = f"{self.config_file_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(new_config_dict, f, sort_keys=False)
            os.replace(temp_path, self.config_file_path)

            # Recreate Config instance
            new_config = Config.from_dict(new_config_dict)

            # If Modbus connection parameters changed, recreate client
            old_host = self.config.modbus.ip
            old_port = self.config.modbus.port
            new_host = new_config.modbus.ip
            new_port = new_config.modbus.port
            if old_host != new_host or old_port != new_port:
                try:
                    self.client.close()
                except Exception as exc:
                    self.logger.debug(f"Error closing Modbus client: {exc}")
                self.client = ModbusTcpClient(host=new_host, port=new_port)

            # Swap config and propagate
            self.config = new_config
            set_logic_config(self.config)
            self.schedules = (
                self.config.schedule.items if hasattr(self.config, "schedule") else []
            )

            # Refresh charger parameters (max current, phases, status)
            self._read_charger_parameters()

            # Update snapshot with new device instance, etc.
            with self.status_lock:
                current = dict(self.status_snapshot)
                current["device_instance"] = int(self.config.device_instance)
                self.status_snapshot = current

            # Reflect limits that may have changed due to config apply
            self._merge_status_snapshot(
                {
                    "station_max_current": float(self.station_max_current),
                    "set_current": float(self.intended_set_current.value),
                }
            )

            return {"ok": True}
        except Exception as exc:  # pragma: no cover - runtime safe-guard
            return {"ok": False, "error": str(exc)}

    def _init_state(self) -> None:
        """Initialize driver state variables."""

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

        # Track hourly overview emission to avoid spam; store last hour key
        self._last_overview_hour_key: Optional[str] = None

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
        if self.current_mode.value == EVC_MODE.AUTO.value:
            msg_mode = "Auto"
        elif self.current_mode.value == EVC_MODE.SCHEDULED.value:
            msg_mode = "Scheduled"
        else:
            msg_mode = "Manual"

        requested_current = None
        if source == "SetCurrent change":
            requested_current = self.intended_set_current.value

        success = set_current(
            self.client,
            self.config,
            effective_current,
            self.station_max_current,
            force_verify=force_verify,
        )

        if success:
            log_msg = f"{source}: Applied {effective_current:.2f} A in {msg_mode} mode"
            if (
                requested_current is not None
                and abs(effective_current - requested_current) > 0.01
            ):
                log_msg += f" (adjusted from {requested_current:.2f} A)"

            log_msg += f". Reason: {explanation}"
            self.logger.info(log_msg)
            return True
        else:
            self.logger.warning(f"Failed to apply current on {source}")
            return False

    def mode_callback(self, path: str, value: Any) -> bool:
        """Handle mode change callback."""
        try:
            self.current_mode.value = int(value)
            self._persist_state()

            # Update HTTP snapshot immediately
            self._merge_status_snapshot({"mode": int(self.current_mode.value)})

            # Apply immediate current change for MANUAL mode
            if self.current_mode.value == EVC_MODE.MANUAL.value:
                self._apply_current_change("Mode change", force_verify=True)

            self.logger.info(
                f"Mode changed to {EVC_MODE(self.current_mode.value).name}"
            )

            # When switching to SCHEDULED, print an overview immediately
            if (
                self.current_mode.value == EVC_MODE.SCHEDULED.value
                and getattr(self.config, "tibber", None)
                and self.config.tibber.enabled
            ):
                try:
                    overview = get_hourly_overview_text(self.config.tibber)
                    if overview:
                        self.logger.info(overview)
                except Exception as e:
                    self.logger.debug(
                        f"Failed to log Tibber overview on mode change: {e}"
                    )
            return True
        except (ValueError, TypeError):
            return False

    def startstop_callback(self, path: str, value: Any) -> bool:
        """Handle start/stop change callback."""
        try:
            self.start_stop.value = int(value)
            self._persist_state()

            # Update HTTP snapshot immediately
            self._merge_status_snapshot({"start_stop": int(self.start_stop.value)})

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

            # Update HTTP snapshot immediately
            self._merge_status_snapshot(
                {"set_current": float(self.intended_set_current.value)}
            )

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
            # Read voltages for L1..L3 (6 registers -> 3 floats)
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
            # Read currents for L1..L3 (6 registers -> 3 floats)
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
            # Read power block (we read 8 registers: 3 phases + total)
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
            # Read energy (64-bit float => 4 registers)
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
            # Read socket status (Mode 3 state string, 5 registers)
            raw_data["socket_status"] = read_holding_registers(
                self.client,
                ModbusRegisters.SOCKET_MODE3_STATE,
                5,
                self.config.modbus.socket_slave_id,
            )
        except Exception as e:
            self.logger.debug(f"Could not read socket status: {e}")
            raw_data["socket_status"] = None

        # Check if we got any data at all
        if all(v is None for v in raw_data.values()):
            raise ModbusError("read", "Failed to read any Modbus data")

        return raw_data

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
                4: "Wait Sun",
                6: "Wait Start",
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

        # Restore other state
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

    def process_logic(self, raw_data: Dict[str, Optional[List[int]]]) -> None:
        """Process business logic based on raw data."""
        # Get power and energy values
        power_w = 0.0
        energy_kwh = 0.0

        power_data = raw_data.get("power")
        if power_data and len(power_data) >= 8:
            try:
                # Total power is in the last two registers of the 8-register block
                power_w = decode_32bit_float(power_data[6:8])
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

        # Persist state if session state changed
        prev_session_active = self.session_manager.current_session is not None

        # Update session manager
        self.session_manager.update(power_w, energy_kwh)

        # Check if session state changed and persist if needed
        curr_session_active = self.session_manager.current_session is not None
        if prev_session_active != curr_session_active:
            self._persist_state()

    def update_dbus_paths(self, raw_data: Dict[str, Optional[List[int]]]) -> None:
        """Update D-Bus paths with fetched data."""
        # Update voltages
        voltages = raw_data.get("voltages")
        if voltages and len(voltages) >= 6:
            self.service["/Ac/L1/Voltage"] = round(decode_32bit_float(voltages[0:2]), 1)
            self.service["/Ac/L2/Voltage"] = round(decode_32bit_float(voltages[2:4]), 1)
            self.service["/Ac/L3/Voltage"] = round(decode_32bit_float(voltages[4:6]), 1)

        # Update currents
        currents = raw_data.get("currents")
        if currents and len(currents) >= 6:
            i1 = decode_32bit_float(currents[0:2])
            i2 = decode_32bit_float(currents[2:4])
            i3 = decode_32bit_float(currents[4:6])
            self.service["/Ac/L1/Current"] = round(i1, 2)
            self.service["/Ac/L2/Current"] = round(i2, 2)
            self.service["/Ac/L3/Current"] = round(i3, 2)
            # Set /Ac/Current to max phase current
            # (likely what Victron displays as charging current)
            max_current = round(max(i1, i2, i3), 2)
            self.service["/Ac/Current"] = max_current
            # Also update /Current for Victron UI display
            self.service["/Current"] = max_current

        # Update power
        power = raw_data.get("power")
        if power and len(power) >= 8:
            self.service["/Ac/L1/Power"] = round(decode_32bit_float(power[0:2]), 0)
            self.service["/Ac/L2/Power"] = round(decode_32bit_float(power[2:4]), 0)
            self.service["/Ac/L3/Power"] = round(decode_32bit_float(power[4:6]), 0)
            total_power = decode_32bit_float(power[6:8])
            self.service["/Ac/Power"] = round(total_power, 0)

        # Update energy (session-based)
        energy = raw_data.get("energy")
        energy_kwh = 0.0
        if energy and len(energy) >= 4:
            energy_kwh = decode_64bit_float(energy) / 1000.0
        if self.session_manager.current_session:
            session_energy = max(
                0.0, energy_kwh - self.session_manager.current_session.start_energy_kwh
            )
            self.service["/Ac/Energy/Forward"] = round(session_energy, 3)
        elif (
            self.session_manager.last_session
            and self.session_manager.last_session.end_energy_kwh is not None
        ):
            self.service["/Ac/Energy/Forward"] = round(
                self.session_manager.last_session.energy_delivered_kwh, 3
            )
        else:
            self.service["/Ac/Energy/Forward"] = 0.0

        # Update session stats
        stats = self.session_manager.get_session_stats()
        duration_min = stats.get("session_duration_min", 0)
        if isinstance(duration_min, (int, float)):
            self.service["/ChargingTime"] = int(duration_min * 60)
        else:
            self.service["/ChargingTime"] = 0

        # Build HTTP status snapshot for web UI
        try:
            snapshot: Dict[str, Any] = {}
            snapshot["mode"] = int(self.current_mode.value)
            snapshot["start_stop"] = int(self.start_stop.value)
            snapshot["set_current"] = float(self.intended_set_current.value)
            snapshot["station_max_current"] = float(self.station_max_current)
            snapshot["status"] = (
                int(self.service.get("/Status", 0)) if hasattr(self, "service") else 0
            )
            snapshot["ac_current"] = float(self.service.get("/Ac/Current", 0.0))
            snapshot["ac_power"] = float(self.service.get("/Ac/Power", 0.0))
            snapshot["energy_forward_kwh"] = float(
                self.service.get("/Ac/Energy/Forward", 0.0)
            )
            snapshot["l1_voltage"] = float(self.service.get("/Ac/L1/Voltage", 0.0))
            snapshot["l2_voltage"] = float(self.service.get("/Ac/L2/Voltage", 0.0))
            snapshot["l3_voltage"] = float(self.service.get("/Ac/L3/Voltage", 0.0))
            snapshot["l1_current"] = float(self.service.get("/Ac/L1/Current", 0.0))
            snapshot["l2_current"] = float(self.service.get("/Ac/L2/Current", 0.0))
            snapshot["l3_current"] = float(self.service.get("/Ac/L3/Current", 0.0))
            snapshot["l1_power"] = float(self.service.get("/Ac/L1/Power", 0.0))
            snapshot["l2_power"] = float(self.service.get("/Ac/L2/Power", 0.0))
            snapshot["l3_power"] = float(self.service.get("/Ac/L3/Power", 0.0))
            snapshot["active_phases"] = int(getattr(self, "active_phases", 0) or 0)
            snapshot["charging_time_sec"] = int(self.service.get("/ChargingTime", 0))
            snapshot["firmware"] = str(self.service.get("/FirmwareVersion", ""))
            snapshot["serial"] = str(self.service.get("/Serial", ""))
            snapshot["product_name"] = str(self.service.get("/ProductName", ""))
            snapshot["device_instance"] = int(self.config.device_instance)

            if (
                hasattr(self.session_manager, "current_session")
                and self.session_manager.current_session is not None
            ):
                cs = self.session_manager.current_session
                snapshot["session"] = {
                    "start_ts": cs.start_time.isoformat(),
                    "start_energy_kwh": cs.start_energy_kwh,
                }
            elif self.session_manager.last_session is not None:
                ls = self.session_manager.last_session
                snapshot["session"] = {
                    "start_ts": ls.start_time.isoformat(),
                    "end_ts": ls.end_time.isoformat()
                    if ls.end_time is not None
                    else None,
                    "energy_delivered_kwh": ls.energy_delivered_kwh,
                }
            else:
                snapshot["session"] = {}

            with self.status_lock:
                self.status_snapshot = snapshot
        except Exception as e:
            self.logger.debug(f"Failed to update HTTP snapshot: {e}")

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
            self.last_positive_set_time,
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

        # If in SCHEDULED (with Tibber enabled), emit an hourly overview at top of the hour
        try:
            if (
                self.current_mode.value == EVC_MODE.SCHEDULED.value
                and getattr(self.config, "tibber", None)
                and self.config.tibber.enabled
            ):
                # Build an hour key in local timezone to avoid multiple logs within same hour
                try:
                    import datetime as _dt

                    import pytz as _pytz

                    tz = _pytz.timezone(self.config.timezone)
                    local_dt = _dt.datetime.fromtimestamp(now, tz)
                    hour_key = local_dt.strftime("%Y-%m-%d %H")
                    is_top_of_hour = local_dt.minute == 0 and local_dt.second < 2
                except Exception:
                    # Fallback using UTC
                    hour_key = time.strftime("%Y-%m-%d %H", time.gmtime(now))
                    tm = time.gmtime(now)
                    is_top_of_hour = tm.tm_min == 0 and tm.tm_sec < 2

                if is_top_of_hour and hour_key != self._last_overview_hour_key:
                    overview = get_hourly_overview_text(self.config.tibber)
                    if overview:
                        self.logger.info(overview)
                    self._last_overview_hour_key = hour_key
        except Exception as e:
            self.logger.debug(f"Failed to emit hourly Tibber overview: {e}")

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

        # Update Victron status with mode-specific adjustments (e.g. WAIT_SUN, LOW_SOC)
        try:
            base_status = get_complete_status(
                self.client,
                self.config,
                EVC_MODE(self.current_mode.value),
                self.active_phases,
            )
            connected_flag = base_status != EVC_STATUS.DISCONNECTED
            final_status = apply_mode_specific_status(
                EVC_MODE(self.current_mode.value),
                connected_flag,
                EVC_CHARGE(self.start_stop.value),
                self.intended_set_current.value,
                self.schedules,
                base_status,
                self.config.timezone,
                effective_current=effective_current,
            )

            if final_status != self.last_status:
                status_names = {
                    0: "Disconnected",
                    1: "Connected",
                    2: "Charging",
                    4: "Wait Sun",
                    6: "Wait Start",
                    7: "Low SOC",
                }
                old_name = status_names.get(
                    self.last_status, f"Unknown({self.last_status})"
                )
                new_name = status_names.get(final_status, f"Unknown({final_status})")
                self.logger.info(f"EV Status changed: {old_name} -> {new_name}")

            self.service["/Status"] = final_status
            self.last_status = final_status
        except Exception as e:
            self.logger.debug(f"Failed to update status: {e}")

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
            # Keep periodic polling alive; try again on next tick
            return True
        except Exception as e:
            self.logger.error(f"Unexpected error in poll: {e}")
            # Avoid stopping the GLib timeout; continue polling
            return True

    def run(self) -> None:
        """Run the main driver loop."""
        # Schedule first poll
        GLib.timeout_add(PollingIntervals.DEFAULT, self.poll)

        # Start main loop
        mainloop = GLib.MainLoop()
        mainloop.run()

    def _persist_state(self) -> None:
        """Persist current state to disk."""
        self.persistence.update(
            {
                "mode": self.current_mode.value,
                "start_stop": self.start_stop.value,
                "set_current": self.intended_set_current.value,
                "insufficient_solar_start": self.insufficient_solar_start,
            }
        )
        # Save session state
        self.persistence.set_section("session", self.session_manager.get_state())
        self.persistence.save_state()
