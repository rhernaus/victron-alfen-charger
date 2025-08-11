#!/usr/bin/env python3

import json
import logging
import math
import os
import sys
import time

sys.path.insert(
    1, os.path.join(os.path.dirname(__file__), "/opt/victronenergy/dbus-modbus-client")
)

from dataclasses import asdict
from typing import Any, Dict, List

from gi.repository import GLib
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from .config import Config, ScheduleItem, load_config, load_config_from_disk
from .controls import set_current, set_effective_current, update_station_max_current
from .dbus_utils import EVC_CHARGE, EVC_MODE, register_dbus_service
from .logic import compute_effective_current, process_status_and_energy
from .modbus_utils import read_modbus_string, reconnect

try:
    import dbus
except ImportError:  # pragma: no cover
    dbus = None


class MutableValue:
    def __init__(self, value):
        self.value = value


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
        self.current_mode = MutableValue(EVC_MODE.AUTO.value)
        self.start_stop = MutableValue(EVC_CHARGE.DISABLED.value)
        self.auto_start = MutableValue(1)
        self.intended_set_current = MutableValue(
            self.config.defaults.intended_set_current
        )
        self.last_sent_current = -1.0
        self.just_connected = False
        self.schedules = self.config.schedule.items
        self.station_max_current = self.config.defaults.station_max_current
        self.max_current_update_counter = 0
        modbus_config = self.config.modbus
        self.client = ModbusTcpClient(host=modbus_config.ip, port=modbus_config.port)
        device_instance = self.config.device_instance
        self.service_name = f"com.victronenergy.evcharger.alfen_{device_instance}"
        self.config_file_path = f"/data/evcharger_alfen_{device_instance}.json"

        # Moved load initial logic
        existing_service_found = False
        if dbus is not None:
            try:
                bus = dbus.SystemBus()
                dbus_proxy = bus.get_object(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus"
                )
                dbus_iface = dbus.Interface(dbus_proxy, "org.freedesktop.DBus")
                if dbus_iface.NameHasOwner(self.service_name):
                    self.logger.info(
                        f"Existing D-Bus service {self.service_name} found. Loading initial values from it."
                    )
                    existing_service_found = True
                    paths_list = [
                        "/Mode",
                        "/StartStop",
                        "/AutoStart",
                        "/SetCurrent",
                    ]
                    for path in paths_list:
                        try:
                            obj = bus.get_object(self.service_name, path)
                            v = obj.GetValue(dbus_interface="com.victronenergy.BusItem")
                            if v is not None:
                                if path == "/Mode":
                                    self.current_mode.value = int(v)
                                elif path == "/StartStop":
                                    self.start_stop.value = int(v)
                                elif path == "/AutoStart":
                                    self.auto_start.value = int(v)
                                elif path == "/SetCurrent":
                                    self.intended_set_current.value = float(v)
                        except Exception as e:
                            self.logger.warning(f"Failed to load {path}: {e}")
            except dbus.DBusException:
                self.logger.warning(
                    "Failed to inspect existing D-Bus service state; using defaults."
                )
        if not existing_service_found:
            data = load_config_from_disk(self.config_file_path, self.logger)
            if data is not None:
                try:
                    self.current_mode.value = int(
                        data.get("Mode", self.current_mode.value)
                    )
                except ValueError:
                    pass
                try:
                    self.start_stop.value = int(
                        data.get("StartStop", self.start_stop.value)
                    )
                except ValueError:
                    pass
                self.auto_start.value = int(
                    data.get("AutoStart", self.auto_start.value)
                )
                self.intended_set_current.value = float(
                    data.get("SetCurrent", self.intended_set_current.value)
                )

        self.service = register_dbus_service(
            self.service_name,
            self.config,
            self.current_mode.value,
            self.start_stop.value,
            self.auto_start.value,
            self.intended_set_current.value,
            self.schedules,
            self.mode_callback,
            self.startstop_callback,
            self.set_current_callback,
            self.autostart_callback,
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

    def _persist_config(self) -> None:
        try:
            cfg = {
                "Mode": self.current_mode.value,
                "StartStop": self.start_stop.value,
                "AutoStart": self.auto_start.value,
                "SetCurrent": self.intended_set_current.value,
            }
            os.makedirs(os.path.dirname(self.config_file_path), exist_ok=True)
            with open(self.config_file_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            self.logger.warning(f"Failed to persist config: {e}")

    def _ensure_soc_proxy(self) -> bool:
        pass

    def _read_battery_soc(self) -> float | None:
        return None

    def mode_callback(self, path: str, value: Any) -> bool:
        try:
            self.current_mode.value = int(value)
            self._persist_config()
            now = time.time()
            if self.current_mode.value == EVC_MODE.MANUAL.value:
                effective_current, explanation = compute_effective_current(
                    self.current_mode.value,
                    self.start_stop.value,
                    self.intended_set_current.value,
                    self.station_max_current,
                    now,
                    self.schedules,
                    self.config.timezone,
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
                    self.logger.debug(
                        f"Immediate Mode change applied current: {effective_current:.2f} A (mode={EVC_MODE(self.current_mode.value).name}). Calculation: {explanation}"
                    )
            self.logger.info(
                f"Mode changed to {EVC_MODE(self.current_mode.value).name}"
            )
            return True
        except (ValueError, TypeError):
            return False

    def startstop_callback(self, path: str, value: Any) -> bool:
        try:
            self.start_stop.value = int(value)
            self._persist_config()
            if self.current_mode.value == EVC_MODE.MANUAL.value:
                target = (
                    self.intended_set_current.value
                    if self.start_stop.value == EVC_CHARGE.ENABLED.value
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
                        f"Immediate StartStop change applied: {target:.2f} A (StartStop={EVC_CHARGE(self.start_stop.value).name})"
                    )
            self.logger.info(
                f"StartStop changed to {EVC_CHARGE(self.start_stop.value).name}"
            )
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
            self.intended_set_current.value = requested
            self.service["/SetCurrent"] = round(self.intended_set_current.value, 1)
            self.logger.info(
                f"GUI request to set intended current to {requested:.2f} A"
            )
            self._persist_config()

            if self.current_mode.value == EVC_MODE.MANUAL.value:
                effective_current, explanation = compute_effective_current(
                    self.current_mode.value,
                    self.start_stop.value,
                    self.intended_set_current.value,
                    self.station_max_current,
                    time.time(),
                    self.schedules,
                    self.config.timezone,
                )
                if set_current(
                    self.client,
                    self.config,
                    effective_current,
                    self.station_max_current,
                    force_verify=True,
                ):
                    self.last_current_set_time = time.time()
                    self.last_sent_current = effective_current
                    log_msg = f"Immediate SetCurrent applied: {effective_current:.2f} A (MANUAL)"
                    if not math.isclose(effective_current, requested, abs_tol=0.01):
                        log_msg += f" (clamped from requested {requested:.2f} A)"
                    log_msg += f". Calculation: {explanation}"
                    self.logger.info(log_msg)
            self.logger.info(
                f"SetCurrent changed to {self.intended_set_current.value:.2f} A"
            )
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
        self.auto_start.value = int(value)
        self._persist_config()
        self.logger.info(f"AutoStart changed to {self.auto_start.value}")
        return True

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
        self.charging_start_time, self.session_start_energy_kwh, self.just_connected = (
            process_status_and_energy(
                self.client,
                self.config,
                self.service,
                self.current_mode.value,
                self.start_stop.value,
                self.auto_start.value,
                self.intended_set_current.value,
                self.schedules,
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
                self.logger,
                self.config.timezone,
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
        # Calculate current ev_power from service
        ev_power = (
            self.service["/Ac/L1/Power"]
            + self.service["/Ac/L2/Power"]
            + self.service["/Ac/L3/Power"]
        )
        self.last_sent_current, self.last_current_set_time = set_effective_current(
            self.client,
            self.config,
            self.current_mode.value,
            self.start_stop.value,
            self.intended_set_current.value,
            self.station_max_current,
            self.last_sent_current,
            self.last_current_set_time,
            self.schedules,
            self.logger,
            ev_power,  # Pass local ev_power
            force=self.just_connected,
            timezone=self.config.timezone,
        )

    def poll(self) -> bool:
        """
        Main polling loop.
        """
        if not self.client.is_socket_open():
            reconnect(self.client, self.logger)
        try:
            raw_data = self.fetch_raw_data()
            self.process_logic()
            self.update_dbus_paths(raw_data)
            self.apply_controls()
            self.service["/Connected"] = 1
            return True
        except ModbusException as e:
            self.logger.error(f"Modbus error during poll: {e}")
            self.service["/Connected"] = 0
            reconnect(self.client, self.logger)
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during poll: {e}")
            return False

    def run(self) -> None:
        """
        Run the main GLib loop with periodic polling.
        """
        mainloop = GLib.MainLoop()
        mainloop.run()
