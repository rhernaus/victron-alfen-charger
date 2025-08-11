import logging
import math
import time
from typing import Any, Dict

from .config import parse_hhmm_to_minutes
from .dbus_utils import EVC_CHARGE, EVC_MODE
from .modbus_utils import decode_64bit_float, read_holding_registers

MIN_CHARGING_CURRENT: float = 0.1


def is_within_schedule(
    schedule_enabled: int,
    schedule_days_mask: int,
    schedule_start: str,
    schedule_end: str,
    now: float,
) -> bool:
    """
    Check if current time is within the scheduled window.

    Parameters:
        now: Current time in seconds since epoch.

    Returns:
        True if within schedule, False otherwise.

    Uses local time, day mask, and start/end times.
    """
    if schedule_enabled == 0:
        return False
    tm = time.localtime(now)
    weekday = tm.tm_wday  # Mon=0..Sun=6
    sun_based_index = (weekday + 1) % 7
    if (schedule_days_mask & (1 << sun_based_index)) == 0:
        return False
    minutes_now = tm.tm_hour * 60 + tm.tm_min
    start_min = parse_hhmm_to_minutes(schedule_start)
    end_min = parse_hhmm_to_minutes(schedule_end)
    if start_min == end_min:
        return False
    if start_min < end_min:
        return start_min <= minutes_now < end_min
    return minutes_now >= start_min or minutes_now < end_min


def compute_effective_current(
    current_mode: EVC_MODE,
    start_stop: EVC_CHARGE,
    intended_set_current: float,
    low_soc_enabled: int,
    low_soc_active: bool,
    station_max_current: float,
    now: float,
    schedule_enabled: int,
    schedule_days_mask: int,
    schedule_start: str,
    schedule_end: str,
) -> float:
    """
    Calculate the effective charging current based on mode, schedule, and low SoC conditions.

    Parameters:
        now: Current time in seconds since epoch.

    Returns:
        The computed effective current (clamped to 0 - station_max_current).
    """
    effective = 0.0
    if current_mode == EVC_MODE.MANUAL:
        effective = intended_set_current if start_stop == EVC_CHARGE.ENABLED else 0.0
    elif current_mode == EVC_MODE.AUTO:
        effective = intended_set_current if start_stop == EVC_CHARGE.ENABLED else 0.0
    elif current_mode == EVC_MODE.SCHEDULED:
        effective = (
            intended_set_current
            if is_within_schedule(
                schedule_enabled, schedule_days_mask, schedule_start, schedule_end, now
            )
            else 0.0
        )
    if (
        low_soc_enabled
        and low_soc_active
        and current_mode in (EVC_MODE.AUTO, EVC_MODE.SCHEDULED)
    ):
        effective = 0.0
    return max(0.0, min(effective, station_max_current))


