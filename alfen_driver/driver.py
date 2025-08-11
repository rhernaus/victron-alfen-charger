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

from typing import Any, Dict, List

from gi.repository import GLib
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from .config import Config, ScheduleItem, load_config, load_config_from_disk
from .controls import set_current, set_effective_current, update_station_max_current
from .dbus_utils import EVC_CHARGE, EVC_MODE, register_dbus_service
from .logic import (
    apply_mode_specific_status,
    compute_effective_current,
    map_alfen_status,
    process_status_and_energy,
)
from .modbus_utils import (
    decode_64bit_float,
    read_holding_registers,
    read_modbus_string,
    reconnect,
)

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
        self.intended_set_current = MutableValue(
            self.config.defaults.intended_set_current
        )
        self.last_sent_current = -1.0
        self.last_sent_phases = 3
        self.just_connected = False
        self.schedules = self.config.schedule.items
        self.station_max_current = self.config.defaults.station_max_current
        self.max_current_update_counter = 0
        self.last_charging_time: float = 0.0
        self.last_session_energy: float = 0.0
        modbus_config = self.config.modbus
        self.client = ModbusTcpClient(host=modbus_config.ip, port=modbus_config.port)
        try:
            reg = self.client.read_holding_registers(
                self.config.registers.phases,
                1,
                slave=self.config.modbus.socket_slave_id,
            )
            if not reg.isError():
                self.last_sent_phases = reg.registers[0]
        except ModbusException as e:
            self.logger.warning(f"Failed to read initial phases: {e}")
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
                self.intended_set_current.value = float(
                    data.get("SetCurrent", self.intended_set_current.value)
                )
                if "Schedules" in data:
                    schedules_data = data["Schedules"]
                    self.schedules = [ScheduleItem(**d) for d in schedules_data]
                    while len(self.schedules) < 3:
                        self.schedules.append(ScheduleItem())
                self.last_charging_time = data.get("LastChargingTime", 0.0)
                self.last_session_energy = data.get("LastSessionEnergy", 0.0)
                self.charging_start_time = data.get("ChargingStartTime", 0.0)
                self.session_start_energy_kwh = data.get("SessionStartEnergyKWh", 0.0)

        self.service = register_dbus_service(
            self.service_name,
            self.config,
            self.current_mode.value,
            self.start_stop.value,
            self.intended_set_current.value,
            self.schedules,
            self.mode_callback,
            self.startstop_callback,
            self.set_current_callback,
        )

        self._load_static_info()
        self._restore_session_state()
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
                "SetCurrent": self.intended_set_current.value,
                "ChargingStartTime": self.charging_start_time,
                "SessionStartEnergyKWh": self.session_start_energy_kwh,
                "LastChargingTime": self.last_charging_time,
                "LastSessionEnergy": self.last_session_energy,
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
                effective_current, effective_phases, explanation = (
                    compute_effective_current(
                        self.current_mode.value,
                        self.start_stop.value,
                        self.intended_set_current.value,
                        self.station_max_current,
                        now,
                        self.schedules,
                        self.config.timezone,
                        current_phases=self.last_sent_phases,
                    )
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
                    self.last_sent_phases = effective_phases
                    self.logger.info(
                        f"Immediate Mode change applied current: {effective_current:.2f} A (mode={EVC_MODE(self.current_mode.value).name}). Calculation: {explanation}"
                    )
                else:
                    self.logger.warning(
                        "Failed to apply immediate current on mode change"
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
                effective_current, effective_phases, explanation = (
                    compute_effective_current(
                        self.current_mode.value,
                        self.start_stop.value,
                        self.intended_set_current.value,
                        self.station_max_current,
                        time.time(),
                        self.schedules,
                        self.config.timezone,
                        current_phases=self.last_sent_phases,
                    )
                )
                ok_phases = True
                if effective_phases != self.last_sent_phases:
                    ok_phases = set_current(
                        self.client, self.config, effective_phases, force_verify=True
                    )
                if ok_phases and set_current(
                    self.client,
                    self.config,
                    effective_current,
                    self.station_max_current,
                    force_verify=True,
                ):
                    self.last_current_set_time = time.time()
                    self.last_sent_current = effective_current
                    self.last_sent_phases = effective_phases
                    log_msg = f"Immediate StartStop change applied: {effective_current:.2f} A (MANUAL)"
                    if not math.isclose(effective_current, target, abs_tol=0.01):
                        log_msg += f" (clamped from requested {target:.2f} A)"
                    log_msg += f". Calculation: {explanation}"
                    self.logger.info(log_msg)
            else:
                effective_current, effective_phases, explanation = (
                    compute_effective_current(
                        self.current_mode.value,
                        self.start_stop.value,
                        self.intended_set_current.value,
                        self.station_max_current,
                        time.time(),
                        self.schedules,
                        0.0,
                        self.config.timezone,
                        current_phases=self.last_sent_phases,
                    )
                )
                ok_phases = True
                if effective_phases != self.last_sent_phases:
                    ok_phases = set_current(
                        self.client, self.config, effective_phases, force_verify=True
                    )
                if ok_phases and set_current(
                    self.client,
                    self.config,
                    effective_current,
                    self.station_max_current,
                    force_verify=True,
                ):
                    self.last_current_set_time = time.time()
                    self.last_sent_current = effective_current
                    self.last_sent_phases = effective_phases
                    log_msg = f"Immediate StartStop change applied: {effective_current:.2f} A (mode={EVC_MODE(self.current_mode.value).name}, StartStop={EVC_CHARGE(self.start_stop.value).name})"
                    log_msg += f". Calculation: {explanation}"
                    self.logger.info(log_msg)
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
                effective_current, effective_phases, explanation = (
                    compute_effective_current(
                        self.current_mode.value,
                        self.start_stop.value,
                        self.intended_set_current.value,
                        self.station_max_current,
                        time.time(),
                        self.schedules,
                        self.config.timezone,
                        current_phases=self.last_sent_phases,
                    )
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
                    self.last_sent_phases = effective_phases
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
        (
            self.charging_start_time,
            self.session_start_energy_kwh,
            self.last_charging_time,
            self.last_session_energy,
            self.just_connected,
        ) = process_status_and_energy(
            self.client,
            self.config,
            self.service,
            self.current_mode.value,
            self.start_stop.value,
            self.intended_set_current.value,
            self.schedules,
            self.station_max_current,
            self.charging_start_time,
            self.session_start_energy_kwh,
            self.last_charging_time,
            self.last_session_energy,
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
        self.last_sent_current, self.last_current_set_time, self.last_sent_phases = (
            set_effective_current(
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
                last_sent_phases=self.last_sent_phases,
            )
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

    def _restore_session_state(self) -> None:
        if not self.client.connect():
            self.logger.warning("Failed to connect to Modbus for session restore")
            return
        total_energy_regs = read_holding_registers(
            self.client,
            self.config.registers.energy,
            4,
            self.config.modbus.socket_slave_id,
        )
        total_energy_kwh = decode_64bit_float(total_energy_regs) / 1000.0
        raw_status = map_alfen_status(self.client, self.config)
        connected = raw_status >= 1
        new_victron_status = raw_status
        new_victron_status = apply_mode_specific_status(
            self.current_mode.value,
            connected,
            self.start_stop.value,
            self.intended_set_current.value,
            self.schedules,
            new_victron_status,
            self.config.timezone,
        )
        now = time.time()
        if self.charging_start_time > 0:
            if new_victron_status == 2:
                # Continue session, add approximate downtime
                downtime = now - (
                    self.charging_start_time + self.service["/ChargingTime"]
                )
                self.service["/ChargingTime"] = (
                    now - self.charging_start_time
                )  # Includes downtime
                energy_delta = max(
                    0.0, total_energy_kwh - self.session_start_energy_kwh
                )
                self.service["/Ac/Energy/Forward"] = round(energy_delta, 3)
                self.logger.info(
                    f"Restored ongoing session, added {downtime:.0f}s downtime"
                )
            else:
                # Session ended during downtime
                downtime = now - (
                    self.charging_start_time + self.service["/ChargingTime"]
                )
                self.last_charging_time = self.service["/ChargingTime"] + downtime
                energy_delta = max(
                    0.0, total_energy_kwh - self.session_start_energy_kwh
                )
                self.last_session_energy = round(energy_delta, 3)
                self.service["/ChargingTime"] = self.last_charging_time
                self.service["/Ac/Energy/Forward"] = self.last_session_energy
                self.charging_start_time = 0
                self.session_start_energy_kwh = 0
                self._persist_config()
                self.logger.info("Restored ended session with approximate downtime")
        else:
            self.service["/ChargingTime"] = self.last_charging_time
            self.service["/Ac/Energy/Forward"] = self.last_session_energy
            self.logger.info("Restored last session values")
