#!/usr/bin/env python3

import enum
import json
import logging
import math
import os
import sys
import time
import traceback
from typing import Any, Dict, Optional

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

    def __init__(self):
        """Initialize the AlfenDriver with default values and setup."""
        self.config: Dict[str, Any] = self._load_config()
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
        self.logger: logging.Logger = logging.getLogger("alfen_driver")
        self.config_file_path: str = f"/data/evcharger_alfen_{device_instance}.json"

        self._load_initial_config()

        self.service.add_path("/Mgmt/ProcessName", __file__)
        self.service.add_path("/Mgmt/ProcessVersion", "1.4")
        self.service.add_path(
            "/Mgmt/Connection", f"Modbus TCP at {modbus_config['ip']}"
        )
        self.service.add_path("/DeviceInstance", device_instance)
        self.service.add_path("/Connected", 0)
        self.service.add_path("/ProductName", "Alfen EV Charger")
        self.service.add_path("/ProductId", 0xA142)
        self.service.add_path("/FirmwareVersion", "N/A")
        self.service.add_path("/Serial", "ALFEN-001")
        self.service.add_path("/Status", 0)
        self.service.add_path(
            "/Mode",
            self.current_mode.value,
            writeable=True,
            onchangecallback=self.mode_callback,
        )
        self.service.add_path(
            "/StartStop",
            self.start_stop.value,
            writeable=True,
            onchangecallback=self.startstop_callback,
        )
        self.service.add_path(
            "/SetCurrent",
            self.intended_set_current,
            writeable=True,
            onchangecallback=self.set_current_callback,
        )
        self.service.add_path("/MaxCurrent", 32.0)
        self.service.add_path(
            "/AutoStart",
            self.auto_start,
            writeable=True,
            onchangecallback=self.autostart_callback,
        )
        self.service.add_path("/ChargingTime", 0)
        self.service.add_path("/Current", 0.0)
        self.service.add_path("/Ac/Current", 0.0)
        self.service.add_path("/Ac/Power", 0.0)
        self.service.add_path("/Ac/Energy/Forward", 0.0)
        self.service.add_path("/Ac/PhaseCount", 0)
        self.service.add_path("/Position", 0, writeable=True)
        self.service.add_path("/Ac/L1/Voltage", 0.0)
        self.service.add_path("/Ac/L1/Current", 0.0)
        self.service.add_path("/Ac/L1/Power", 0.0)
        self.service.add_path("/Ac/L2/Voltage", 0.0)
        self.service.add_path("/Ac/L2/Current", 0.0)
        self.service.add_path("/Ac/L2/Power", 0.0)
        self.service.add_path("/Ac/L3/Voltage", 0.0)
        self.service.add_path("/Ac/L3/Current", 0.0)
        self.service.add_path("/Ac/L3/Power", 0.0)
        self.service.add_path(
            "/Schedule/Enabled",
            self.schedule_enabled,
            writeable=True,
            onchangecallback=self.schedule_enabled_callback,
        )
        self.service.add_path(
            "/Schedule/Days",
            self.schedule_days_mask,
            writeable=True,
            onchangecallback=self.schedule_days_callback,
        )
        self.service.add_path(
            "/Schedule/Start",
            self.schedule_start,
            writeable=True,
            onchangecallback=self.schedule_start_callback,
        )
        self.service.add_path(
            "/Schedule/End",
            self.schedule_end,
            writeable=True,
            onchangecallback=self.schedule_end_callback,
        )
        self.service.add_path(
            "/LowSoc/Enabled",
            self.low_soc_enabled,
            writeable=True,
            onchangecallback=self.low_soc_enabled_callback,
        )
        self.service.add_path(
            "/LowSoc/Threshold",
            self.low_soc_threshold,
            writeable=True,
            onchangecallback=self.low_soc_threshold_callback,
        )
        self.service.add_path(
            "/LowSoc/Hysteresis",
            self.low_soc_hysteresis,
            writeable=True,
            onchangecallback=self.low_soc_hysteresis_callback,
        )
        self.service.add_path("/LowSoc/Value", 0.0)

        self.service.register()

    def _load_initial_config(self) -> None:
        """Load initial configuration from D-Bus or disk."""
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
        try:
            obj = bus.get_object(self.service_name, path)
            return obj.GetValue(dbus_interface="com.victronenergy.BusItem")
        except Exception:
            return None

    def _persist_config_to_disk(self) -> None:
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
        try:
            if not os.path.exists(self.config_file_path):
                return None
            with open(self.config_file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.logger.warning(f"Failed to load persisted config: {e}")
            return None

    def _apply_config(self, data: Dict[str, Any]) -> None:
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
        """Parse HH:MM string to minutes since midnight."""
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
        """Check if current time is within the scheduled window."""
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
        """Load configuration from JSON file, falling back to defaults."""
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

    def _write_current_with_verification(self, target_amps: float) -> bool:
        try:
            builder = BinaryPayloadBuilder(byteorder=Endian.BIG, wordorder=Endian.BIG)
            builder.add_32bit_float(float(target_amps))
            payload = builder.to_registers()
            self.client.write_registers(
                self.config["registers"]["amps_config"],
                payload,
                slave=self.config["modbus"]["socket_slave_id"],
            )
            rr = self.client.read_holding_registers(
                self.config["registers"]["amps_config"],
                2,
                slave=self.config["modbus"]["socket_slave_id"],
            )
            regs = rr.registers if hasattr(rr, "registers") else []
            if len(regs) == 2:
                dec = BinaryPayloadDecoder.fromRegisters(
                    regs, byteorder=Endian.BIG, wordorder=Endian.BIG
                ).decode_32bit_float()
                self.logger.info(f"SetCurrent write: raw={regs}, dec={dec:.3f}")
                if math.isclose(
                    dec, float(target_amps), abs_tol=self.CURRENT_TOLERANCE
                ):
                    return True
        except Exception as e:
            self.logger.error(f"SetCurrent write failed: {e}")
        return False

    def set_current_callback(self, path: str, value: Any) -> bool:
        """Handle changes to the set current value from D-Bus."""
        try:
            requested = max(0.0, min(self.MAX_SET_CURRENT, float(value)))
            if self.current_mode == EVC_MODE.MANUAL:
                self._update_station_max_current()
                max_allowed = max(0.0, float(self.station_max_current))
                self.intended_set_current = min(requested, max_allowed)
            else:
                self.intended_set_current = requested
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
                if self._write_current_with_verification(target):
                    self.last_current_set_time = time.time()
                    self.last_sent_current = target
                    self.logger.info(
                        f"Immediate SetCurrent applied: {target:.2f} A (MANUAL)"
                    )
            self.logger.info(f"SetCurrent changed to {self.intended_set_current:.2f} A")
            return True
        except (ValueError, TypeError, ModbusException) as e:
            self.logger.error(f"Set current error: {e}\n{traceback.format_exc()}")
            return False

    def mode_callback(self, path: str, value: Any) -> bool:
        """Handle changes to the mode from D-Bus."""
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

            if self._write_current_with_verification(effective_current):
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
            self._persist_config_to_disk()
            if self.current_mode == EVC_MODE.MANUAL:
                target = (
                    self.intended_set_current
                    if self.start_stop == EVC_CHARGE.ENABLED
                    else 0.0
                )
                if self._write_current_with_verification(target):
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
        self.auto_start = int(value)
        self._persist_config_to_disk()
        self.logger.info(f"AutoStart changed to {self.auto_start}")
        return True

    def schedule_enabled_callback(self, path: str, value: Any) -> bool:
        try:
            self.schedule_enabled = int(value)
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def schedule_days_callback(self, path: str, value: Any) -> bool:
        try:
            self.schedule_days_mask = int(value) & 0x7F
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def schedule_start_callback(self, path: str, value: Any) -> bool:
        try:
            _ = self._parse_hhmm_to_minutes(str(value))
            self.schedule_start = str(value)
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def schedule_end_callback(self, path: str, value: Any) -> bool:
        try:
            _ = self._parse_hhmm_to_minutes(str(value))
            self.schedule_end = str(value)
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def low_soc_enabled_callback(self, path: str, value: Any) -> bool:
        try:
            self.low_soc_enabled = int(value)
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def low_soc_threshold_callback(self, path: str, value: Any) -> bool:
        try:
            self.low_soc_threshold = float(value)
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def low_soc_hysteresis_callback(self, path: str, value: Any) -> bool:
        try:
            self.low_soc_hysteresis = max(0.0, float(value))
            self._persist_config_to_disk()
            return True
        except (ValueError, TypeError):
            return False

    def _compute_effective_current(self, now: float) -> float:
        """Calculate the effective charging current based on mode, schedule, and low SoC conditions."""
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
        """Update the station max current from Modbus with retries and return True if successful."""
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

    def _update_ac_measurements(self) -> None:
        """Read and update AC voltages, currents, power, and phase count from Modbus."""
        rr_v = self.client.read_holding_registers(
            self.config["registers"]["voltages"],
            6,
            slave=self.config["modbus"]["socket_slave_id"],
        )
        decoder_v = BinaryPayloadDecoder.fromRegisters(
            rr_v.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
        )
        v1, v2, v3 = (
            decoder_v.decode_32bit_float(),
            decoder_v.decode_32bit_float(),
            decoder_v.decode_32bit_float(),
        )
        self.service["/Ac/L1/Voltage"] = round(v1 if not math.isnan(v1) else 0, 2)
        self.service["/Ac/L2/Voltage"] = round(v2 if not math.isnan(v2) else 0, 2)
        self.service["/Ac/L3/Voltage"] = round(v3 if not math.isnan(v3) else 0, 2)
        rr_c = self.client.read_holding_registers(
            self.config["registers"]["currents"],
            6,
            slave=self.config["modbus"]["socket_slave_id"],
        )
        decoder_c = BinaryPayloadDecoder.fromRegisters(
            rr_c.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
        )
        i1, i2, i3 = (
            decoder_c.decode_32bit_float(),
            decoder_c.decode_32bit_float(),
            decoder_c.decode_32bit_float(),
        )
        self.service["/Ac/L1/Current"] = round(i1 if not math.isnan(i1) else 0, 2)
        self.service["/Ac/L2/Current"] = round(i2 if not math.isnan(i2) else 0, 2)
        self.service["/Ac/L3/Current"] = round(i3 if not math.isnan(i3) else 0, 2)
        current_a = round(max(i1, i2, i3), 2)
        self.service["/Ac/Current"] = current_a
        self.service["/Current"] = current_a
        self.service["/Ac/L1/Power"] = round(v1 * i1, 2)
        self.service["/Ac/L2/Power"] = round(v2 * i2, 2)
        self.service["/Ac/L3/Power"] = round(v3 * i3, 2)
        rr_p = self.client.read_holding_registers(
            self.config["registers"]["power"],
            2,
            slave=self.config["modbus"]["socket_slave_id"],
        )
        power = BinaryPayloadDecoder.fromRegisters(
            rr_p.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
        ).decode_32bit_float()
        self.service["/Ac/Power"] = round(power if not math.isnan(power) else 0)
        rr_ph = self.client.read_holding_registers(
            self.config["registers"]["phases"],
            1,
            slave=self.config["modbus"]["socket_slave_id"],
        )
        self.service["/Ac/PhaseCount"] = rr_ph.registers[0]

    def poll(self) -> bool:
        """Poll the Modbus device for updates and apply logic."""
        try:
            if not self.client.is_socket_open():
                self.logger.info(
                    "Modbus connection is closed. Attempting to reconnect..."
                )
                if not self.client.connect():
                    self.logger.error(
                        "Failed to reconnect to Alfen. Will retry on next poll."
                    )
                    self.service["/Connected"] = 0
                    return True
                self.logger.info("Modbus connection re-established.")
                self._read_firmware_version()
                self._read_station_serial()
                self._read_product_name()

            self._update_status_and_apply_logic()
            self._update_energy_and_charging_time()
            self._update_station_max_current()
            if self.current_mode == EVC_MODE.MANUAL:
                self._clamp_intended_current_to_max()
            self._update_ac_measurements()
            self._set_effective_current()

            self.service["/Connected"] = 1
            self.logger.debug("Poll completed successfully")

        except (ModbusException, ConnectionError) as e:
            self.logger.error(f"Poll error: {e}. The connection will be retried.")
            self.client.close()
            self.service["/Connected"] = 0
        return True

    def _update_status_and_apply_logic(self) -> None:
        """Update status from Modbus and apply logic for victron status, low SOC, and auto-start."""
        rr_status = self.client.read_holding_registers(
            self.config["registers"]["status"],
            5,
            slave=self.config["modbus"]["socket_slave_id"],
        )
        if rr_status.isError():
            raise ConnectionError("Modbus error reading status")
        status_str = (
            "".join([chr((r >> 8) & 0xFF) + chr(r & 0xFF) for r in rr_status.registers])
            .strip("\x00 ")
            .upper()
        )

        # Map Alfen status strings to raw status codes
        if status_str in ("C2", "D2"):
            raw_status = 2  # Charging
        elif status_str in ("B1", "B2", "C1", "D1"):
            raw_status = 1  # Connected
        else:
            raw_status = 0  # Disconnected

        old_victron_status = self.service["/Status"]

        connected = raw_status >= 1

        # Detect connection event
        was_disconnected = old_victron_status == 0
        now_connected = connected

        new_victron_status = raw_status
        if (
            self.current_mode == EVC_MODE.MANUAL
            and connected
            and self.auto_start == 0
            and self.start_stop == EVC_CHARGE.DISABLED
        ):
            new_victron_status = 6  # WAIT_START
        if self.current_mode == EVC_MODE.AUTO and connected:
            if self.start_stop == EVC_CHARGE.DISABLED:
                new_victron_status = 6
            elif self.intended_set_current <= self.MIN_CHARGING_CURRENT:
                new_victron_status = 4
        if self.current_mode == EVC_MODE.SCHEDULED and connected:
            if not self._is_within_schedule(time.time()):
                new_victron_status = 6

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
                new_victron_status = 7

        self.service["/Status"] = new_victron_status

        # Auto-start logic: if just connected in ANY mode and auto_start enabled, enable StartStop
        if (
            now_connected
            and was_disconnected
            and self.auto_start == 1
            and self.start_stop
            == EVC_CHARGE.DISABLED  # Avoid re-triggering if already enabled
        ):
            self.start_stop = EVC_CHARGE.ENABLED
            self._persist_config_to_disk()
            self.logger.info(
                f"Auto-start triggered: Set StartStop to ENABLED (mode: {self.current_mode.name})"
            )
            # Apply the current immediately
            target = self._compute_effective_current(time.time())
            if self._set_current(target, force_verify=True):
                self.last_current_set_time = time.time()
                self.last_sent_current = target
                self.logger.info(f"Auto-start applied current: {target:.2f} A")

    def _update_energy_and_charging_time(self) -> None:
        """Update energy readings and charging time from Modbus."""
        rr_e = self.client.read_holding_registers(
            self.config["registers"]["energy"],
            4,
            slave=self.config["modbus"]["socket_slave_id"],
        )
        total_energy_kwh = (
            BinaryPayloadDecoder.fromRegisters(
                rr_e.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            ).decode_64bit_float()
            / 1000.0
        )

        old_victron_status = self.service["/Status"]
        new_victron_status = self.service[
            "/Status"
        ]  # Assuming updated in previous method

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
            self.service["/Ac/Energy/Forward"] = round(
                session_energy if not math.isnan(session_energy) else 0, 3
            )
        else:
            self.service["/Ac/Energy/Forward"] = 0.0

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
        """Set the current via Modbus with verification and retries."""
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
                    time.sleep(self.VERIFICATION_DELAY)  # Small delay for verification
                    rr = self.client.read_holding_registers(
                        self.config["registers"]["amps_config"],
                        2,
                        slave=self.config["modbus"]["socket_slave_id"],
                    )
                    regs = rr.registers if hasattr(rr, "registers") else []
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
                    return True  # Assume success if not verifying
            except Exception as e:
                self.logger.error(
                    f"SetCurrent write failed on attempt {attempt+1}: {e}"
                )
                if attempt < retries - 1:
                    time.sleep(self.RETRY_DELAY)
        return False

    def _set_effective_current(self, force: bool = False) -> None:
        """Set the effective current based on mode and watchdog."""
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

    def _read_firmware_version(self) -> None:
        try:
            rr_fw = self.client.read_holding_registers(
                self.config["registers"]["firmware_version"],
                self.config["registers"]["firmware_version_count"],
                slave=self.config["modbus"]["station_slave_id"],
            )
            fw_regs = rr_fw.registers if hasattr(rr_fw, "registers") else []
            bytes_fw = []
            for reg in fw_regs:
                bytes_fw.append((reg >> 8) & 0xFF)
                bytes_fw.append(reg & 0xFF)
            fw_str = "".join(chr(b) for b in bytes_fw).strip("\x00 ")
            self.service["/FirmwareVersion"] = fw_str
        except ModbusException as e:
            self.logger.debug(f"FirmwareVersion read failed: {e}")

    def _read_station_serial(self) -> None:
        try:
            rr_sn = self.client.read_holding_registers(
                self.config["registers"]["station_serial"],
                self.config["registers"]["station_serial_count"],
                slave=self.config["modbus"]["station_slave_id"],
            )
            sn_regs = rr_sn.registers if hasattr(rr_sn, "registers") else []
            bytes_sn = []
            for reg in sn_regs:
                bytes_sn.append((reg >> 8) & 0xFF)
                bytes_sn.append(reg & 0xFF)
            sn_str = "".join(chr(b) for b in bytes_sn).strip("\x00 ")
            self.service["/Serial"] = sn_str
        except ModbusException as e:
            self.logger.debug(f"Serial read failed: {e}")

    def _read_product_name(self) -> None:
        try:
            rr_mfg = self.client.read_holding_registers(
                self.config["registers"]["manufacturer"],
                self.config["registers"]["manufacturer_count"],
                slave=self.config["modbus"]["station_slave_id"],
            )
            mfg_regs = rr_mfg.registers if hasattr(rr_mfg, "registers") else []
            bytes_mfg = []
            for reg in mfg_regs:
                bytes_mfg.append((reg >> 8) & 0xFF)
                bytes_mfg.append(reg & 0xFF)
            mfg_str = "".join(chr(b) for b in bytes_mfg).strip("\x00 ")

            rr_pt = self.client.read_holding_registers(
                self.config["registers"]["platform_type"],
                self.config["registers"]["platform_type_count"],
                slave=self.config["modbus"]["station_slave_id"],
            )
            pt_regs = rr_pt.registers if hasattr(rr_pt, "registers") else []
            bytes_pt = []
            for reg in pt_regs:
                bytes_pt.append((reg >> 8) & 0xFF)
                bytes_pt.append(reg & 0xFF)
            pt_str = "".join(chr(b) for b in bytes_pt).strip("\x00 ")

            self.service["/ProductName"] = f"{mfg_str} {pt_str}"
        except ModbusException as e:
            self.logger.debug(f"ProductName creation failed: {e}")

    def run(self) -> None:
        GLib.timeout_add(1000, self.poll)
        mainloop = GLib.MainLoop()
        mainloop.run()


def main() -> None:
    DBusGMainLoop(set_as_default=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("/var/log/alfen_driver.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    driver = AlfenDriver()
    driver.run()


if __name__ == "__main__":
    main()
