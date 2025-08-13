import logging
import math
import time
from datetime import datetime
from enum import Enum
from typing import Any, Callable, List, Tuple

import dbus
import pytz

from .config import Config, ScheduleItem, parse_hhmm_to_minutes
from .constants import ChargingLimits, ModbusRegisters
from .dbus_utils import EVC_CHARGE, EVC_MODE, EVC_STATUS
from .exceptions import StatusMappingError
from .logging_utils import get_logger
from .modbus_utils import decode_64bit_float, read_holding_registers, read_uint16

MIN_CHARGING_CURRENT: float = 0.1

NOMINAL_VOLTAGE = ChargingLimits.NOMINAL_VOLTAGE
MIN_CURRENT = ChargingLimits.MIN_CURRENT

_config = None  # Module-level cache


def set_config(config: Config) -> None:
    """Set the module-level config for use in Tibber integration."""
    global _config
    _config = config


# Schedule check cache to reduce excessive logging
_schedule_cache = {
    "last_check_time": 0,
    "last_result": False,
    "last_log_time": 0,
}
SCHEDULE_LOG_INTERVAL = 60  # Only log schedule checks every 60 seconds


class AlfenStatus(Enum):
    """Alfen charger status codes."""

    A = "A"  # Disconnected
    B1 = "B1"  # Connected
    B2 = "B2"  # Connected
    C1 = "C1"  # Connected
    C2 = "C2"  # Charging
    D1 = "D1"  # Connected
    D2 = "D2"  # Charging
    E = "E"  # Disconnected
    F = "F"  # Fault

    @property
    def is_disconnected(self) -> bool:
        """Check if status represents disconnected state."""
        return self in (AlfenStatus.A, AlfenStatus.E)

    @property
    def is_connected(self) -> bool:
        """Check if status represents connected state."""
        return self in (AlfenStatus.B1, AlfenStatus.B2, AlfenStatus.C1, AlfenStatus.D1)

    @property
    def is_charging(self) -> bool:
        """Check if status represents charging state."""
        return self in (AlfenStatus.C2, AlfenStatus.D2)

    @property
    def is_fault(self) -> bool:
        """Check if status represents fault state."""
        return self == AlfenStatus.F

    def to_victron_status(self) -> int:
        """Convert to Victron status code (0=Disconnected, 1=Connected, 2=Charging)."""
        if self.is_charging:
            return 2
        elif self.is_connected:
            return 1
        else:  # Disconnected or Fault
            return 0


def clamp_value(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(value, max_val))


