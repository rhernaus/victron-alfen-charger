#!/usr/bin/env python3

import enum
import json
import logging
import math
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

sys.path.insert(
    1, os.path.join(os.path.dirname(__file__), "/opt/victronenergy/dbus-modbus-client")
)

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from pymodbus.client import ModbusTcpClient
from pymodbus.constants import Endian
from pymodbus.exceptions import ModbusException
from pymodbus.payload import BinaryPayloadBuilder, BinaryPayloadDecoder
from vedbus import VeDbusService

try:
    import dbus
except ImportError:  # pragma: no cover
    dbus = None


class EVC_MODE(enum.IntEnum):
    MANUAL = 0
    AUTO = 1
    SCHEDULED = 2


class EVC_CHARGE(enum.IntEnum):
    DISABLED = 0
    ENABLED = 1


# --- Configuration File Path ---
CONFIG_PATH: str = os.path.join(os.path.dirname(__file__), "alfen_driver_config.json")

# --- Default Configuration (fallback if file is missing or invalid) ---
DEFAULT_CONFIG: Dict[str, Any] = {
    "modbus": {
        "ip": "10.128.0.64",
        "port": 502,
        "socket_slave_id": 1,
        "station_slave_id": 200,
    },
    "device_instance": 0,
    "registers": {
        "voltages": 306,
        "currents": 320,
        "power": 344,
        "energy": 374,
        "status": 1201,
        "amps_config": 1210,
        "phases": 1215,
        "firmware_version": 123,
        "firmware_version_count": 17,
        "station_serial": 157,
        "station_serial_count": 11,
        "manufacturer": 117,
        "manufacturer_count": 5,
        "platform_type": 140,
        "platform_type_count": 17,
        "station_max_current": 1100,
    },
    "defaults": {"intended_set_current": 6.0, "station_max_current": 32.0},
    "logging": {"level": "INFO", "file": "/var/log/alfen_driver.log"},
    "schedule": {"enabled": 0, "days_mask": 0, "start": "00:00", "end": "00:00"},
    "low_soc": {
        "enabled": 0,
        "threshold": 20.0,
        "hysteresis": 2.0,
        "battery_soc_dbus_path": "/Dc/Battery/Soc",
    },
    "poll_interval_ms": 1000,
}


