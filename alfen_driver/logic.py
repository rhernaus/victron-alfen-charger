import logging
import time
from typing import Any

import dbus

from .config import Config, ScheduleItem, parse_hhmm_to_minutes
from .dbus_utils import EVC_CHARGE, EVC_MODE, get_current_ess_strategy
from .modbus_utils import decode_64bit_float, read_holding_registers

MIN_CHARGING_CURRENT: float = 0.1

NOMINAL_VOLTAGE = 230.0  # Configurable if needed
MIN_CURRENT = 6.0
MAX_CURRENT = 16.0  # Example, make configurable

_config = None  # Module-level cache


def clamp_value(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(value, max_val))


def is_within_any_schedule(
    schedules: list[ScheduleItem],
    now: float,
) -> bool:
    """
    Check if current time is within any of the scheduled windows.

    Parameters:
        now: Current time in seconds since epoch.

    Returns:
        True if within any schedule, False otherwise.

    Uses local time, day mask, and start/end times.
    """
    tm = time.localtime(now)
    weekday = tm.tm_wday  # Mon=0..Sun=6
    sun_based_index = (weekday + 1) % 7
    minutes_now = tm.tm_hour * 60 + tm.tm_min
    for item in schedules:
        if item.enabled == 0:
            continue
        if (item.days_mask & (1 << sun_based_index)) == 0:
            continue
        start_min = parse_hhmm_to_minutes(item.start)
        end_min = parse_hhmm_to_minutes(item.end)
        if start_min == end_min:
            continue
        if start_min < end_min:
            if start_min <= minutes_now < end_min:
                return True
        else:
            if minutes_now >= start_min or minutes_now < end_min:
                return True
    return False


def get_excess_solar_current(ev_power: float = 0.0) -> float:
    global _config
    try:
        bus = dbus.SystemBus()
        system = bus.get_object("com.victronenergy.system", "/")
        all_values = system.GetValue()  # Fetch entire system dict
        dc_pv = all_values.get("Dc/Pv/Power", 0.0)
        ac_pv_l1 = all_values.get("Ac/PvOnOutput/L1/Power", 0.0)
        ac_pv_l2 = all_values.get("Ac/PvOnOutput/L2/Power", 0.0)
        ac_pv_l3 = all_values.get("Ac/PvOnOutput/L3/Power", 0.0)
        total_pv = dc_pv + ac_pv_l1 + ac_pv_l2 + ac_pv_l3
        consumption = (
            all_values.get("Ac/Consumption/L1/Power", 0.0)
            + all_values.get("Ac/Consumption/L2/Power", 0.0)
            + all_values.get("Ac/Consumption/L3/Power", 0.0)
        )
        adjusted_consumption = consumption - ev_power
        battery_power = all_values.get(
            "Dc/Battery/Power", 0.0
        )  # Positive: charging, negative: discharging
        # Adjust excess: subtract battery charging (if positive) as it's using solar
        excess = max(0.0, total_pv - adjusted_consumption - max(0.0, battery_power))
        # Calculate current for 3 phases
        current = excess / (3 * NOMINAL_VOLTAGE)
        return clamp_value(current, MIN_CURRENT, MAX_CURRENT)
    except Exception as e:
        logging.error(f"Error calculating excess solar: {e}")
        return 0.0


def compute_effective_current(
    current_mode: EVC_MODE,
    start_stop: EVC_CHARGE,
    intended_set_current: float,
    station_max_current: float,
    now: float,
    schedules: list[ScheduleItem],
    ev_power: float = 0.0,  # New parameter
) -> float:
    effective = 0.0
    if current_mode == EVC_MODE.MANUAL:
        effective = intended_set_current if start_stop == EVC_CHARGE.ENABLED else 0.0
    elif current_mode == EVC_MODE.AUTO:
        if start_stop == EVC_CHARGE.DISABLED:
            effective = 0.0
        else:
            strategy = get_current_ess_strategy()
            if strategy == "buying":
                effective = station_max_current  # Max current
            elif strategy == "selling":
                effective = 0.0  # Disable
            else:
                effective = get_excess_solar_current(ev_power)  # Pass ev_power
    elif current_mode == EVC_MODE.SCHEDULED:
        effective = (
            intended_set_current if is_within_any_schedule(schedules, now) else 0.0
        )
    return max(0.0, min(effective, station_max_current))


def map_alfen_status(client: Any, config: Config) -> int:
    """Map Alfen status string to raw status code (0=Disconnected, 1=Connected, 2=Charging)."""
    status_regs = read_holding_registers(
        client,
        config.registers.status,
        5,
        config.modbus.socket_slave_id,
    )
    status_str = (
        "".join([chr((r >> 8) & 0xFF) + chr(r & 0xFF) for r in status_regs])
        .strip("\x00 ")
        .upper()
    )
    if status_str in ("C2", "D2"):
        return 2  # Charging
    elif status_str in ("B1", "B2", "C1", "D1"):
        return 1  # Connected
    else:
        return 0  # Disconnected


