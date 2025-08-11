import logging
import math
import time
from datetime import datetime
from typing import Any

import dbus
import pytz

from .config import Config, ScheduleItem, parse_hhmm_to_minutes
from .dbus_utils import EVC_CHARGE, EVC_MODE, get_current_ess_strategy
from .modbus_utils import decode_64bit_float, read_holding_registers

MIN_CHARGING_CURRENT: float = 0.1

NOMINAL_VOLTAGE = 230.0  # Configurable if needed
MIN_CURRENT = 6.0

_config = None  # Module-level cache


def clamp_value(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(value, max_val))


def is_within_any_schedule(
    schedules: list[ScheduleItem],
    now: float,
    timezone: str,
) -> bool:
    """
    Check if current time is within any of the scheduled windows.

    Parameters:
        now: Current time in seconds since epoch.

    Returns:
        True if within any schedule, False otherwise.

    Uses local time, day mask, and start/end times.
    """
    utc_dt = datetime.utcfromtimestamp(now)
    local_tz = pytz.timezone(timezone)
    local_dt = utc_dt.replace(tzinfo=pytz.utc).astimezone(local_tz)
    weekday = local_dt.weekday()  # Mon=0..Sun=6
    sun_based_index = (weekday + 1) % 7
    minutes_now = local_dt.hour * 60 + local_dt.minute
    logger = logging.getLogger("alfen_driver.logic")
    logger.debug(
        f"Checking schedules at local time {local_dt.strftime('%H:%M %A')} (minutes={minutes_now}, index={sun_based_index}, timezone={timezone})"
    )
    for idx, item in enumerate(schedules):
        if item.enabled == 0:
            logger.debug(f"Schedule {idx+1} skipped: disabled")
            continue
        mask_check = (item.days_mask & (1 << sun_based_index)) != 0
        if not mask_check:
            logger.debug(
                f"Schedule {idx+1} skipped: day not matched (mask={item.days_mask}, required bit={1 << sun_based_index})"
            )
            continue
        start_min = parse_hhmm_to_minutes(item.start)
        end_min = parse_hhmm_to_minutes(item.end)
        if start_min == end_min:
            logger.debug(f"Schedule {idx+1} skipped: start == end ({start_min})")
            continue
        is_overnight = start_min >= end_min
        condition = (
            (start_min <= minutes_now < end_min)
            if not is_overnight
            else (minutes_now >= start_min or minutes_now < end_min)
        )
        logger.debug(
            f"Schedule {idx+1}: start_min={start_min}, end_min={end_min}, overnight={is_overnight}, condition={condition}"
        )
        if condition:
            logger.debug(f"Schedule {idx+1} matched, returning True")
            return True
    logger.debug("No schedules matched, returning False")
    return False


def get_excess_solar_current(
    ev_power: float = 0.0, station_max: float = float("inf")
) -> tuple[float, str]:
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
        clamped_current = min(current, station_max)
        clamp_reason = ""
        if clamped_current < MIN_CURRENT and clamped_current > 0:
            clamped_current = 0.0
            clamp_reason = f" (below min {MIN_CURRENT}A, set to 0)"
        explanation = (
            f"total_pv={total_pv:.2f}W, "
            f"adjusted_consumption={adjusted_consumption:.2f}W (consumption={consumption:.2f}W - ev_power={ev_power:.2f}W), "
            f"battery_charging={max(0.0, battery_power):.2f}W, "
            f"excess={excess:.2f}W, "
            f"raw_current={current:.2f}A{clamp_reason} -> {clamped_current:.2f}A"
        )
        return clamped_current, explanation
    except Exception as e:
        logging.error(f"Error calculating excess solar: {e}")
        return 0.0, f"Error: {str(e)}"


def compute_effective_current(
    current_mode: EVC_MODE,
    start_stop: EVC_CHARGE,
    intended_set_current: float,
    station_max_current: float,
    now: float,
    schedules: list[ScheduleItem],
    ev_power: float = 0.0,  # New parameter
    timezone: str = "UTC",
) -> tuple[float, str]:
    effective = 0.0
    explanation = ""
    if current_mode == EVC_MODE.MANUAL:
        if start_stop == EVC_CHARGE.ENABLED:
            effective = intended_set_current
            state = "enabled"
        else:
            effective = 0.0
            state = "disabled"
        explanation = f"Manual mode {state}, intended_current={intended_set_current:.2f}A -> {effective:.2f}A"
    elif current_mode == EVC_MODE.AUTO:
        if start_stop == EVC_CHARGE.DISABLED:
            effective = 0.0
            explanation = "Auto mode disabled by start_stop"
        else:
            strategy = get_current_ess_strategy()
            if strategy == "buying":
                effective = station_max_current
                explanation = (
                    f"Auto mode buying strategy, set to max {station_max_current:.2f}A"
                )
            elif strategy == "selling":
                effective = 0.0
                explanation = "Auto mode selling strategy, disabled"
            else:
                effective, excess_exp = get_excess_solar_current(
                    ev_power, station_max_current
                )
                explanation = f"Auto mode excess solar: {excess_exp}"
    elif current_mode == EVC_MODE.SCHEDULED:
        utc_dt = datetime.utcfromtimestamp(now)
        local_tz = pytz.timezone(timezone)
        local_dt = utc_dt.replace(tzinfo=pytz.utc).astimezone(local_tz)
        weekday = local_dt.weekday()
        sun_based_index = (weekday + 1) % 7
        minutes_now = local_dt.hour * 60 + local_dt.minute
        local_time_str = local_dt.strftime("%H:%M")
        day_str = local_dt.strftime("%A")
        if start_stop == EVC_CHARGE.DISABLED:
            effective = 0.0
            explanation = f"Scheduled mode disabled by start_stop (local time: {local_time_str} on {day_str}, timezone: {timezone})"
        else:
            within = is_within_any_schedule(schedules, now, timezone)
            effective = station_max_current if within else 0.0
            status = "within" if within else "not within"
            explanation = f"Scheduled mode: {status} schedule (local time: {local_time_str} on {day_str}, timezone: {timezone}), set to {effective:.2f}A"
    clamped_effective = max(0.0, min(effective, station_max_current))
    if not math.isclose(clamped_effective, effective, abs_tol=0.01):
        explanation += f" (clamped from {effective:.2f}A to {clamped_effective:.2f}A)"
    return clamped_effective, explanation


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
    timezone: str,
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
        if not is_within_any_schedule(schedules, time.time(), timezone):
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
    timezone: str,
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
        logger.debug(
            f"Auto-start triggered: Set StartStop to ENABLED (mode: {EVC_MODE(current_mode).name})"
        )
        target, explanation = compute_effective_current(
            current_mode,
            start_stop,
            intended_set_current,
            station_max_current,
            time.time(),
            schedules,
            0.0,  # Default ev_power
            timezone,
        )
        if set_current(target, force_verify=True):
            logger.debug(
                f"Auto-start applied current: {target:.2f} A. Calculation: {explanation}"
            )
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
    timezone: str,
) -> tuple[float, float, bool]:
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
        timezone,
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
        timezone,
    )

    target, explanation = compute_effective_current(
        current_mode,
        start_stop,
        intended_set_current,
        station_max_current,
        time.time(),
        schedules,
        0.0,  # Default ev_power
        timezone,
    )
    if set_current(target, force_verify=True):
        logger.debug(
            f"Auto-start applied current: {target:.2f} A. Calculation: {explanation}"
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

    return (
        charging_start_time,
        session_start_energy_kwh,
        (now_connected and was_disconnected),
    )