def is_within_any_schedule(
    schedules: List[ScheduleItem],
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

    # Throttle schedule checking logs to reduce spam
    logger = get_logger("alfen_driver.logic")
    should_log = (now - _schedule_cache["last_log_time"]) >= SCHEDULE_LOG_INTERVAL

    if should_log:
        logger.debug(
            "Checking charging schedules",
            extra={
                "structured_data": {
                    "local_time": local_dt.strftime("%H:%M %A"),
                    "minutes_now": minutes_now,
                    "day_index": sun_based_index,
                    "timezone": timezone,
                    "total_schedules": len(schedules),
                }
            },
        )
        _schedule_cache["last_log_time"] = int(now)
    for idx, item in enumerate(schedules):
        if item.enabled == 0:
            if should_log:
                logger.debug(f"Schedule {idx + 1} skipped: disabled")
            continue
        mask_check = (item.days_mask & (1 << sun_based_index)) != 0
        if not mask_check:
            if should_log:
                logger.debug(
                    f"Schedule {idx + 1} skipped: day not matched "
                    f"(mask={item.days_mask}, required bit={1 << sun_based_index})"
                )
            continue
        start_min = parse_hhmm_to_minutes(item.start)
        end_min = parse_hhmm_to_minutes(item.end)
        if start_min == end_min:
            if should_log:
                logger.debug(f"Schedule {idx + 1} skipped: start == end ({start_min})")
            continue
        is_overnight = start_min >= end_min
        condition = (
            (start_min <= minutes_now < end_min)
            if not is_overnight
            else (minutes_now >= start_min or minutes_now < end_min)
        )
        if should_log:
            logger.debug(
                f"Schedule {idx + 1}: start_min={start_min}, end_min={end_min}, "
                f"overnight={is_overnight}, condition={condition}"
            )
        if condition:
            if should_log:
                logger.debug(f"Schedule {idx + 1} matched, returning True")
            return True
    if should_log:
        logger.debug("No schedules matched, returning False")
    return False


def get_excess_solar_current(
    ev_power: float = 0.0,
    station_max: float = float("inf"),
    insufficient_solar_start: float = 0.0,
    min_charge_duration_seconds: int = 300,
    active_phases: int = 3,
    min_battery_soc: float = 0.0,
) -> Tuple[float, str, float, bool]:
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
        battery_soc = all_values.get("Dc/Battery/Soc", 100.0)

        # Check if battery SOC is too low
        low_soc = battery_soc < min_battery_soc

        # Only subtract battery discharge (negative power) from excess
        # Ignore battery charging (positive) - it will adapt to available solar
        battery_discharge = abs(min(0.0, battery_power))
        excess = max(0.0, total_pv - adjusted_consumption - battery_discharge)

        # If battery SOC too low, set excess to 0
        if low_soc:
            excess = 0.0
        # Calculate current based on active phases
        current = excess / (active_phases * NOMINAL_VOLTAGE)
        min_power_phases = MIN_CURRENT * active_phases * NOMINAL_VOLTAGE
        clamp_reason = ""

        # If insufficient power for minimum based on active phases, set to 0
        if excess < min_power_phases:
            current = 0.0

        clamped_current = min(current, station_max)
        if clamped_current < MIN_CURRENT and clamped_current > 0:
            clamped_current = 0.0
            clamp_reason = f" (below min {MIN_CURRENT}A, set to 0)"

        explanation = (
            f"total_pv={total_pv:.2f}W, "
            f"adjusted_consumption={adjusted_consumption:.2f}W "
            f"(consumption={consumption:.2f}W - ev_power={ev_power:.2f}W), "
            f"battery_power={battery_power:.2f}W "
            f"(discharge={battery_discharge:.2f}W subtracted), "
            f"battery_soc={battery_soc:.1f}% (min={min_battery_soc:.1f}%), "
            f"excess={excess:.2f}W, "
            f"raw_current={current:.2f}A{clamp_reason} -> "
            f"{clamped_current:.2f}A ({active_phases}-phase)"
        )

        if low_soc:
            explanation += f" (LOW SOC: {battery_soc:.1f}% < {min_battery_soc:.1f}%)"

        # Handle insufficient solar timer
        new_insufficient_start = insufficient_solar_start
        if clamped_current == 0.0 and ev_power > 0:  # Currently charging but no excess
            if insufficient_solar_start == 0:
                # Just started having insufficient solar
                new_insufficient_start = time.time()
                clamped_current = MIN_CURRENT
                explanation += f" (starting {min_charge_duration_seconds}s timer)"
            else:
                # Check if timer expired
                time_insufficient = time.time() - insufficient_solar_start
                if time_insufficient < min_charge_duration_seconds:
                    # Keep charging
                    clamped_current = MIN_CURRENT
                    remaining = min_charge_duration_seconds - time_insufficient
                    explanation += f" (timer: {remaining:.0f}s remaining)"
                else:
                    # Timer expired, stop charging
                    explanation += " (timer expired, stopping)"
        elif clamped_current >= MIN_CURRENT and insufficient_solar_start > 0:
            # Sufficient solar again, reset timer
            new_insufficient_start = 0
            explanation += " (sufficient solar, timer reset)"

        return clamped_current, explanation, new_insufficient_start, low_soc
    except Exception as e:
        logging.error(f"Error calculating excess solar: {e}")
        return 0.0, f"Error: {str(e)}", insufficient_solar_start, False


def compute_effective_current(
    current_mode: EVC_MODE,
    start_stop: EVC_CHARGE,
    intended_set_current: float,
    station_max_current: float,
    now: float,
    schedules: List[ScheduleItem],
    ev_power: float = 0.0,
    timezone: str = "UTC",
    insufficient_solar_start: float = 0.0,
    min_charge_duration_seconds: int = 300,
    active_phases: int = 3,
    min_battery_soc: float = 0.0,
) -> Tuple[float, str, float, bool]:
    effective = 0.0
    explanation = ""
    new_insufficient_start = insufficient_solar_start
    low_soc = False

    if current_mode == EVC_MODE.MANUAL:
        if start_stop == EVC_CHARGE.ENABLED:
            effective = intended_set_current
            state = "enabled"
        else:
            effective = 0.0
            state = "disabled"
        explanation = (
            f"Manual mode {state}, intended_current={intended_set_current:.2f}A "
            f"-> {effective:.2f}A"
        )
    elif current_mode == EVC_MODE.AUTO:
        if start_stop == EVC_CHARGE.DISABLED:
            effective = 0.0
            explanation = "Auto mode disabled by start_stop"
        else:
            (
                effective,
                excess_exp,
                new_insufficient_start,
                low_soc,
            ) = get_excess_solar_current(
                ev_power,
                station_max_current,
                insufficient_solar_start,
                min_charge_duration_seconds,
                active_phases,
                min_battery_soc,
            )
            explanation = f"Auto mode excess solar: {excess_exp}"
    elif current_mode == EVC_MODE.SCHEDULED:
        if start_stop == EVC_CHARGE.DISABLED:
            effective = 0.0
            explanation = "Scheduled mode disabled by start_stop"
        else:
            # Check if we should use Tibber or legacy schedules
            global _config
            if _config and hasattr(_config, "tibber") and _config.tibber.enabled:
                # Use Tibber API for dynamic pricing
                from .tibber import check_tibber_schedule

                should_charge, tibber_explanation = check_tibber_schedule(
                    _config.tibber
                )
                effective = station_max_current if should_charge else 0.0
                explanation = (
                    f"Scheduled mode (Tibber): {tibber_explanation}, "
                    f"set to {effective:.2f}A"
                )
            else:
                # Use legacy time-based schedules
                utc_dt = datetime.utcfromtimestamp(now)
                local_tz = pytz.timezone(timezone)
                local_dt = utc_dt.replace(tzinfo=pytz.utc).astimezone(local_tz)
                local_time_str = local_dt.strftime("%H:%M")
                day_str = local_dt.strftime("%A")
                within = is_within_any_schedule(schedules, now, timezone)
                effective = station_max_current if within else 0.0
                status = "within" if within else "not within"
                explanation = (
                    f"Scheduled mode: {status} schedule "
                    f"(local time: {local_time_str} on {day_str}, "
                    f"timezone: {timezone}), set to {effective:.2f}A"
                )
    clamped_effective = max(0.0, min(effective, station_max_current))
    if not math.isclose(clamped_effective, effective, abs_tol=0.01):
        explanation += f" (clamped from {effective:.2f}A to {clamped_effective:.2f}A)"
    return clamped_effective, explanation, new_insufficient_start, low_soc


def read_active_phases(client: Any, config: Config) -> int:
    """Read the number of active phases from the charger.

    Returns:
        Number of active phases (1 or 3). Defaults to 3 if read fails.
    """
    try:
        phases = read_uint16(
            client,
            ModbusRegisters.ACTIVE_PHASES,
            config.modbus.socket_slave_id,
        )
        # Alfen only supports 1 or 3 phase charging
        if phases == 1:
            return 1
        elif phases in (2, 3):
            # 2-phase gets treated as 3-phase
            return 3
        else:
            logging.getLogger("alfen_driver.logic").warning(
                f"Invalid phase count {phases}, defaulting to 3"
            )
            return 3
    except Exception as e:
        logging.getLogger("alfen_driver.logic").debug(
            f"Could not read active phases: {e}, defaulting to 3"
        )
        return 3


def map_alfen_status(client: Any, config: Config) -> int:
    """Map Alfen status string to raw status code.

    Returns:
        0=Disconnected, 1=Connected, 2=Charging
    """
    try:
        # Read Mode 3 state from socket (slave ID 1, register 1201)
        status_regs = read_holding_registers(
            client,
            ModbusRegisters.SOCKET_MODE3_STATE,
            5,  # 5 registers for the state string
            config.modbus.socket_slave_id,  # Slave ID 1
        )
        status_str = (
            "".join([chr((r >> 8) & 0xFF) + chr(r & 0xFF) for r in status_regs])
            .strip("\x00 ")
            .upper()
        )

        # Handle empty status
        if status_str == "":
            logging.getLogger("alfen_driver.logic").warning(
                "Empty status string received, assuming disconnected"
            )
            return 0

        # Try to map to AlfenStatus enum
        try:
            alfen_status = AlfenStatus(status_str)
            return alfen_status.to_victron_status()
        except ValueError:
            # Unknown status code
            logging.getLogger("alfen_driver.logic").warning(
                f"Unknown Alfen status '{status_str}', assuming disconnected"
            )
            return 0  # Disconnected for unknown states
    except Exception as e:
        logging.getLogger("alfen_driver.logic").error(
            f"Failed to map Alfen status: {e}"
        )
        raise StatusMappingError(f"Failed to read status registers: {e}") from e


def apply_mode_specific_status(
    current_mode: EVC_MODE,
    connected: bool,
    start_stop: EVC_CHARGE,
    intended_set_current: float,
    schedules: List[ScheduleItem],
    new_victron_status: int,
    timezone: str,
    effective_current: float = 0.0,  # Add param for effective
) -> int:
    """Adjust Victron status based on mode, schedule, and low SOC."""
    if (
        current_mode == EVC_MODE.MANUAL
        and connected
        and start_stop == EVC_CHARGE.DISABLED
    ):
        new_victron_status = EVC_STATUS.WAIT_START
    if current_mode == EVC_MODE.AUTO and connected:
        if start_stop == EVC_CHARGE.DISABLED:
            new_victron_status = EVC_STATUS.WAIT_START
        elif effective_current < MIN_CURRENT:
            new_victron_status = EVC_STATUS.WAIT_SUN  # Not enough excess solar
    # Cache schedule check result to avoid duplicate calls
    within_schedule = None
    if current_mode == EVC_MODE.SCHEDULED:
        within_schedule = is_within_any_schedule(schedules, time.time(), timezone)

    if current_mode == EVC_MODE.SCHEDULED and connected:
        if not within_schedule:
            new_victron_status = EVC_STATUS.WAIT_START

    # General: If charging disabled but connected, wait for start
    charging_disabled = start_stop == EVC_CHARGE.DISABLED or (
        current_mode == EVC_MODE.SCHEDULED and not within_schedule
    )
    if connected and charging_disabled and new_victron_status == EVC_STATUS.CONNECTED:
        new_victron_status = EVC_STATUS.WAIT_START

    return new_victron_status


def calculate_session_energy_and_time(
    client: Any,
    config: Config,
    service: Any,
    new_victron_status: int,
    old_victron_status: int,
    charging_start_time: float,
    session_start_energy_kwh: float,
    last_charging_time: float,
    last_session_energy: float,
    persist_config_to_disk: Callable[[], None],
    raw_status: int,  # Add param
    effective_current: float,  # Add param
    current_mode: EVC_MODE,  # Add param
    logger: Any,
) -> tuple[float, float, float, float]:
    from .dbus_utils import EVC_STATUS

    energy_regs = read_holding_registers(
        client,
        ModbusRegisters.METER_ACTIVE_ENERGY_TOTAL,
        4,
        config.modbus.socket_slave_id,
    )
    total_energy_kwh = decode_64bit_float(energy_regs) / 1000.0

    # Define active session states: CHARGING and WAIT_SUN
    is_active_session = new_victron_status in (EVC_STATUS.CHARGING, EVC_STATUS.WAIT_SUN)
    was_active_session = old_victron_status in (
        EVC_STATUS.CHARGING,
        EVC_STATUS.WAIT_SUN,
    )

    if is_active_session and not was_active_session:
        # New session start: reset to 0
        charging_start_time = time.time()
        session_start_energy_kwh = total_energy_kwh
        service["/ChargingTime"] = 0
        service["/Ac/Energy/Forward"] = 0.0
        persist_config_to_disk()
        logger.info(
            "New charging session started (status changed to "
            f"{EVC_STATUS(new_victron_status).name})"
        )
    elif is_active_session:
        # Continuing session (could be CHARGING or WAIT_SUN)
        service["/ChargingTime"] = time.time() - charging_start_time
        session_energy = total_energy_kwh - session_start_energy_kwh
        service["/Ac/Energy/Forward"] = round(session_energy, 3)
    elif not is_active_session and was_active_session:
        # Session stop: calculate finals, persist, reset starts
        last_charging_time = time.time() - charging_start_time
        session_energy = total_energy_kwh - session_start_energy_kwh
        last_session_energy = round(session_energy, 3)
        service["/ChargingTime"] = last_charging_time
        service["/Ac/Energy/Forward"] = last_session_energy
        charging_start_time = 0
        session_start_energy_kwh = 0
        persist_config_to_disk()

        # Detect charged: Transition from charging to connected while
        # effective current was sufficient
        if raw_status == EVC_STATUS.CONNECTED:
            if effective_current >= MIN_CURRENT:
                new_status = EVC_STATUS.CHARGED
            elif current_mode == EVC_MODE.AUTO:
                new_status = EVC_STATUS.WAIT_SUN  # Tie-in with WAIT_SUN
            else:
                new_status = EVC_STATUS.CONNECTED  # Fallback
            if new_status != new_victron_status:
                service["/Status"] = new_status
                logger.info(
                    f"Status changed to {EVC_STATUS(new_status).name} "
                    "after session finish"
                )
        logger.info(
            f"Session finished: Set status to {service['/Status']} "
            f"(energy: {last_session_energy:.3f} kWh, "
            f"time: {last_charging_time:.0f}s)"
        )

    else:
        # Not charging, no transition: show last values
        service["/ChargingTime"] = last_charging_time
        service["/Ac/Energy/Forward"] = last_session_energy
    return (
        charging_start_time,
        session_start_energy_kwh,
        last_charging_time,
        last_session_energy,
    )


def process_status_and_energy(
    client: Any,
    config: Config,
    service: Any,
    current_mode: EVC_MODE,
    start_stop: EVC_CHARGE,
    intended_set_current: float,
    schedules: List[ScheduleItem],
    station_max_current: float,
    charging_start_time: float,
    session_start_energy_kwh: float,
    last_charging_time: float,
    last_session_energy: float,
    set_current: Callable[[float, bool], bool],
    persist_config_to_disk: Callable[[], None],
    logger: Any,
    timezone: str,
    insufficient_solar_start: float = 0.0,
    active_phases: int = 3,
    min_battery_soc: float = 0.0,
) -> Tuple[float, float, float, float, bool, float]:
    raw_status = map_alfen_status(client, config)

    old_victron_status = service["/Status"]
    connected = raw_status >= 1
    was_disconnected = old_victron_status == 0
    now_connected = connected

    # Read current EV power from service
    ev_power = service.get("/Ac/Power", 0.0)

    # Compute effective early to inform status
    (
        effective_current,
        explanation,
        new_insufficient_start,
        low_soc,
    ) = compute_effective_current(
        current_mode,
        start_stop,
        intended_set_current,
        station_max_current,
        time.time(),
        schedules,
        ev_power,
        timezone,
        insufficient_solar_start,
        config.controls.min_charge_duration_seconds,
        active_phases,
        min_battery_soc,
    )

    new_victron_status = raw_status

    # Apply LOW_SOC status if battery SOC is too low
    if low_soc and connected and current_mode == EVC_MODE.AUTO:
        new_victron_status = EVC_STATUS.LOW_SOC

    new_victron_status = apply_mode_specific_status(
        current_mode,
        connected,
        start_stop,
        intended_set_current,
        schedules,
        new_victron_status,
        timezone,
        effective_current,  # Pass effective
    )

    service["/Status"] = new_victron_status

    if new_victron_status != old_victron_status:
        logger.info(
            f"Status changed from {EVC_STATUS(old_victron_status).name} "
            f"to {EVC_STATUS(new_victron_status).name}"
        )

    old_victron_status = new_victron_status  # Update for re-evaluation check

    # Re-evaluate status after potential auto-start and current set
    new_victron_status = apply_mode_specific_status(
        current_mode,
        connected,
        start_stop,
        intended_set_current,
        schedules,
        service["/Status"],
        timezone,
        effective_current,  # Pass again
    )
    service["/Status"] = new_victron_status

    if new_victron_status != old_victron_status:
        logger.info(
            f"Status changed from {EVC_STATUS(old_victron_status).name} "
            f"to {EVC_STATUS(new_victron_status).name}"
        )

    (
        charging_start_time,
        session_start_energy_kwh,
        last_charging_time,
        last_session_energy,
    ) = calculate_session_energy_and_time(
        client,
        config,
        service,
        new_victron_status,
        old_victron_status,
        charging_start_time,
        session_start_energy_kwh,
        last_charging_time,
        last_session_energy,
        persist_config_to_disk,
        raw_status,  # Pass new params
        effective_current,
        current_mode,
        logger,
    )
    return (
        charging_start_time,
        session_start_energy_kwh,
        last_charging_time,
        last_session_energy,
        (now_connected and was_disconnected),
        new_insufficient_start,
    )