def apply_mode_specific_status(
    current_mode: EVC_MODE,
    connected: bool,
    auto_start: int,
    start_stop: EVC_CHARGE,
    intended_set_current: float,
    schedules: list[ScheduleItem],
    new_victron_status: int,
) -> int:
    """Adjust Victron status based on mode, auto-start, schedule, and low SOC."""
    if (
        current_mode == EVC_MODE.MANUAL
        and connected
        and auto_start == 0
        and start_stop == EVC_CHARGE.DISABLED
    ):
        new_victron_status = 6  # Wait for start
    if current_mode == EVC_MODE.AUTO and connected:
        if start_stop == EVC_CHARGE.DISABLED:
            new_victron_status = 6
        elif intended_set_current <= MIN_CHARGING_CURRENT:
            new_victron_status = 4  # Low current
    if current_mode == EVC_MODE.SCHEDULED and connected:
        if not is_within_any_schedule(schedules, time.time()):
            new_victron_status = 6

    return new_victron_status


def apply_auto_start(
    now_connected: bool,
    was_disconnected: bool,
    auto_start: int,
    start_stop: EVC_CHARGE,
    current_mode: EVC_MODE,
    intended_set_current: float,
    station_max_current: float,
    schedules: list[ScheduleItem],
    set_current: callable,
    persist_config_to_disk: callable,
    logger: logging.Logger,
) -> EVC_CHARGE:
    """Apply auto-start logic if vehicle connects and conditions are met."""
    if (
        now_connected
        and was_disconnected
        and auto_start == 1
        and start_stop == EVC_CHARGE.DISABLED
    ):
        start_stop = EVC_CHARGE.ENABLED
        persist_config_to_disk()
        logger.info(
            f"Auto-start triggered: Set StartStop to ENABLED (mode: {EVC_MODE(current_mode).name})"
        )
        target = compute_effective_current(
            current_mode,
            start_stop,
            intended_set_current,
            station_max_current,
            time.time(),
            schedules,
        )
        if set_current(target, force_verify=True):
            logger.info(f"Auto-start applied current: {target:.2f} A")
    return start_stop


def calculate_session_energy_and_time(
    client: Any,
    config: Config,
    service: Any,
    new_victron_status: int,
    old_victron_status: int,
    charging_start_time: float,
    session_start_energy_kwh: float,
) -> tuple[float, float]:
    """Calculate and update session energy and charging time."""
    energy_regs = read_holding_registers(
        client,
        config.registers.energy,
        4,
        config.modbus.socket_slave_id,
    )
    total_energy_kwh = decode_64bit_float(energy_regs) / 1000.0

    if new_victron_status == 2 and old_victron_status != 2:
        charging_start_time = time.time()
        session_start_energy_kwh = total_energy_kwh
    elif new_victron_status != 2:
        charging_start_time = 0
        session_start_energy_kwh = 0

    service["/ChargingTime"] = (
        time.time() - charging_start_time if charging_start_time > 0 else 0
    )

    if session_start_energy_kwh > 0:
        session_energy = total_energy_kwh - session_start_energy_kwh
        service["/Ac/Energy/Forward"] = round(session_energy, 3)
    else:
        service["/Ac/Energy/Forward"] = 0.0

    return charging_start_time, session_start_energy_kwh


def process_status_and_energy(
    client: Any,
    config: Config,
    service: Any,
    current_mode: EVC_MODE,
    start_stop: EVC_CHARGE,
    auto_start: int,
    intended_set_current: float,
    schedules: list[ScheduleItem],
    station_max_current: float,
    charging_start_time: float,
    session_start_energy_kwh: float,
    set_current: callable,
    persist_config_to_disk: callable,
    logger: logging.Logger,
) -> tuple[float, float]:
    raw_status = map_alfen_status(client, config)

    old_victron_status = service["/Status"]
    connected = raw_status >= 1
    was_disconnected = old_victron_status == 0
    now_connected = connected

    new_victron_status = raw_status

    new_victron_status = apply_mode_specific_status(
        current_mode,
        connected,
        auto_start,
        start_stop,
        intended_set_current,
        schedules,
        new_victron_status,
    )

    service["/Status"] = new_victron_status

    start_stop = apply_auto_start(
        now_connected,
        was_disconnected,
        auto_start,
        start_stop,
        current_mode,
        intended_set_current,
        station_max_current,
        schedules,
        set_current,
        persist_config_to_disk,
        logger,
    )

    charging_start_time, session_start_energy_kwh = calculate_session_energy_and_time(
        client,
        config,
        service,
        new_victron_status,
        old_victron_status,
        charging_start_time,
        session_start_energy_kwh,
    )

    return charging_start_time, session_start_energy_kwh
