import logging
import math
import time
from typing import Any, Dict

from pymodbus.constants import Endian
from pymodbus.exceptions import ModbusException
from pymodbus.payload import BinaryPayloadBuilder, BinaryPayloadDecoder

from .logic import compute_effective_current
from .modbus_utils import decode_floats, read_holding_registers

CURRENT_TOLERANCE: float = 0.25
CLAMP_EPSILON: float = 1e-6
UPDATE_DIFFERENCE_THRESHOLD: float = 0.1
VERIFICATION_DELAY: float = 0.1
RETRY_DELAY: float = 0.5
MAX_RETRIES: int = 3
WATCHDOG_INTERVAL_SECONDS: int = 30
MAX_SET_CURRENT: float = 64.0


def set_current(
    client: Any,
    config: dict,
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
    target_amps = max(0.0, min(target_amps, station_max_current))
    retries = MAX_RETRIES
    for attempt in range(retries):
        try:
            builder = BinaryPayloadBuilder(byteorder=Endian.BIG, wordorder=Endian.BIG)
            builder.add_32bit_float(float(target_amps))
            payload = builder.to_registers()
            client.write_registers(
                config["registers"]["amps_config"],
                payload,
                slave=config["modbus"]["socket_slave_id"],
            )
            if force_verify:
                time.sleep(VERIFICATION_DELAY)
                regs = read_holding_registers(
                    client,
                    config["registers"]["amps_config"],
                    2,
                    config["modbus"]["socket_slave_id"],
                )
                if len(regs) == 2:
                    dec = BinaryPayloadDecoder.fromRegisters(
                        regs, byteorder=Endian.BIG, wordorder=Endian.BIG
                    ).decode_32bit_float()
                    logging.getLogger("alfen_driver").info(
                        f"SetCurrent write (attempt {attempt+1}): raw={regs}, dec={dec:.3f}"
                    )
                    if math.isclose(dec, float(target_amps), abs_tol=CURRENT_TOLERANCE):
                        return True
                else:
                    logging.getLogger("alfen_driver").warning(
                        f"Verification failed on attempt {attempt+1}"
                    )
            else:
                return True
        except ModbusException as e:
            logging.getLogger("alfen_driver").error(
                f"Modbus error on SetCurrent attempt {attempt+1}: {e}"
            )
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY)
        except ValueError as e:
            logging.getLogger("alfen_driver").error(
                f"Value error on SetCurrent attempt {attempt+1}: {e}"
            )
            return False
    return False


def set_effective_current(
    client: Any,
    config: dict,
    current_mode: Any,
    start_stop: Any,
    intended_set_current: float,
    low_soc_enabled: int,
    low_soc_active: bool,
    station_max_current: float,
    last_sent_current: float,
    last_current_set_time: float,
    schedule_enabled: int,
    schedule_days_mask: int,
    schedule_start: str,
    schedule_end: str,
    logger: logging.Logger,
    force: bool = False,
) -> tuple[float, float]:
    """
    Set the effective current based on mode and watchdog.

    Parameters:
        force: If True, force update regardless of thresholds.
    """
    now = time.time()
    effective_current = compute_effective_current(
        current_mode,
        start_stop,
        intended_set_current,
        low_soc_enabled,
        low_soc_active,
        station_max_current,
        now,
        schedule_enabled,
        schedule_days_mask,
        schedule_start,
        schedule_end,
    )
    if effective_current < 0:
        effective_current = 0.0
    if effective_current > station_max_current:
        effective_current = station_max_current

    current_time = time.time()
    needs_update = (
        force
        or abs(effective_current - last_sent_current) > UPDATE_DIFFERENCE_THRESHOLD
        or (current_time - last_current_set_time > WATCHDOG_INTERVAL_SECONDS)
    )
    if needs_update:
        ok = set_current(
            client, config, effective_current, station_max_current, force_verify=True
        )
        if ok:
            last_current_set_time = current_time
            last_sent_current = effective_current
            logger.info(
                f"Set effective current to {effective_current:.2f} A (mode: {current_mode.name}, intended: {intended_set_current:.2f})"
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
    config: Dict[str, Any],
    service: Any,
    defaults: Dict[str, Any],
    logger: logging.Logger,
) -> float:
    """
    Update the station max current from Modbus with retries.

    Returns:
        The updated station max current (uses fallback on failure).

    References Alfen Modbus spec for register 1100 (Max Current).
    """
    retries = MAX_RETRIES
    for attempt in range(retries):
        try:
            rr_max_c = client.read_holding_registers(
                config["registers"]["station_max_current"],
                2,
                slave=config["modbus"]["station_slave_id"],
            )
            if not rr_max_c.isError():
                max_current = decode_floats(rr_max_c.registers, 1)[0]
                if not math.isnan(max_current) and max_current > 0:
                    station_max_current = float(max_current)
                    service["/MaxCurrent"] = round(station_max_current, 1)
                    return station_max_current
        except ModbusException as e:
            logger.debug(f"Station MaxCurrent read failed: {e}")
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY)
    logger.warning("Failed to read station max current after retries. Using fallback.")
    station_max_current = defaults["station_max_current"]
    service["/MaxCurrent"] = round(station_max_current, 1)
    return station_max_current
