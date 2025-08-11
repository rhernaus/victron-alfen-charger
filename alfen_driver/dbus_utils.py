import enum
import logging
import time

import dbus
from vedbus import VeDbusService

from .config import Config, ScheduleItem


class EVC_MODE(enum.IntEnum):
    MANUAL = 0
    AUTO = 1
    SCHEDULED = 2


class EVC_CHARGE(enum.IntEnum):
    DISABLED = 0
    ENABLED = 1


class EVC_STATUS(enum.IntEnum):
    DISCONNECTED = 0
    CONNECTED = 1
    CHARGING = 2
    CHARGED = 3
    WAIT_SUN = 4
    WAIT_START = 6
    LOW_SOC = 7


def register_dbus_service(
    service_name: str,
    config: Config,
    current_mode: EVC_MODE,
    start_stop: EVC_CHARGE,
    auto_start: int,
    intended_set_current: float,
    schedules: list[ScheduleItem],
    mode_callback: callable,
    startstop_callback: callable,
    set_current_callback: callable,
    autostart_callback: callable,
) -> VeDbusService:
    service = VeDbusService(service_name, register=False)
    modbus_config = config.modbus
    device_instance = config.device_instance
    dbus_paths = [
        {"path": "/Mgmt/ProcessName", "value": __file__},
        {"path": "/Mgmt/ProcessVersion", "value": "1.4"},
        {
            "path": "/Mgmt/Connection",
            "value": f"Modbus TCP at {modbus_config.ip}",
        },
        {"path": "/DeviceInstance", "value": device_instance},
        {"path": "/Connected", "value": 0},
        {"path": "/ProductName", "value": "Alfen EV Charger"},
        {"path": "/ProductId", "value": 0xC024},
        {"path": "/FirmwareVersion", "value": "N/A"},
        {"path": "/Serial", "value": "ALFEN-001"},
        {"path": "/Status", "value": 0},
        {
            "path": "/Mode",
            "value": current_mode,
            "writeable": True,
            "callback": mode_callback,
        },
        {
            "path": "/StartStop",
            "value": start_stop,
            "writeable": True,
            "callback": startstop_callback,
        },
        {
            "path": "/SetCurrent",
            "value": intended_set_current,
            "writeable": True,
            "callback": set_current_callback,
        },
        {"path": "/MaxCurrent", "value": 32.0},
        {
            "path": "/AutoStart",
            "value": auto_start,
            "writeable": True,
            "callback": autostart_callback,
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
    ]
    for p in dbus_paths:
        service.add_path(
            p["path"],
            p["value"],
            writeable=p.get("writeable", False),
            onchangecallback=p.get("callback", None),
        )
    service.register()
    return service


def get_current_ess_strategy() -> str:
    """Determine if Victron is buying, selling (from battery), or idle based on grid and battery power."""
    try:
        bus = dbus.SystemBus()
        system = bus.get_object("com.victronenergy.system", "/")
        all_values = system.GetValue()
        grid_l1 = all_values.get("Ac/Grid/L1/Power", 0.0)
        grid_l2 = all_values.get("Ac/Grid/L2/Power", 0.0)
        grid_l3 = all_values.get("Ac/Grid/L3/Power", 0.0)
        grid_total = grid_l1 + grid_l2 + grid_l3
        battery_power = all_values.get(
            "Dc/Battery/Power", 0.0
        )  # Positive: charging, negative: discharging
        threshold = 250.0  # Watts, to avoid noise around zero
        if grid_total > threshold:
            return "buying"
        elif grid_total < -threshold and battery_power < -threshold:
            return "selling"
        else:
            return "idle"
    except Exception as e:
        logging.error(f"Error getting grid strategy: {e}")
        return "idle"
