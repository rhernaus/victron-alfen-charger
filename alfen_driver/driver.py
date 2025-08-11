#!/usr/bin/env python3

import logging
import os
import sys
import time

sys.path.insert(
    1, os.path.join(os.path.dirname(__file__), "/opt/victronenergy/dbus-modbus-client")
)

from typing import Any, Dict, List

from gi.repository import GLib
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from .config import Config, load_config, load_initial_config, persist_config_to_disk
from .controls import (
    MAX_SET_CURRENT,
    clamp_intended_current_to_max,
    set_current,
    set_effective_current,
    update_station_max_current,
)
from .dbus_utils import EVC_CHARGE, EVC_MODE, register_dbus_service
from .logic import compute_effective_current, process_status_and_energy
from .modbus_utils import read_modbus_string, reconnect

try:
    import dbus
except ImportError:  # pragma: no cover
    dbus = None


def _persist_config(self) -> None:
    persist_config_to_disk(
        self.config_file_path,
        self.current_mode,
        self.start_stop,
        self.auto_start,
        self.intended_set_current,
        self.logger,
    )


class AlfenDriver:
    POLL_INTERVAL_MS: int = 1000  # Default
    IDLE_POLL_MS: int = 5000
    ACTIVE_POLL_MS: int = 1000

    def __init__(self):
        """
        Initialize the AlfenDriver with default values and setup.

        Sets up logging, loads configuration, initializes Modbus client,
        and registers D-Bus service with paths and callbacks.
        """
        # Initialize default logging before config load
        logging.basicConfig(
            level=logging.INFO,  # Default level
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler("/var/log/alfen_driver.log"),  # Default file
                logging.StreamHandler(sys.stdout),
            ],
        )
        self.logger: logging.Logger = logging.getLogger("alfen_driver")

        self.config: Config = load_config(self.logger)

        # Reconfigure logging with loaded config values
        logging.basicConfig(
            level=self.config.logging.level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(self.config.logging.file),
                logging.StreamHandler(sys.stdout),
            ],
            force=True,  # Force reconfiguration
        )

        self.charging_start_time: float = 0
        self.last_current_set_time: float = 0
        self.session_start_energy_kwh: float = 0
        self.intended_set_current: float = self.config.defaults.intended_set_current
        self.current_mode: EVC_MODE = EVC_MODE.AUTO
        self.start_stop: EVC_CHARGE = EVC_CHARGE.DISABLED
        self.auto_start: int = 1
        self.last_sent_current: float = -1.0
        self.schedule_enabled: int = self.config.schedule.enabled
        self.schedule_days_mask: int = self.config.schedule.days_mask
        self.schedule_start: str = self.config.schedule.start
        self.schedule_end: str = self.config.schedule.end
        self.low_soc_enabled: int = self.config.low_soc.enabled
        self.low_soc_threshold: float = self.config.low_soc.threshold
        self.low_soc_hysteresis: float = self.config.low_soc.hysteresis
        self.low_soc_active: bool = False
        self.battery_soc: float | None = None
        self.dbus_bus: Any | None = None
        self.dbus_soc_obj: Any | None = None
        self.station_max_current: float = self.config.defaults.station_max_current
        self.max_current_update_counter: int = 0
        modbus_config = self.config.modbus
        self.client: ModbusTcpClient = ModbusTcpClient(
            host=modbus_config.ip, port=modbus_config.port
        )
        device_instance = self.config.device_instance
        self.service_name: str = f"com.victronenergy.evcharger.alfen_{device_instance}"
        self.config_file_path: str = f"/data/evcharger_alfen_{device_instance}.json"

        load_initial_config(
            self.service_name,
            self.current_mode,
            self.start_stop,
            self.auto_start,
            self.intended_set_current,
            self.logger,
            self.config_file_path,
        )

        self.service = register_dbus_service(
            self.service_name,
            self.config,
            self.current_mode,
            self.start_stop,
            self.auto_start,
            self.intended_set_current,
            self.schedule_enabled,
            self.schedule_days_mask,
            self.schedule_start,
            self.schedule_end,
            self.low_soc_enabled,
            self.low_soc_threshold,
            self.low_soc_hysteresis,
            self.mode_callback,
            self.startstop_callback,
            self.set_current_callback,
            self.autostart_callback,
            self.schedule_enabled_callback,
            self.schedule_days_callback,
            self.schedule_start_callback,
            self.schedule_end_callback,
            self.low_soc_enabled_callback,
            self.low_soc_threshold_callback,
            self.low_soc_hysteresis_callback,
        )

        self._load_static_info()
        self._schedule_next_poll(self.config.poll_interval_ms)

    def _schedule_next_poll(self, interval: int) -> None:
        GLib.timeout_add(interval, self.poll)

    def _load_static_info(self) -> None:
        self._read_firmware_version()
        self._read_station_serial()
        self._read_product_name()

    def _read_firmware_version(self) -> None:
        """
        Read and update firmware version from Modbus.

        Updates /FirmwareVersion D-Bus path.
        """
        fw_str = read_modbus_string(
            self.client,
            self.config.registers.firmware_version,
            self.config.registers.firmware_version_count,
            self.config.modbus.station_slave_id,
        )
        self.service["/FirmwareVersion"] = fw_str

    def _read_station_serial(self) -> None:
        """
        Read and update station serial from Modbus.

        Updates /Serial D-Bus path.
        """
        sn_str = read_modbus_string(
            self.client,
            self.config.registers.station_serial,
            self.config.registers.station_serial_count,
            self.config.modbus.station_slave_id,
        )
        self.service["/Serial"] = sn_str

    def _read_product_name(self) -> None:
        """
        Read and update product name from Modbus.

        Combines manufacturer and platform type for /ProductName.
        """
        mfg_str = read_modbus_string(
            self.client,
            self.config.registers.manufacturer,
            self.config.registers.manufacturer_count,
            self.config.modbus.station_slave_id,
        )
        pt_str = read_modbus_string(
            self.client,
            self.config.registers.platform_type,
            self.config.registers.platform_type_count,
            self.config.modbus.station_slave_id,
        )
        self.service["/ProductName"] = f"{mfg_str} {pt_str}"

    def _ensure_soc_proxy(self) -> bool:
        """
        Ensure D-Bus proxy for battery SOC is initialized.

        Returns:
            True if proxy is ready, False otherwise.

        Raises:
            dbus.DBusException: If connection fails (caught and returns False).
        """
        if dbus is None:
            return False
        try:
            if self.dbus_bus is None:
                self.dbus_bus = dbus.SystemBus()
            if self.dbus_soc_obj is None:
                soc_path = self.config.low_soc.battery_soc_dbus_path
                self.dbus_soc_obj = self.dbus_bus.get_object(
                    "com.victronenergy.system", soc_path
                )
            return True
        except dbus.DBusException:
            self.dbus_soc_obj = None
            return False

    def _read_battery_soc(self) -> float | None:
        """
        Read battery SOC from D-Bus.

        Returns:
            The SOC value as float, or None if unavailable.

        Raises:
            dbus.DBusException: Handled and logged, returns None.
        """
        try:
            if not self._ensure_soc_proxy():
                self.logger.warning("Unable to connect to battery SOC D-Bus path.")
                return None
            val = self.dbus_soc_obj.GetValue(dbus_interface="com.victronenergy.BusItem")
            if val is None or not isinstance(val, (int, float)):
                return None
            return float(val)
        except dbus.DBusException as e:
            self.logger.error(f"Error reading battery SOC: {e}")
            return None

    def mode_callback(self, path: str, value: Any) -> bool:
        try:
            self.current_mode = EVC_MODE(int(value))
            self._persist_config()
            now = time.time()
            effective_current = compute_effective_current(
                self.current_mode,
                self.start_stop,
                self.intended_set_current,
                self.low_soc_enabled,
                self.low_soc_active,
                self.station_max_current,
                now,
                self.schedule_enabled,
                self.schedule_days_mask,
                self.schedule_start,
                self.schedule_end,
            )
            if self.current_mode == EVC_MODE.MANUAL:
                if self.intended_set_current > self.station_max_current:
                    self.intended_set_current = self.station_max_current
                    self.service["/SetCurrent"] = round(self.intended_set_current, 1)
                    self._persist_config()
                    self.logger.info(
                        f"Clamped /SetCurrent to station max: {self.intended_set_current:.1f} A "
                        f"(on MANUAL mode)"
                    )
            if set_current(
                self.client,
                self.config,
                effective_current,
                self.station_max_current,
                force_verify=True,
            ):
                self.last_current_set_time = now
                self.last_sent_current = effective_current
                self.logger.info(
                    f"Immediate Mode change applied current: {effective_current:.2f} A (mode={self.current_mode.name})"
                )
            self.logger.info(f"Mode changed to {self.current_mode.name}")
            return True
        except (ValueError, TypeError):
            return False

    def startstop_callback(self, path: str, value: Any) -> bool:
        try:
            self.start_stop = EVC_CHARGE(int(value))
            self._persist_config()
            if self.current_mode == EVC_MODE.MANUAL:
                target = (
                    self.intended_set_current
                    if self.start_stop == EVC_CHARGE.ENABLED
                    else 0.0
                )
                if set_current(
                    self.client,
                    self.config,
                    target,
                    self.station_max_current,
                    force_verify=True,
                ):
                    self.last_current_set_time = time.time()
                    self.last_sent_current = target
                    self.logger.info(
                        f"Immediate StartStop change applied: {target:.2f} A (StartStop={self.start_stop.name})"
                    )
            self.logger.info(f"StartStop changed to {self.start_stop.name}")
            return True
        except (ValueError, TypeError):
            return False

    def set_current_callback(self, path: str, value: Any) -> bool:
        try:
            requested = max(
                0.0, min(self.config.controls.max_set_current, float(value))
            )
            self.station_max_current = update_station_max_current(
                self.client,
                self.config,
                self.service,
                self.config.defaults,
                self.logger,
            )
            max_allowed = max(0.0, float(self.station_max_current))
            self.intended_set_current = min(requested, max_allowed)
            self.service["/SetCurrent"] = round(self.intended_set_current, 1)
            self.logger.info(
                f"GUI request to set intended current to {self.intended_set_current:.2f} A"
            )
            self._persist_config()

            if self.current_mode == EVC_MODE.MANUAL:
                target = (
                    self.intended_set_current
                    if self.start_stop == EVC_CHARGE.ENABLED
                    else 0.0
                )
                if target > self.station_max_current:
                    target = self.station_max_current
                if set_current(
                    self.client, self.config, target, self.station_max_current
                ):
                    self.last_current_set_time = time.time()
                    self.last_sent_current = target
                    self.logger.info(
                        f"Immediate SetCurrent applied: {target:.2f} A (MANUAL)"
                    )
            self.logger.info(f"SetCurrent changed to {self.intended_set_current:.2f} A")
            return True
        except ValueError as e:
            self.logger.error(f"Set current value error: {e}")
            return False
        except ModbusException as e:
            self.logger.error(f"Set current Modbus error: {e}")
            reconnect(self.client, self.logger)
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error in set_current_callback: {e}")
            return False

    def autostart_callback(self, path: str, value: Any) -> bool:
        self.auto_start = int(value)
        self._persist_config()
        self.logger.info(f"AutoStart changed to {self.auto_start}")
        return True

    def schedule_enabled_callback(self, path: str, value: Any) -> bool:
        try:
            self.schedule_enabled = int(value)
            self._persist_config()
            return True
        except (ValueError, TypeError):
            return False

    def schedule_days_callback(self, path: str, value: Any) -> bool:
        try:
            self.schedule_days_mask = int(value) & 0x7F
            self._persist_config()
            return True
        except (ValueError, TypeError):
            return False

    def schedule_start_callback(self, path: str, value: Any) -> bool:
        try:
            from .config import parse_hhmm_to_minutes

            _ = parse_hhmm_to_minutes(str(value))
            self.schedule_start = str(value)
            self._persist_config()
            return True
        except (ValueError, TypeError):
            return False

    def schedule_end_callback(self, path: str, value: Any) -> bool:
        try:
            from .config import parse_hhmm_to_minutes

            _ = parse_hhmm_to_minutes(str(value))
            self.schedule_end = str(value)
            self._persist_config()
            return True
        except (ValueError, TypeError):
            return False

    def low_soc_enabled_callback(self, path: str, value: Any) -> bool:
        try:
            self.low_soc_enabled = int(value)
            self._persist_config()
            return True
        except (ValueError, TypeError):
            return False

    def low_soc_threshold_callback(self, path: str, value: Any) -> bool:
        try:
            self.low_soc_threshold = float(value)
            self._persist_config()
            return True
        except (ValueError, TypeError):
            return False

    def low_soc_hysteresis_callback(self, path: str, value: Any) -> bool:
        try:
            self.low_soc_hysteresis = max(0.0, float(value))
            self._persist_config()
            return True
        except (ValueError, TypeError):
            return False

    def fetch_raw_data(self) -> Dict[str, List[int]]:
        """
        Fetch raw register data from Modbus for AC measurements.

        Returns:
            Dictionary of raw register lists for voltages, currents, power, phases.

        Raises:
            ModbusException: If any read fails (propagated to poll).
        """
        from .modbus_utils import read_holding_registers

        return {
            "voltages": read_holding_registers(
                self.client,
                self.config.registers.voltages,
                6,
                self.config.modbus.socket_slave_id,
            ),
            "currents": read_holding_registers(
                self.client,
                self.config.registers.currents,
                6,
                self.config.modbus.socket_slave_id,
            ),
            "power": read_holding_registers(
                self.client,
                self.config.registers.power,
                2,
                self.config.modbus.socket_slave_id,
            ),
            "phases": read_holding_registers(
                self.client,
                self.config.registers.phases,
                1,
                self.config.modbus.socket_slave_id,
            ),
        }

    def process_logic(self) -> None:
        """
        Process business logic including status, energy, max current, and clamping.
        """
        self.low_soc_active, self.charging_start_time, self.session_start_energy_kwh = (
            process_status_and_energy(
                self.client,
                self.config,
                self.service,
                self.current_mode,
                self.start_stop,
                self.auto_start,
                self.intended_set_current,
                self.low_soc_enabled,
                self.low_soc_threshold,
                self.low_soc_hysteresis,
                self.low_soc_active,
                self.schedule_enabled,
                self.schedule_days_mask,
                self.schedule_start,
                self.schedule_end,
                self.station_max_current,
                self.charging_start_time,
                self.session_start_energy_kwh,
                lambda target, force_verify: set_current(
                    self.client,
                    self.config,
                    target,
                    self.station_max_current,
                    force_verify,
                ),
                lambda: self._persist_config(),
                self._read_battery_soc,
                self.logger,
            )
        )
        if self.max_current_update_counter % 10 == 0:
            self.station_max_current = update_station_max_current(
                self.client,
                self.config,
                self.service,
                self.config.defaults,
                self.logger,
            )
        self.max_current_update_counter += 1
        if self.current_mode == EVC_MODE.MANUAL:
            self.intended_set_current = clamp_intended_current_to_max(
                self.intended_set_current,
                self.station_max_current,
                self.service,
                lambda: self._persist_config(),
                self.logger,
            )

    def update_dbus_paths(self, raw_data: Dict[str, List[int]]) -> None:
        """
        Update D-Bus paths with processed data from raw registers.

        Parameters:
            raw_data: Dictionary of raw register data.
        """
        from .modbus_utils import decode_floats

        voltages = decode_floats(raw_data["voltages"], 3)
        self.service["/Ac/L1/Voltage"] = round(voltages[0], 2)
        self.service["/Ac/L2/Voltage"] = round(voltages[1], 2)
        self.service["/Ac/L3/Voltage"] = round(voltages[2], 2)

        currents = decode_floats(raw_data["currents"], 3)
        self.service["/Ac/L1/Current"] = round(currents[0], 2)
        self.service["/Ac/L2/Current"] = round(currents[1], 2)
        self.service["/Ac/L3/Current"] = round(currents[2], 2)

        current_a = round(max(currents), 2)
        self.service["/Ac/Current"] = current_a
        self.service["/Current"] = current_a

        self.service["/Ac/L1/Power"] = round(voltages[0] * currents[0], 2)
        self.service["/Ac/L2/Power"] = round(voltages[1] * currents[1], 2)
        self.service["/Ac/L3/Power"] = round(voltages[2] * currents[2], 2)

        power = decode_floats(raw_data["power"], 1)[0]
        self.service["/Ac/Power"] = round(power, 2)

        self.service["/Ac/PhaseCount"] = raw_data["phases"][0]

    def apply_controls(self) -> None:
        """
        Apply control actions like setting effective current.
        """
        self.last_sent_current, self.last_current_set_time = set_effective_current(
            self.client,
            self.config,
            self.current_mode,
            self.start_stop,
            self.intended_set_current,
            self.low_soc_enabled,
            self.low_soc_active,
            self.station_max_current,
            self.last_sent_current,
            self.last_current_set_time,
            self.schedule_enabled,
            self.schedule_days_mask,
            self.schedule_start,
            self.schedule_end,
            self.logger,
        )

    def poll(self) -> bool:
        """
        Poll the Modbus device for updates and apply logic.

        Returns:
            Always True (for GLib timeout compatibility).

        Handles reconnection and sets /Connected status.
        """
        try:
            if not self.client.is_socket_open():
                if not reconnect(self.client, self.logger):
                    self.service["/Connected"] = 0
                    return True

            raw_data = self.fetch_raw_data()
            self.process_logic()
            self.update_dbus_paths(raw_data)
            self.apply_controls()

            self.service["/Connected"] = 1
            self.logger.debug("Poll completed successfully")

        except ModbusException as e:
            self.logger.error(f"Poll error: {e}. Attempting reconnect.")
            reconnect(self.client, self.logger)
            self.service["/Connected"] = 0
        except ConnectionError as e:
            self.logger.error(f"Connection error: {e}. Attempting reconnect.")
            reconnect(self.client, self.logger)
            self.service["/Connected"] = 0
        status = self.service["/Status"]
        next_interval = self.ACTIVE_POLL_MS if status == 2 else self.IDLE_POLL_MS
        self._schedule_next_poll(next_interval)
        return False  # One-shot, will be rescheduled

    def run(self) -> None:
        """
        Run the main GLib loop with periodic polling.
        """
        mainloop = GLib.MainLoop()
        mainloop.run()