class AlfenDriver:
    WATCHDOG_INTERVAL_SECONDS: int = 30
    MAX_SET_CURRENT: float = 64.0
    CURRENT_TOLERANCE: float = 0.25
    CLAMP_EPSILON: float = 1e-6
    MIN_CHARGING_CURRENT: float = 0.1
    UPDATE_DIFFERENCE_THRESHOLD: float = 0.1
    VERIFICATION_DELAY: float = 0.1
    RETRY_DELAY: float = 0.5
    MAX_RETRIES: int = 3
    POLL_INTERVAL_MS: int = 1000  # Default

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

        self.config: Dict[str, Any] = self._load_config()

        # Reconfigure logging with loaded config values
        logging.basicConfig(
            level=self.config["logging"]["level"],
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(self.config["logging"]["file"]),
                logging.StreamHandler(sys.stdout),
            ],
            force=True,  # Force reconfiguration
        )

        self.charging_start_time: float = 0
        self.last_current_set_time: float = 0
        self.session_start_energy_kwh: float = 0
        self.intended_set_current: float = self.config["defaults"][
            "intended_set_current"
        ]
        self.current_mode: EVC_MODE = EVC_MODE.AUTO
        self.start_stop: EVC_CHARGE = EVC_CHARGE.DISABLED
        self.auto_start: int = 1
        self.last_sent_current: float = -1.0
        self.schedule_enabled: int = self.config["schedule"]["enabled"]
        self.schedule_days_mask: int = self.config["schedule"]["days_mask"]
        self.schedule_start: str = self.config["schedule"]["start"]
        self.schedule_end: str = self.config["schedule"]["end"]
        self.low_soc_enabled: int = self.config["low_soc"]["enabled"]
        self.low_soc_threshold: float = self.config["low_soc"]["threshold"]
        self.low_soc_hysteresis: float = self.config["low_soc"]["hysteresis"]
        self.low_soc_active: bool = False
        self.battery_soc: Optional[float] = None
        self.dbus_bus: Optional[Any] = None
        self.dbus_soc_obj: Optional[Any] = None
        self.station_max_current: float = self.config["defaults"]["station_max_current"]
        modbus_config = self.config["modbus"]
        self.client: ModbusTcpClient = ModbusTcpClient(
            host=modbus_config["ip"], port=modbus_config["port"]
        )
        device_instance = self.config["device_instance"]
        self.service_name: str = f"com.victronenergy.evcharger.alfen_{device_instance}"
        self.service: VeDbusService = VeDbusService(self.service_name, register=False)
        self.config_file_path: str = f"/data/evcharger_alfen_{device_instance}.json"

        self._load_initial_config()

        # Data-driven D-Bus paths
        dbus_paths = [
            {"path": "/Mgmt/ProcessName", "value": __file__},
            {"path": "/Mgmt/ProcessVersion", "value": "1.4"},
            {
                "path": "/Mgmt/Connection",
                "value": f"Modbus TCP at {modbus_config['ip']}",
            },
            {"path": "/DeviceInstance", "value": device_instance},
            {"path": "/Connected", "value": 0},
            {"path": "/ProductName", "value": "Alfen EV Charger"},
            {"path": "/ProductId", "value": 0xA142},
            {"path": "/FirmwareVersion", "value": "N/A"},
            {"path": "/Serial", "value": "ALFEN-001"},
            {"path": "/Status", "value": 0},
            {
                "path": "/Mode",
                "value": self.current_mode.value,
                "writeable": True,
                "callback": self.mode_callback,
            },
            {
                "path": "/StartStop",
                "value": self.start_stop.value,
                "writeable": True,
                "callback": self.startstop_callback,
            },
            {
                "path": "/SetCurrent",
                "value": self.intended_set_current,
                "writeable": True,
                "callback": self.set_current_callback,
            },
            {"path": "/MaxCurrent", "value": 32.0},
            {
                "path": "/AutoStart",
                "value": self.auto_start,
                "writeable": True,
                "callback": self.autostart_callback,
            },
            {"path": "/ChargingTime", "value": 0},
            {"path": "/Current", "value": 0.0},
            {"path": "/Ac/Current", "value": 0.0},
            {"path": "/Ac/Power", "value": 0.0},
            {"path": "/Ac/Energy/Forward", "value": 0.0},
            {"path": "/Ac/PhaseCount", "value": 0},
            {"path": "/Position", "value": 0, "writeable": True},
            {"path": "/Ac/L1/Voltage", "value": 0.0},
            {"path": "/Ac/L1/Current", "value": 0.0},
            {"path": "/Ac/L1/Power", "value": 0.0},
            {"path": "/Ac/L2/Voltage", "value": 0.0},
            {"path": "/Ac/L2/Current", "value": 0.0},
            {"path": "/Ac/L2/Power", "value": 0.0},
            {"path": "/Ac/L3/Voltage", "value": 0.0},
            {"path": "/Ac/L3/Current", "value": 0.0},
            {"path": "/Ac/L3/Power", "value": 0.0},
            {
                "path": "/Schedule/Enabled",
                "value": self.schedule_enabled,
                "writeable": True,
                "callback": self.schedule_enabled_callback,
            },
            {
                "path": "/Schedule/Days",
                "value": self.schedule_days_mask,
                "writeable": True,
                "callback": self.schedule_days_callback,
            },
            {
                "path": "/Schedule/Start",
                "value": self.schedule_start,
                "writeable": True,
                "callback": self.schedule_start_callback,
            },
            {
                "path": "/Schedule/End",
                "value": self.schedule_end,
                "writeable": True,
                "callback": self.schedule_end_callback,
            },
            {
                "path": "/LowSoc/Enabled",
                "value": self.low_soc_enabled,
                "writeable": True,
                "callback": self.low_soc_enabled_callback,
            },
            {
                "path": "/LowSoc/Threshold",
                "value": self.low_soc_threshold,
                "writeable": True,
                "callback": self.low_soc_threshold_callback,
            },
            {
                "path": "/LowSoc/Hysteresis",
                "value": self.low_soc_hysteresis,
                "writeable": True,
                "callback": self.low_soc_hysteresis_callback,
            },
            {"path": "/LowSoc/Value", "value": 0.0},
            {"path": "/LowSoc/ActiveReason", "value": ""},
        ]

        for p in dbus_paths:
            self.service.add_path(
                p["path"],
                p["value"],
                writeable=p.get("writeable", False),
                onchangecallback=p.get("callback", None),
            )

        self.service.register()

    def _load_initial_config(self) -> None:
        """
        Load initial configuration from D-Bus or disk.

        Checks for existing D-Bus service and loads values if found,
        otherwise loads from persisted JSON file.
        """
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
                    self._load_from_dbus(bus)
            except dbus.DBusException:
                self.logger.warning(
                    "Failed to inspect existing D-Bus service state; using defaults."
                )

        if not existing_service_found:
            data = self._load_config_from_disk()
            if data is not None:
                self._apply_config(data)

    def _load_from_dbus(self, bus: Any) -> None:
        """
        Load configuration values from an existing D-Bus service.

        Parameters:
            bus: The D-Bus system bus object.

        Loads values like mode, start/stop, auto-start, and set current.
        """
        paths = {
            "/Mode": lambda v: setattr(self, "current_mode", EVC_MODE(int(v))),
            "/StartStop": lambda v: setattr(self, "start_stop", EVC_CHARGE(int(v))),
            "/AutoStart": lambda v: setattr(self, "auto_start", int(v)),
            "/SetCurrent": lambda v: setattr(self, "intended_set_current", float(v)),
        }
        for path, setter in paths.items():
            v = self._get_busitem_value(bus, path)
            if v is not None:
                try:
                    setter(v)
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"Failed to parse existing {path}: {v!r} ({e})")

    def _get_busitem_value(self, bus: Any, path: str) -> Optional[Any]:
        """
        Retrieve a value from a D-Bus path.

        Parameters:
            bus: The D-Bus system bus object.
            path: The D-Bus path to query.

        Returns:
            The value from the D-Bus item, or None if not found or error.

        Raises:
            dbus.DBusException: If D-Bus query fails (caught externally).
        """
        try:
            obj = bus.get_object(self.service_name, path)
            return obj.GetValue(dbus_interface="com.victronenergy.BusItem")
        except Exception:
            return None

    def _persist_config_to_disk(self) -> None:
        """
        Persist current configuration to disk as JSON.

        Saves key states like mode, start/stop, auto-start, and set current.
        """
        try:
            cfg: Dict[str, Any] = {
                "Mode": int(self.current_mode),
                "StartStop": int(self.start_stop),
                "AutoStart": int(self.auto_start),
                "SetCurrent": float(self.intended_set_current),
            }
            os.makedirs(os.path.dirname(self.config_file_path), exist_ok=True)
            with open(self.config_file_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            self.logger.warning(f"Failed to persist config: {e}")

    def _load_config_from_disk(self) -> Optional[Dict[str, Any]]:
        """
        Load persisted configuration from disk.

        Returns:
            The loaded config dictionary, or None if file not found or invalid.

        Raises:
            OSError, json.JSONDecodeError: Handled and logged.
        """
        try:
            if not os.path.exists(self.config_file_path):
                return None
            with open(self.config_file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.logger.warning(f"Failed to load persisted config: {e}")
            return None

    def _apply_config(self, data: Dict[str, Any]) -> None:
        """
        Apply loaded configuration data to instance variables.

        Parameters:
            data: The configuration dictionary to apply.

        Handles type conversions and defaults on errors.
        """
        try:
            self.current_mode = EVC_MODE(int(data.get("Mode", int(self.current_mode))))
        except ValueError:
            pass
        try:
            self.start_stop = EVC_CHARGE(
                int(data.get("StartStop", int(self.start_stop)))
            )
        except ValueError:
            pass
        self.auto_start = int(data.get("AutoStart", self.auto_start))
        self.intended_set_current = float(
            data.get("SetCurrent", self.intended_set_current)
        )

    def _parse_hhmm_to_minutes(self, timestr: str) -> int:
        """
        Parse HH:MM string to minutes since midnight.

        Parameters:
            timestr: The time string in HH:MM format.

        Returns:
            Minutes since midnight, or 0 if invalid.

        Raises:
            ValueError: If parsing fails (caught and returns 0).
        """
        try:
            parts = timestr.strip().split(":")
            if len(parts) != 2:
                return 0
            hours = int(parts[0]) % 24
            minutes = int(parts[1]) % 60
            return hours * 60 + minutes
        except ValueError:
            return 0

    def _is_within_schedule(self, now: float) -> bool:
        """
        Check if current time is within the scheduled window.

        Parameters:
            now: Current time in seconds since epoch.

        Returns:
            True if within schedule, False otherwise.

        Uses local time, day mask, and start/end times.
        """
        if self.schedule_enabled == 0:
            return False
        tm = time.localtime(now)
        weekday = tm.tm_wday  # Mon=0..Sun=6
        sun_based_index = (weekday + 1) % 7
        if (self.schedule_days_mask & (1 << sun_based_index)) == 0:
            return False
        minutes_now = tm.tm_hour * 60 + tm.tm_min
        start_min = self._parse_hhmm_to_minutes(self.schedule_start)
        end_min = self._parse_hhmm_to_minutes(self.schedule_end)
        if start_min == end_min:
            return False
        if start_min < end_min:
            return start_min <= minutes_now < end_min
        return minutes_now >= start_min or minutes_now < end_min

    def _load_config(self) -> Dict[str, Any]:
        """
        Load configuration from JSON file, falling back to defaults.

        Returns:
            The loaded or default configuration dictionary.

        Validates key fields and merges with defaults.
        """
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded_config = json.load(f)
                # Basic validation
                if not isinstance(loaded_config, dict):
                    raise ValueError("Config must be a dictionary")
                # Validate specific fields (example)
                if "modbus" in loaded_config:
                    modbus = loaded_config["modbus"]
                    if "ip" in modbus and not isinstance(modbus["ip"], str):
                        raise ValueError("modbus.ip must be a string")
                    if "port" in modbus and not isinstance(modbus["port"], int):
                        raise ValueError("modbus.port must be an integer")
                if "schedule" in loaded_config:
                    sched = loaded_config["schedule"]
                    if "start" in sched:
                        self._parse_hhmm_to_minutes(
                            sched["start"]
                        )  # Will raise if invalid
                    if "end" in sched:
                        self._parse_hhmm_to_minutes(sched["end"])
                # Merge with defaults
                config = DEFAULT_CONFIG.copy()
                for key in config:
                    if key in loaded_config:
                        if isinstance(config[key], dict) and isinstance(
                            loaded_config[key], dict
                        ):
                            config[key].update(loaded_config[key])
                        else:
                            config[key] = loaded_config[key]
                self.logger.info(f"Loaded and validated config from {CONFIG_PATH}")
                return config
            except (ValueError, KeyError) as e:
                self.logger.warning(
                    f"Invalid config in {CONFIG_PATH}: {e}. Using defaults."
                )
            except Exception as e:
                self.logger.warning(
                    f"Failed to load config from {CONFIG_PATH}: {e}. Using defaults."
                )
        else:
            self.logger.info(f"Config file {CONFIG_PATH} not found. Using defaults.")
        return DEFAULT_CONFIG

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
                soc_path = self.config["low_soc"]["battery_soc_dbus_path"]
                self.dbus_soc_obj = self.dbus_bus.get_object(
                    "com.victronenergy.system", soc_path
                )
            return True
        except dbus.DBusException:
            self.dbus_soc_obj = None
            return False

    def _read_battery_soc(self) -> Optional[float]:
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

    def _clamp_intended_current_to_max(self) -> None:
        """Clamp the intended set current to the station max in MANUAL mode."""
        max_allowed = max(0.0, float(self.station_max_current))
        if self.intended_set_current > max_allowed + self.CLAMP_EPSILON:
            self.intended_set_current = max_allowed
            self.service["/SetCurrent"] = round(self.intended_set_current, 1)
            self._persist_config_to_disk()
            self.logger.info(
                f"Clamped DBus /SetCurrent to station max: {self.intended_set_current:.1f} A (MANUAL mode)"
            )

    def _set_current(self, target_amps: float, force_verify: bool = False) -> bool:
        """
        Set the current via Modbus with verification and retries.

        Parameters:
            target_amps: The target current in amps.
            force_verify: If True, verify the write by reading back.

        Returns:
            True if set successfully, False otherwise.

        Raises:
            ModbusException, ValueError: Handled with logging and retries.
        """
        target_amps = max(0.0, min(target_amps, self.station_max_current))
        retries = self.MAX_RETRIES
        for attempt in range(retries):
            try:
                builder = BinaryPayloadBuilder(
                    byteorder=Endian.BIG, wordorder=Endian.BIG
                )
                builder.add_32bit_float(float(target_amps))
                payload = builder.to_registers()
                self.client.write_registers(
                    self.config["registers"]["amps_config"],
                    payload,
                    slave=self.config["modbus"]["socket_slave_id"],
                )
                if force_verify:
                    time.sleep(self.VERIFICATION_DELAY)
                    regs = self.read_holding_registers(
                        self.config["registers"]["amps_config"],
                        2,
                        self.config["modbus"]["socket_slave_id"],
                    )
                    if len(regs) == 2:
                        dec = BinaryPayloadDecoder.fromRegisters(
                            regs, byteorder=Endian.BIG, wordorder=Endian.BIG
                        ).decode_32bit_float()
                        self.logger.info(
                            f"SetCurrent write (attempt {attempt+1}): raw={regs}, dec={dec:.3f}"
                        )
                        if math.isclose(
                            dec, float(target_amps), abs_tol=self.CURRENT_TOLERANCE
                        ):
                            return True
                    else:
                        self.logger.warning(
                            f"Verification failed on attempt {attempt+1}"
                        )
                else:
                    return True
            except ModbusException as e:
                self.logger.error(
                    f"Modbus error on SetCurrent attempt {attempt+1}: {e}"
                )
                if attempt < retries - 1:
                    time.sleep(self.RETRY_DELAY)
            except ValueError as e:
                self.logger.error(f"Value error on SetCurrent attempt {attempt+1}: {e}")
                return False
        return False

    def _set_effective_current(self, force: bool = False) -> None:
        """
        Set the effective current based on mode and watchdog.

        Parameters:
            force: If True, force update regardless of thresholds.
        """
        effective_current = self._compute_effective_current(time.time())
        if effective_current < 0:
            effective_current = 0.0
        if effective_current > self.station_max_current:
            effective_current = self.station_max_current

        current_time = time.time()
        needs_update = (
            force
            or abs(effective_current - self.last_sent_current)
            > self.UPDATE_DIFFERENCE_THRESHOLD
            or (
                current_time - self.last_current_set_time
                > self.WATCHDOG_INTERVAL_SECONDS
            )
        )
        if needs_update:
            ok = self._set_current(effective_current, force_verify=True)
            if ok:
                self.last_current_set_time = current_time
                self.last_sent_current = effective_current
                self.logger.info(
                    f"Set effective current to {effective_current:.2f} A (mode: {self.current_mode.name}, intended: {self.intended_set_current:.2f})"
                )

    def set_current_callback(self, path: str, value: Any) -> bool:
        """
        Handle changes to the set current value from D-Bus.

        Parameters:
            path: The D-Bus path (unused, for callback compatibility).
            value: The new current value.

        Returns:
            True if update successful, False otherwise.

        Applies clamping, persists config, and sets current in manual mode.
        """
        try:
            requested = max(0.0, min(self.MAX_SET_CURRENT, float(value)))
            self._update_station_max_current()
            max_allowed = max(0.0, float(self.station_max_current))
            self.intended_set_current = min(requested, max_allowed)
            self.service["/SetCurrent"] = round(self.intended_set_current, 1)
            self.logger.info(
                f"GUI request to set intended current to {self.intended_set_current:.2f} A"
            )
            self._persist_config_to_disk()

            if self.current_mode == EVC_MODE.MANUAL:
                target = (
                    self.intended_set_current
                    if self.start_stop == EVC_CHARGE.ENABLED
                    else 0.0
                )
                if target > self.station_max_current:
                    target = self.station_max_current
                if self._set_current(target):
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
            self.reconnect()
            return False
        except Exception as e:  # Fallback for unexpected errors
            self.logger.error(
                f"Unexpected error in set_current_callback: {e}\n{traceback.format_exc()}"
            )
            return False

    def mode_callback(self, path: str, value: Any) -> bool:
        """
        Handle changes to the mode from D-Bus.

        Parameters:
            path: The D-Bus path (unused).
            value: The new mode value (int).

        Returns:
            True if update successful, False otherwise.

        Updates mode, persists config, and applies effective current.
        """
        try:
            self.current_mode = EVC_MODE(int(value))
            self._persist_config_to_disk()
            now = time.time()
            effective_current = 0.0
            if self.current_mode == EVC_MODE.MANUAL:
                if self.intended_set_current > self.station_max_current:
                    self.intended_set_current = self.station_max_current
                    self.service["/SetCurrent"] = round(self.intended_set_current, 1)
                    self._persist_config_to_disk()
                    self.logger.info(
                        f"Clamped /SetCurrent to station max: {self.intended_set_current:.1f} A "
                        f"(on MANUAL mode)"
                    )
                effective_current = (
                    self.intended_set_current
                    if self.start_stop == EVC_CHARGE.ENABLED
                    else 0.0
                )
            elif self.current_mode == EVC_MODE.AUTO:
                effective_current = (
                    self.intended_set_current
                    if self.start_stop == EVC_CHARGE.ENABLED
                    else 0.0
                )
            elif self.current_mode == EVC_MODE.SCHEDULED:
                effective_current = (
                    self.intended_set_current if self._is_within_schedule(now) else 0.0
                )
            if (
                self.low_soc_enabled
                and self.low_soc_active
                and self.current_mode in (EVC_MODE.AUTO, EVC_MODE.SCHEDULED)
            ):
                effective_current = 0.0

            if self._set_current(effective_current, force_verify=True):
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
        """
        Handle changes to start/stop from D-Bus.

        Parameters:
            path: The D-Bus path (unused).
            value: The new start/stop value (int).

        Returns:
            True if update successful, False otherwise.

        Updates start/stop, persists config, and applies in manual mode.
        """
        try:
            self.start_stop = EVC_CHARGE(int(value))
            self._persist_config_to_disk()
            if self.current_mode == EVC_MODE.MANUAL:
                target = (
                    self.intended_set_current
                    if self.start_stop == EVC_CHARGE.ENABLED
                    else 0.0
                )
                if self._set_current(target, force_verify=True):
                    self.last_current_set_time = time.time()
                    self.last_sent_current = target
                    self.logger.info(
                        f"Immediate StartStop change applied: {target:.2f} A (StartStop={self.start_stop.name})"
                    )
            self.logger.info(f"StartStop changed to {self.start_stop.name}")
            return True
        except (ValueError, TypeError):
            return False

    def autostart_callback(self, path: str, value: Any) -> bool:
        """
        Handle changes to auto-start from D-Bus.

        Parameters:
            path: The D-Bus path (unused).
            value: The new auto-start value (int).

        Returns:
            True if update successful.
        """
        self.auto_start = int(value)
        self._persist_config_to_disk()
        self.logger.info(f"AutoStart changed to {self.auto_start}")
        return True

    def schedule_enabled_callback(self, path: str, value: Any) -> bool:
        """
        Handle changes to schedule enabled from D-Bus.

        Parameters:
            path: The D-Bus path (unused).
            value: The new enabled value (int).

        Returns:
            True if update successful, False otherwise.
        """
        try:
            self.schedule_enabled = int(value)
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def schedule_days_callback(self, path: str, value: Any) -> bool:
        """
        Handle changes to schedule days mask from D-Bus.

        Parameters:
            path: The D-Bus path (unused).
            value: The new days mask value (int).

        Returns:
            True if update successful, False otherwise.
        """
        try:
            self.schedule_days_mask = int(value) & 0x7F
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def schedule_start_callback(self, path: str, value: Any) -> bool:
        """
        Handle changes to schedule start time from D-Bus.

        Parameters:
            path: The D-Bus path (unused).
            value: The new start time string (HH:MM).

        Returns:
            True if valid and updated, False otherwise.
        """
        try:
            _ = self._parse_hhmm_to_minutes(str(value))
            self.schedule_start = str(value)
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def schedule_end_callback(self, path: str, value: Any) -> bool:
        """
        Handle changes to schedule end time from D-Bus.

        Parameters:
            path: The D-Bus path (unused).
            value: The new end time string (HH:MM).

        Returns:
            True if valid and updated, False otherwise.
        """
        try:
            _ = self._parse_hhmm_to_minutes(str(value))
            self.schedule_end = str(value)
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def low_soc_enabled_callback(self, path: str, value: Any) -> bool:
        """
        Handle changes to low SOC enabled from D-Bus.

        Parameters:
            path: The D-Bus path (unused).
            value: The new enabled value (int).

        Returns:
            True if update successful, False otherwise.
        """
        try:
            self.low_soc_enabled = int(value)
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def low_soc_threshold_callback(self, path: str, value: Any) -> bool:
        """
        Handle changes to low SOC threshold from D-Bus.

        Parameters:
            path: The D-Bus path (unused).
            value: The new threshold value (float).

        Returns:
            True if update successful, False otherwise.
        """
        try:
            self.low_soc_threshold = float(value)
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def low_soc_hysteresis_callback(self, path: str, value: Any) -> bool:
        """
        Handle changes to low SOC hysteresis from D-Bus.

        Parameters:
            path: The D-Bus path (unused).
            value: The new hysteresis value (float).

        Returns:
            True if update successful, False otherwise.
        """
        try:
            self.low_soc_hysteresis = max(0.0, float(value))
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def _compute_effective_current(self, now: float) -> float:
        """
        Calculate the effective charging current based on mode, schedule, and low SoC conditions.

        Parameters:
            now: Current time in seconds since epoch.

        Returns:
            The computed effective current (clamped to 0 - station_max_current).
        """
        effective = 0.0
        if self.current_mode == EVC_MODE.MANUAL:
            effective = (
                self.intended_set_current
                if self.start_stop == EVC_CHARGE.ENABLED
                else 0.0
            )
        elif self.current_mode == EVC_MODE.AUTO:
            effective = (
                self.intended_set_current
                if self.start_stop == EVC_CHARGE.ENABLED
                else 0.0
            )
        elif self.current_mode == EVC_MODE.SCHEDULED:
            effective = (
                self.intended_set_current if self._is_within_schedule(now) else 0.0
            )
        if (
            self.low_soc_enabled
            and self.low_soc_active
            and self.current_mode in (EVC_MODE.AUTO, EVC_MODE.SCHEDULED)
        ):
            effective = 0.0
        return max(0.0, min(effective, self.station_max_current))

    def _update_station_max_current(self) -> bool:
        """
        Update the station max current from Modbus with retries.

        Returns:
            True if read successful, False otherwise (uses fallback).

        References Alfen Modbus spec for register 1100 (Max Current).
        """
        retries = self.MAX_RETRIES
        for attempt in range(retries):
            try:
                rr_max_c = self.client.read_holding_registers(
                    self.config["registers"]["station_max_current"],
                    2,
                    slave=self.config["modbus"]["station_slave_id"],
                )
                if not rr_max_c.isError():
                    max_current = BinaryPayloadDecoder.fromRegisters(
                        rr_max_c.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
                    ).decode_32bit_float()
                    if not math.isnan(max_current) and max_current > 0:
                        self.station_max_current = float(max_current)
                        self.service["/MaxCurrent"] = round(self.station_max_current, 1)
                        return True
            except ModbusException as e:
                self.logger.debug(f"Station MaxCurrent read failed: {e}")
                if attempt < retries - 1:
                    time.sleep(self.RETRY_DELAY)
        self.logger.warning(
            "Failed to read station max current after retries. Using fallback."
        )
        self.station_max_current = self.config["defaults"]["station_max_current"]
        self.service["/MaxCurrent"] = round(self.station_max_current, 1)
        return False

    # Helper methods for Modbus operations
    def read_holding_registers(self, address: int, count: int, slave: int) -> List[int]:
        """
        Read holding registers from Modbus.

        Parameters:
            address: Starting register address.
            count: Number of registers to read.
            slave: Modbus slave ID.

        Returns:
            List of register values.

        Raises:
            ModbusException: If read fails.
        """
        rr = self.client.read_holding_registers(address, count, slave=slave)
        if rr.isError():
            raise ModbusException(f"Error reading registers at {address}")
        return rr.registers

    def decode_floats(self, registers: List[int], count: int) -> List[float]:
        """
        Decode list of registers into floats (assuming 2 registers per float).

        Parameters:
            registers: List of register values.
            count: Number of floats to decode.

        Returns:
            List of decoded float values (NaN replaced with 0.0).
        """
        values = []
        decoder = BinaryPayloadDecoder.fromRegisters(
            registers, byteorder=Endian.BIG, wordorder=Endian.BIG
        )
        for _ in range(count):
            val = decoder.decode_32bit_float()
            values.append(val if not math.isnan(val) else 0.0)
        return values

    def decode_64bit_float(self, registers: List[int]) -> float:
        """
        Decode 4 registers into a 64-bit float.

        Parameters:
            registers: List of 4 register values.

        Returns:
            The decoded float (NaN replaced with 0.0).
        """
        decoder = BinaryPayloadDecoder.fromRegisters(
            registers, byteorder=Endian.BIG, wordorder=Endian.BIG
        )
        val = decoder.decode_64bit_float()
        return val if not math.isnan(val) else 0.0

    def read_modbus_string(self, address: int, count: int, slave: int) -> str:
        """
        Read a string from Modbus holding registers.

        Parameters:
            address: Starting register address.
            count: Number of registers to read.
            slave: Modbus slave ID.

        Returns:
            The decoded string, stripped of nulls/spaces.

        Raises:
            ModbusException: If read fails (logged, returns "N/A").
        """
        try:
            regs = self.read_holding_registers(address, count, slave)
            bytes_list = []
            for reg in regs:
                bytes_list.append((reg >> 8) & 0xFF)
                bytes_list.append(reg & 0xFF)
            return "".join(chr(b) for b in bytes_list).strip("\x00 ")
        except ModbusException as e:
            self.logger.debug(f"Modbus string read failed: {e}")
            return "N/A"

    # Merged method for status and energy logic
    def process_status_and_energy(self) -> None:
        """
        Process status, apply logic, and update energy/charging time.

        Handles status mapping, mode logic, low SOC, auto-start, and energy calculations.
        References Alfen Modbus spec for status codes (register 1201).
        """
        # Read status
        status_regs = self.read_holding_registers(
            self.config["registers"]["status"],
            5,
            self.config["modbus"]["socket_slave_id"],
        )
        status_str = (
            "".join([chr((r >> 8) & 0xFF) + chr(r & 0xFF) for r in status_regs])
            .strip("\x00 ")
            .upper()
        )

        # Map Alfen status strings to raw status codes
        # Per Alfen Modbus spec (Implementation_of_Modbus_Slave_TCPIP_for_Alfen_NG9xx_platform.pdf):
        # C2/D2: Charging, B1/B2/C1/D1: Connected/Available, others: Disconnected/Error
        if status_str in ("C2", "D2"):
            raw_status = 2  # Charging
        elif status_str in ("B1", "B2", "C1", "D1"):
            raw_status = 1  # Connected
        else:
            raw_status = 0  # Disconnected

        old_victron_status = self.service["/Status"]
        connected = raw_status >= 1
        was_disconnected = old_victron_status == 0
        now_connected = connected

        new_victron_status = raw_status

        # Apply mode-specific logic for Victron status codes
        if (
            self.current_mode == EVC_MODE.MANUAL
            and connected
            and self.auto_start == 0
            and self.start_stop == EVC_CHARGE.DISABLED
        ):
            new_victron_status = 6  # Wait for start
        if self.current_mode == EVC_MODE.AUTO and connected:
            if self.start_stop == EVC_CHARGE.DISABLED:
                new_victron_status = 6
            elif self.intended_set_current <= self.MIN_CHARGING_CURRENT:
                new_victron_status = 4  # Low current
        if self.current_mode == EVC_MODE.SCHEDULED and connected:
            if not self._is_within_schedule(time.time()):
                new_victron_status = 6

        # Low SOC logic with hysteresis to prevent flapping
        battery_soc_value = self._read_battery_soc()
        if battery_soc_value is not None and not math.isnan(battery_soc_value):
            battery_soc_value = float(battery_soc_value)
        else:
            battery_soc_value = None

        if self.low_soc_enabled and battery_soc_value is not None:
            if self.low_soc_active:
                if battery_soc_value >= (
                    self.low_soc_threshold + self.low_soc_hysteresis
                ):
                    self.low_soc_active = False
            else:
                if battery_soc_value <= self.low_soc_threshold:
                    self.low_soc_active = True
            if self.low_soc_active and connected:
                new_victron_status = 7  # Low SOC pause

        self.service["/Status"] = new_victron_status

        # Auto-start logic: Enable charging on connection if configured
        if (
            now_connected
            and was_disconnected
            and self.auto_start == 1
            and self.start_stop == EVC_CHARGE.DISABLED
        ):
            self.start_stop = EVC_CHARGE.ENABLED
            self._persist_config_to_disk()
            self.logger.info(
                f"Auto-start triggered: Set StartStop to ENABLED (mode: {self.current_mode.name})"
            )
            target = self._compute_effective_current(time.time())
            if self._set_current(target, force_verify=True):
                self.last_current_set_time = time.time()
                self.last_sent_current = target
                self.logger.info(f"Auto-start applied current: {target:.2f} A")

        # Energy and charging time calculation
        # Uses total energy from register 374 (64-bit float, per Alfen spec)
        energy_regs = self.read_holding_registers(
            self.config["registers"]["energy"],
            4,
            self.config["modbus"]["socket_slave_id"],
        )
        total_energy_kwh = self.decode_64bit_float(energy_regs) / 1000.0

        if new_victron_status == 2 and old_victron_status != 2:
            self.charging_start_time = time.time()
            self.session_start_energy_kwh = total_energy_kwh
        elif new_victron_status != 2:
            self.charging_start_time = 0
            self.session_start_energy_kwh = 0

        self.service["/ChargingTime"] = (
            time.time() - self.charging_start_time
            if self.charging_start_time > 0
            else 0
        )

        if self.session_start_energy_kwh > 0:
            session_energy = total_energy_kwh - self.session_start_energy_kwh
            self.service["/Ac/Energy/Forward"] = round(session_energy, 3)
        else:
            self.service["/Ac/Energy/Forward"] = 0.0

    def _read_firmware_version(self) -> None:
        """
        Read and update firmware version from Modbus.

        Updates /FirmwareVersion D-Bus path.
        """
        fw_str = self.read_modbus_string(
            self.config["registers"]["firmware_version"],
            self.config["registers"]["firmware_version_count"],
            self.config["modbus"]["station_slave_id"],
        )
        self.service["/FirmwareVersion"] = fw_str

    def _read_station_serial(self) -> None:
        """
        Read and update station serial from Modbus.

        Updates /Serial D-Bus path.
        """
        sn_str = self.read_modbus_string(
            self.config["registers"]["station_serial"],
            self.config["registers"]["station_serial_count"],
            self.config["modbus"]["station_slave_id"],
        )
        self.service["/Serial"] = sn_str

    def _read_product_name(self) -> None:
        """
        Read and update product name from Modbus.

        Combines manufacturer and platform type for /ProductName.
        """
        mfg_str = self.read_modbus_string(
            self.config["registers"]["manufacturer"],
            self.config["registers"]["manufacturer_count"],
            self.config["modbus"]["station_slave_id"],
        )
        pt_str = self.read_modbus_string(
            self.config["registers"]["platform_type"],
            self.config["registers"]["platform_type_count"],
            self.config["modbus"]["station_slave_id"],
        )
        self.service["/ProductName"] = f"{mfg_str} {pt_str}"

    def fetch_raw_data(self) -> Dict[str, List[int]]:
        """
        Fetch raw register data from Modbus for AC measurements.

        Returns:
            Dictionary of raw register lists for voltages, currents, power, phases.

        Raises:
            ModbusException: If any read fails (propagated to poll).
        """
        return {
            "voltages": self.read_holding_registers(
                self.config["registers"]["voltages"],
                6,
                self.config["modbus"]["socket_slave_id"],
            ),
            "currents": self.read_holding_registers(
                self.config["registers"]["currents"],
                6,
                self.config["modbus"]["socket_slave_id"],
            ),
            "power": self.read_holding_registers(
                self.config["registers"]["power"],
                2,
                self.config["modbus"]["socket_slave_id"],
            ),
            "phases": self.read_holding_registers(
                self.config["registers"]["phases"],
                1,
                self.config["modbus"]["socket_slave_id"],
            ),
        }

    def process_logic(self) -> None:
        """
        Process business logic including status, energy, max current, and clamping.
        """
        self.process_status_and_energy()
        self._update_station_max_current()
        if self.current_mode == EVC_MODE.MANUAL:
            self._clamp_intended_current_to_max()

    def update_dbus_paths(self, raw_data: Dict[str, List[int]]) -> None:
        """
        Update D-Bus paths with processed data from raw registers.

        Parameters:
            raw_data: Dictionary of raw register data.
        """
        voltages = self.decode_floats(raw_data["voltages"], 3)
        self.service["/Ac/L1/Voltage"] = round(voltages[0], 2)
        self.service["/Ac/L2/Voltage"] = round(voltages[1], 2)
        self.service["/Ac/L3/Voltage"] = round(voltages[2], 2)

        currents = self.decode_floats(raw_data["currents"], 3)
        self.service["/Ac/L1/Current"] = round(currents[0], 2)
        self.service["/Ac/L2/Current"] = round(currents[1], 2)
        self.service["/Ac/L3/Current"] = round(currents[2], 2)

        current_a = round(max(currents), 2)
        self.service["/Ac/Current"] = current_a
        self.service["/Current"] = current_a

        self.service["/Ac/L1/Power"] = round(voltages[0] * currents[0], 2)
        self.service["/Ac/L2/Power"] = round(voltages[1] * currents[1], 2)
        self.service["/Ac/L3/Power"] = round(voltages[2] * currents[2], 2)

        power = self.decode_floats(raw_data["power"], 1)[0]
        self.service["/Ac/Power"] = round(power, 2)

        self.service["/Ac/PhaseCount"] = raw_data["phases"][0]

    def apply_controls(self) -> None:
        """
        Apply control actions like setting effective current.
        """
        self._set_effective_current()

    def poll(self) -> bool:
        """
        Poll the Modbus device for updates and apply logic.

        Returns:
            Always True (for GLib timeout compatibility).

        Handles reconnection and sets /Connected status.
        """
        try:
            if not self.client.is_socket_open():
                if not self.reconnect():
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
            self.reconnect()
            self.service["/Connected"] = 0
        except ConnectionError as e:
            self.logger.error(f"Connection error: {e}. Attempting reconnect.")
            self.reconnect()
            self.service["/Connected"] = 0
        return True

    def run(self) -> None:
        """
        Run the main GLib loop with periodic polling.
        """
        GLib.timeout_add(
            self.config.get("poll_interval_ms", self.POLL_INTERVAL_MS), self.poll
        )
        mainloop = GLib.MainLoop()
        mainloop.run()

    def reconnect(self, retries: int = 3) -> bool:
        """
        Attempt to reconnect to the Modbus server with retries.

        Parameters:
            retries: Number of reconnection attempts (default 3).

        Returns:
            True if reconnected successfully, False otherwise.
        """
        self.client.close()
        for attempt in range(retries):
            self.logger.info(f"Attempting Modbus reconnect (attempt {attempt + 1})...")
            if self.client.connect():
                self.logger.info("Modbus connection re-established.")
                # Re-read static info after reconnect
                self._read_firmware_version()
                self._read_station_serial()
                self._read_product_name()
                return True
            time.sleep(self.RETRY_DELAY)
        self.logger.error("Failed to reconnect to Modbus after retries.")
        return False


def main() -> None:
    """
    Entry point: Set up D-Bus main loop and run the driver.
    """
    DBusGMainLoop(set_as_default=True)
    driver = AlfenDriver()
    driver.run()


if __name__ == "__main__":
    main()
