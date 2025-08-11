import logging
import math
import time
from typing import Any

from pymodbus.constants import Endian
from pymodbus.exceptions import ModbusException
from pymodbus.payload import BinaryPayloadBuilder, BinaryPayloadDecoder

from .config import Config, DefaultsConfig, ScheduleItem
from .dbus_utils import EVC_MODE
from .logic import compute_effective_current
from .modbus_utils import decode_floats, read_holding_registers, retry_modbus_operation

CLAMP_EPSILON = 0.01  # Tolerance for clamping comparison


def clamp_value(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(value, max_val))


def set_current(
    client: Any,
    config: Config,
    target_amps: float,
    station_max_current: float,
    force_verify: bool = False,
) -> bool:
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
    target_amps = clamp_value(target_amps, 0.0, station_max_current)

    def write_op():
        builder = BinaryPayloadBuilder(byteorder=Endian.BIG, wordorder=Endian.BIG)
        builder.add_32bit_float(float(target_amps))
        payload = builder.to_registers()
        client.write_registers(
            config.registers.amps_config,
            payload,
            slave=config.modbus.socket_slave_id,
        )
        if force_verify:
            time.sleep(config.controls.verification_delay)
            regs = read_holding_registers(
                client,
                config.registers.amps_config,
                2,
                config.modbus.socket_slave_id,
            )
            if len(regs) == 2:
                dec = BinaryPayloadDecoder.fromRegisters(
                    regs, byteorder=Endian.BIG, wordorder=Endian.BIG
                ).decode_32bit_float()
                if math.isclose(
                    dec, float(target_amps), abs_tol=config.controls.current_tolerance
                ):
                    return True
            return False
        return True

    try:
        return retry_modbus_operation(
            write_op,
            retries=config.controls.max_retries,
            retry_delay=config.controls.retry_delay,
        )
    except ModbusException:
        return False


def set_effective_current(
    client: Any,
    config: Config,
    current_mode: Any,
    start_stop: Any,
    intended_set_current: float,
    station_max_current: float,
    last_sent_current: float,
    last_current_set_time: float,
    schedules: list[ScheduleItem],
    logger: logging.Logger,
    ev_power: float = 0.0,  # New parameter for local EV power
    force: bool = False,
) -> tuple[float, float]:
    """
    Set the effective current based on mode and watchdog.

    Parameters:
        force: If True, force update regardless of thresholds.
        ev_power: Total power of the EV charger for excess calculation.
    """
    now = time.time()
    effective_current, explanation = compute_effective_current(
        current_mode,
        start_stop,
        intended_set_current,
        station_max_current,
        now,
        schedules,
        ev_power,  # Pass to compute
    )
    current_time = time.time()
    needs_update = (
        force
        or abs(effective_current - last_sent_current)
        > config.controls.update_difference_threshold
        or (
            current_time - last_current_set_time
            > config.controls.watchdog_interval_seconds
        )
    )
    if needs_update:
        ok = set_current(
            client, config, effective_current, station_max_current, force_verify=True
        )
        if ok:
            last_current_set_time = current_time
            last_sent_current = effective_current
            mode_name = EVC_MODE(current_mode).name
            log_message = (
                f"Set effective current to {effective_current:.2f} A (mode: {mode_name}"
            )
            if current_mode == EVC_MODE.MANUAL:
                log_message += f", intended: {intended_set_current:.2f}"
            log_message += f"). Calculation: {explanation}"
            logger.info(log_message)
    else:
        logger.debug(
            f"No update needed for effective current (current: {last_sent_current:.2f}A, proposed: {effective_current:.2f}A). Calculation: {explanation}"
        )
    return last_sent_current, last_current_set_time


def clamp_intended_current_to_max(
    intended_set_current: float,
    station_max_current: float,
    service: Any,
    persist_config_to_disk: callable,
    logger: logging.Logger,
) -> float:
    """Clamp the intended set current to the station max in MANUAL mode."""
    max_allowed = max(0.0, float(station_max_current))
    if intended_set_current > max_allowed + CLAMP_EPSILON:
        intended_set_current = max_allowed
        service["/SetCurrent"] = round(intended_set_current, 1)
        persist_config_to_disk()
        logger.info(
            f"Clamped DBus /SetCurrent to station max: {intended_set_current:.1f} A (MANUAL mode)"
        )
    return intended_set_current


def update_station_max_current(
    client: Any,
    config: Config,
    service: Any,
    defaults: DefaultsConfig,
    logger: logging.Logger,
) -> float:
    """
    Update the station max current from Modbus with retries.

    Returns:
        The updated station max current (uses fallback on failure).

    References Alfen Modbus spec for register 1100 (Max Current).
    """

    def read_op():
        rr_max_c = client.read_holding_registers(
            config.registers.station_max_current,
            2,
            slave=config.modbus.station_slave_id,
        )
        if not rr_max_c.isError():
            max_current = decode_floats(rr_max_c.registers, 1)[0]
            if not math.isnan(max_current) and max_current > 0:
                station_max_current = float(max_current)
                service["/MaxCurrent"] = round(station_max_current, 1)
                return station_max_current
        raise ModbusException("Read failed")

    try:
        return retry_modbus_operation(
            read_op,
            retries=config.controls.max_retries,
            retry_delay=config.controls.retry_delay,
            logger=logger,
        )
    except ModbusException:
        logger.warning(
            "Failed to read station max current after retries. Using fallback."
        )
        station_max_current = defaults.station_max_current
        service["/MaxCurrent"] = round(station_max_current, 1)
        return station_max_current
