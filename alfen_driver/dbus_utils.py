import enum

from vedbus import VeDbusService


class EVC_MODE(enum.IntEnum):
    MANUAL = 0
    AUTO = 1
    SCHEDULED = 2


class EVC_CHARGE(enum.IntEnum):
    DISABLED = 0
    ENABLED = 1


def register_dbus_service(
    service_name: str,
    config: dict,
    current_mode: EVC_MODE,
    start_stop: EVC_CHARGE,
    auto_start: int,
    intended_set_current: float,
    schedule_enabled: int,
    schedule_days_mask: int,
    schedule_start: str,
    schedule_end: str,
    low_soc_enabled: int,
    low_soc_threshold: float,
    low_soc_hysteresis: float,
    mode_callback: callable,
    startstop_callback: callable,
    set_current_callback: callable,
    autostart_callback: callable,
    schedule_enabled_callback: callable,
    schedule_days_callback: callable,
    schedule_start_callback: callable,
    schedule_end_callback: callable,
    low_soc_enabled_callback: callable,
    low_soc_threshold_callback: callable,
    low_soc_hysteresis_callback: callable,
) -> VeDbusService:
    service = VeDbusService(service_name, register=False)

    modbus_config = config["modbus"]
    device_instance = config["device_instance"]

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
            "value": current_mode.value,
            "writeable": True,
            "callback": mode_callback,
        },
        {
            "path": "/StartStop",
            "value": start_stop.value,
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
        {
            "path": "/Schedule/Enabled",
            "value": schedule_enabled,
            "writeable": True,
            "callback": schedule_enabled_callback,
        },
        {
            "path": "/Schedule/Days",
            "value": schedule_days_mask,
            "writeable": True,
            "callback": schedule_days_callback,
        },
        {
            "path": "/Schedule/Start",
            "value": schedule_start,
            "writeable": True,
            "callback": schedule_start_callback,
        },
        {
            "path": "/Schedule/End",
            "value": schedule_end,
            "writeable": True,
            "callback": schedule_end_callback,
        },
        {
            "path": "/LowSoc/Enabled",
            "value": low_soc_enabled,
            "writeable": True,
            "callback": low_soc_enabled_callback,
        },
        {
            "path": "/LowSoc/Threshold",
            "value": low_soc_threshold,
            "writeable": True,
            "callback": low_soc_threshold_callback,
        },
        {
            "path": "/LowSoc/Hysteresis",
            "value": low_soc_hysteresis,
            "writeable": True,
            "callback": low_soc_hysteresis_callback,
        },
        {"path": "/LowSoc/Value", "value": 0.0},
        {"path": "/LowSoc/ActiveReason", "value": ""},
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