def map_alfen_status(client: Any, config: Dict[str, Any]) -> int:
    """Map Alfen status string to raw status code (0=Disconnected, 1=Connected, 2=Charging)."""
    status_regs = read_holding_registers(
        client,
        config["registers"]["status"],
        5,
        config["modbus"]["socket_slave_id"],
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


def handle_low_soc(
    low_soc_enabled: int,
    low_soc_threshold: float,
    low_soc_hysteresis: float,
    low_soc_active: bool,
    read_battery_soc: callable,
) -> bool:
    """Update low_soc_active based on battery SOC with hysteresis."""
    battery_soc_value = read_battery_soc()
    if battery_soc_value is not None and not math.isnan(battery_soc_value):
        battery_soc_value = float(battery_soc_value)
    else:
        battery_soc_value = None

    if low_soc_enabled and battery_soc_value is not None:
        if low_soc_active:
            if battery_soc_value >= (low_soc_threshold + low_soc_hysteresis):
                low_soc_active = False
        else:
            if battery_soc_value <= low_soc_threshold:
                low_soc_active = True
    return low_soc_active


def apply_mode_specific_status(
    current_mode: EVC_MODE,
    connected: bool,
    auto_start: int,
    start_stop: EVC_CHARGE,
    intended_set_current: float,
    low_soc_enabled: int,
    low_soc_active: bool,
    schedule_enabled: int,
    schedule_days_mask: int,
    schedule_start: str,
    schedule_end: str,
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
        if not is_within_schedule(
            schedule_enabled,
            schedule_days_mask,
            schedule_start,
            schedule_end,
            time.time(),
        ):
            new_victron_status = 6

    if low_soc_enabled and low_soc_active and connected:
        new_victron_status = 7  # Low SOC pause

    return new_victron_status


def apply_auto_start(
    now_connected: bool,
    was_disconnected: bool,
    auto_start: int,
    start_stop: EVC_CHARGE,
    current_mode: EVC_MODE,
    intended_set_current: float,
    low_soc_enabled: int,
    low_soc_active: bool,
    station_max_current: float,  # Assuming this is available; add if needed
    schedule_enabled: int,
    schedule_days_mask: int,
    schedule_start: str,
    schedule_end: str,
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
            f"Auto-start triggered: Set StartStop to ENABLED (mode: {current_mode.name})"
        )
        target = compute_effective_current(
            current_mode,
            start_stop,
            intended_set_current,
            low_soc_enabled,
            low_soc_active,
            station_max_current,
            time.time(),
            schedule_enabled,
            schedule_days_mask,
            schedule_start,
            schedule_end,
        )
        if set_current(target, force_verify=True):
            logger.info(f"Auto-start applied current: {target:.2f} A")
    return start_stop


def calculate_session_energy_and_time(
    client: Any,
    config: Dict[str, Any],
    service: Any,
    new_victron_status: int,
    old_victron_status: int,
    charging_start_time: float,
    session_start_energy_kwh: float,
) -> tuple[float, float]:
    """Calculate and update session energy and charging time."""
    energy_regs = read_holding_registers(
        client,
        config["registers"]["energy"],
        4,
        config["modbus"]["socket_slave_id"],
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
    config: Dict[str, Any],
    service: Any,
    current_mode: EVC_MODE,
    start_stop: EVC_CHARGE,
    auto_start: int,
    intended_set_current: float,
    low_soc_enabled: int,
    low_soc_threshold: float,
    low_soc_hysteresis: float,
    low_soc_active: bool,
    schedule_enabled: int,
    schedule_days_mask: int,
    schedule_start: str,
    schedule_end: str,
    station_max_current: float,
    charging_start_time: float,
    session_start_energy_kwh: float,
    set_current: callable,
    persist_config_to_disk: callable,
    read_battery_soc: callable,
    logger: logging.Logger,
) -> tuple[bool, float, float]:
    raw_status = map_alfen_status(client, config)

    old_victron_status = service["/Status"]
    connected = raw_status >= 1
    was_disconnected = old_victron_status == 0
    now_connected = connected

    new_victron_status = raw_status

    low_soc_active = handle_low_soc(
        low_soc_enabled,
        low_soc_threshold,
        low_soc_hysteresis,
        low_soc_active,
        read_battery_soc,
    )

    new_victron_status = apply_mode_specific_status(
        current_mode,
        connected,
        auto_start,
        start_stop,
        intended_set_current,
        low_soc_enabled,
        low_soc_active,
        schedule_enabled,
        schedule_days_mask,
        schedule_start,
        schedule_end,
        new_victron_status,
    )

    if low_soc_active and connected:
        new_victron_status = 7  # Moved here if not in apply_mode_specific_status

    service["/Status"] = new_victron_status

    start_stop = apply_auto_start(
        now_connected,
        was_disconnected,
        auto_start,
        start_stop,
        current_mode,
        intended_set_current,
        low_soc_enabled,
        low_soc_active,
        station_max_current,
        schedule_enabled,
        schedule_days_mask,
        schedule_start,
        schedule_end,
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

    return low_soc_active, charging_start_time, session_start_energy_kwh
