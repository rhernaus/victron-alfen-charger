#!/usr/bin/env python3

import enum
import logging
import math
import os
import sys
import time
import traceback

sys.path.insert(
    1, os.path.join(os.path.dirname(__file__), "/opt/victronenergy/dbus-modbus-client")
)

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from pymodbus.client import ModbusTcpClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadBuilder, BinaryPayloadDecoder
from vedbus import VeDbusService


class EVC_MODE(enum.IntEnum):
    MANUAL = 0
    AUTO = 1
    SCHEDULED = 2


class EVC_CHARGE(enum.IntEnum):
    DISABLED = 0
    ENABLED = 1


# --- Configuration ---
ALFEN_IP = "10.128.0.64"
ALFEN_PORT = 502
SOCKET_SLAVE_ID = 1
STATION_SLAVE_ID = 200
DEVICE_INSTANCE = 0

# --- Modbus Registers ---
REG_VOLTAGES = 306
REG_CURRENTS = 320
REG_POWER = 344
REG_ENERGY = 374
REG_STATUS = 1201
REG_AMPS_CONFIG = 1210
REG_PHASES = 1215
REG_FIRMWARE_VERSION = 123  # Registers 123-139: firmware version ASCII string
REG_FIRMWARE_VERSION_COUNT = 17
REG_STATION_SERIAL = 157  # Registers 157-167: station serial ASCII string
REG_STATION_SERIAL_COUNT = 11
REG_MANUFACTURER = 117
REG_MANUFACTURER_COUNT = 5
REG_PLATFORM_TYPE = 140
REG_PLATFORM_TYPE_COUNT = 17
REG_MAX_CURRENT_APPLIED = 1206


# --- Globals ---
charging_start_time = 0
# NEW: Global for watchdog timer
last_current_set_time = 0
session_start_energy_kwh = 0
intended_set_current = 6.0
current_mode = EVC_MODE.AUTO
start_stop = EVC_CHARGE.DISABLED
auto_start = 1
last_sent_current = -1.0
schedule_enabled = 0
# Bit mask for days: 0=Sun,1=Mon,...,6=Sat
schedule_days_mask = 0
schedule_start = "00:00"  # HH:MM
schedule_end = "00:00"  # HH:MM


def main():
    DBusGMainLoop(set_as_default=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("/var/log/alfen_driver.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("alfen_driver")

    client = ModbusTcpClient(host=ALFEN_IP, port=ALFEN_PORT)
    service_name = f"com.victronenergy.evcharger.alfen_{DEVICE_INSTANCE}"
    service = VeDbusService(service_name, register=False)

    def _parse_hhmm_to_minutes(timestr: str) -> int:
        try:
            parts = timestr.strip().split(":")
            if len(parts) != 2:
                return 0
            hours = int(parts[0]) % 24
            minutes = int(parts[1]) % 60
            return hours * 60 + minutes
        except Exception:
            return 0

    def _is_within_schedule(now: float) -> bool:
        try:
            if schedule_enabled == 0:
                return False
            tm = time.localtime(now)
            weekday = tm.tm_wday  # Mon=0..Sun=6
            # Convert to mask index Sun=0..Sat=6
            sun_based_index = (weekday + 1) % 7
            if (schedule_days_mask & (1 << sun_based_index)) == 0:
                return False
            minutes_now = tm.tm_hour * 60 + tm.tm_min
            start_min = _parse_hhmm_to_minutes(schedule_start)
            end_min = _parse_hhmm_to_minutes(schedule_end)
            if start_min == end_min:
                return False
            if start_min < end_min:
                return start_min <= minutes_now < end_min
            # Overnight window
            return minutes_now >= start_min or minutes_now < end_min
        except Exception:
            return False

    def _write_current_with_verification(target_amps: float) -> bool:
        """Write float32 to REG_AMPS_CONFIG and verify by reading back.

        Tries BIG/BIG first (matching the rest of our map). If the read-back does
        not match within tolerance, tries BIG bytes + LITTLE word order.
        """
        # Single BIG/BIG write + read-back verification
        try:
            builder = BinaryPayloadBuilder(byteorder=Endian.BIG, wordorder=Endian.BIG)
            builder.add_32bit_float(float(target_amps))
            payload = builder.to_registers()
            client.write_registers(REG_AMPS_CONFIG, payload, slave=SOCKET_SLAVE_ID)
            rr = client.read_holding_registers(
                REG_AMPS_CONFIG, 2, slave=SOCKET_SLAVE_ID
            )
            regs = rr.registers if hasattr(rr, "registers") else []
            if len(regs) == 2:
                dec = BinaryPayloadDecoder.fromRegisters(
                    regs, byteorder=Endian.BIG, wordorder=Endian.BIG
                ).decode_32bit_float()
                logger.info("SetCurrent write: raw=%s, dec=%.3f", regs, dec)
                if abs(dec - float(target_amps)) < 0.25:
                    return True
        except Exception as e:
            logger.error("SetCurrent write failed: %s", e)
        return False

    def set_current_callback(path, value):
        global intended_set_current
        try:
            intended_set_current = max(0.0, min(64.0, float(value)))
            service["/SetCurrent"] = round(intended_set_current, 1)
            logger.info(
                "GUI request to set intended current to %.2f A", intended_set_current
            )
            return True
        except Exception as e:
            logger.error("Set current error: %s\n%s", e, traceback.format_exc())
            return False

    def mode_callback(path, value):
        global current_mode
        try:
            current_mode = EVC_MODE(int(value))
            return True
        except ValueError:
            return False

    def startstop_callback(path, value):
        global start_stop
        try:
            start_stop = EVC_CHARGE(int(value))
            return True
        except ValueError:
            return False

    def autostart_callback(path, value):
        global auto_start
        auto_start = int(value)
        return True

    def schedule_enabled_callback(path, value):
        global schedule_enabled
        try:
            schedule_enabled = int(value)
            return True
        except Exception:
            return False

    def schedule_days_callback(path, value):
        global schedule_days_mask
        try:
            schedule_days_mask = int(value) & 0x7F
            return True
        except Exception:
            return False

    def schedule_start_callback(path, value):
        global schedule_start
        try:
            # Basic validation; fallback to previous on bad input
            _ = _parse_hhmm_to_minutes(str(value))
            schedule_start = str(value)
            return True
        except Exception:
            return False

    def schedule_end_callback(path, value):
        global schedule_end
        try:
            _ = _parse_hhmm_to_minutes(str(value))
            schedule_end = str(value)
            return True
        except Exception:
            return False

    # --- D-Bus Path Definitions ---
    service.add_path("/Mgmt/ProcessName", __file__)
    service.add_path("/Mgmt/ProcessVersion", "1.4")
    service.add_path("/Mgmt/Connection", f"Modbus TCP at {ALFEN_IP}")
    service.add_path("/DeviceInstance", DEVICE_INSTANCE)
    service.add_path("/Connected", 0)
    service.add_path("/ProductName", "Alfen EV Charger")
    service.add_path("/ProductId", 0xA142)
    service.add_path("/FirmwareVersion", "N/A")
    service.add_path("/Serial", "ALFEN-001")
    service.add_path("/Status", 0)
    service.add_path(
        "/Mode", current_mode.value, writeable=True, onchangecallback=mode_callback
    )
    service.add_path(
        "/StartStop",
        start_stop.value,
        writeable=True,
        onchangecallback=startstop_callback,
    )
    service.add_path(
        "/SetCurrent",
        intended_set_current,
        writeable=True,
        onchangecallback=set_current_callback,
    )
    service.add_path("/MaxCurrent", 32.0)
    service.add_path(
        "/AutoStart", auto_start, writeable=True, onchangecallback=autostart_callback
    )
    service.add_path("/ChargingTime", 0)
    # EVCS UI expects both "/Current" and "/Ac/Current"; publish both
    service.add_path("/Current", 0.0)
    service.add_path("/Ac/Current", 0.0)
    service.add_path("/Ac/Power", 0.0)
    service.add_path("/Ac/Energy/Forward", 0.0)
    service.add_path("/Ac/PhaseCount", 0)
    service.add_path("/Position", 0, writeable=True)  # 0=AC Output, 1=AC Input
    service.add_path("/Ac/L1/Voltage", 0.0)
    service.add_path("/Ac/L1/Current", 0.0)
    service.add_path("/Ac/L1/Power", 0.0)
    service.add_path("/Ac/L2/Voltage", 0.0)
    service.add_path("/Ac/L2/Current", 0.0)
    service.add_path("/Ac/L2/Power", 0.0)
    service.add_path("/Ac/L3/Voltage", 0.0)
    service.add_path("/Ac/L3/Current", 0.0)
    service.add_path("/Ac/L3/Power", 0.0)

    # Simple schedule configuration
    service.add_path(
        "/Schedule/Enabled",
        schedule_enabled,
        writeable=True,
        onchangecallback=schedule_enabled_callback,
    )
    service.add_path(
        "/Schedule/Days",
        schedule_days_mask,
        writeable=True,
        onchangecallback=schedule_days_callback,
    )
    service.add_path(
        "/Schedule/Start",
        schedule_start,
        writeable=True,
        onchangecallback=schedule_start_callback,
    )
    service.add_path(
        "/Schedule/End",
        schedule_end,
        writeable=True,
        onchangecallback=schedule_end_callback,
    )

    service.register()

    def poll():
        global charging_start_time, last_current_set_time, session_start_energy_kwh
        try:
            if not client.is_socket_open():
                logger.info("Modbus connection is closed. Attempting to reconnect...")
                if not client.connect():
                    logger.error(
                        "Failed to reconnect to Alfen. Will retry on next poll."
                    )
                    service["/Connected"] = 0
                    return True
                logger.info("Modbus connection re-established.")
                # Read firmware version
                try:
                    rr_fw = client.read_holding_registers(
                        REG_FIRMWARE_VERSION,
                        REG_FIRMWARE_VERSION_COUNT,
                        slave=STATION_SLAVE_ID,
                    )
                    fw_regs = rr_fw.registers if hasattr(rr_fw, "registers") else []
                    bytes_fw = []
                    for reg in fw_regs:
                        bytes_fw.append((reg >> 8) & 0xFF)
                        bytes_fw.append(reg & 0xFF)
                    fw_str = "".join(chr(b) for b in bytes_fw).strip("\x00 ")
                    service["/FirmwareVersion"] = fw_str
                except Exception as e:
                    logger.debug("FirmwareVersion read failed: %s", e)
                # Read station serial number
                try:
                    rr_sn = client.read_holding_registers(
                        REG_STATION_SERIAL,
                        REG_STATION_SERIAL_COUNT,
                        slave=STATION_SLAVE_ID,
                    )
                    sn_regs = rr_sn.registers if hasattr(rr_sn, "registers") else []
                    bytes_sn = []
                    for reg in sn_regs:
                        bytes_sn.append((reg >> 8) & 0xFF)
                        bytes_sn.append(reg & 0xFF)
                    sn_str = "".join(chr(b) for b in bytes_sn).strip("\x00 ")
                    service["/Serial"] = sn_str
                except Exception as e:
                    logger.debug("Serial read failed: %s", e)
                # Read manufacturer and platform type to build ProductName
                try:
                    # Read Manufacturer
                    rr_mfg = client.read_holding_registers(
                        REG_MANUFACTURER, REG_MANUFACTURER_COUNT, slave=STATION_SLAVE_ID
                    )
                    mfg_regs = rr_mfg.registers if hasattr(rr_mfg, "registers") else []
                    bytes_mfg = []
                    for reg in mfg_regs:
                        bytes_mfg.append((reg >> 8) & 0xFF)
                        bytes_mfg.append(reg & 0xFF)
                    mfg_str = "".join(chr(b) for b in bytes_mfg).strip("\x00 ")

                    # Read Platform Type
                    rr_pt = client.read_holding_registers(
                        REG_PLATFORM_TYPE,
                        REG_PLATFORM_TYPE_COUNT,
                        slave=STATION_SLAVE_ID,
                    )
                    pt_regs = rr_pt.registers if hasattr(rr_pt, "registers") else []
                    bytes_pt = []
                    for reg in pt_regs:
                        bytes_pt.append((reg >> 8) & 0xFF)
                        bytes_pt.append(reg & 0xFF)
                    pt_str = "".join(chr(b) for b in bytes_pt).strip("\x00 ")

                    service["/ProductName"] = f"{mfg_str} {pt_str}"
                except Exception as e:
                    logger.debug("ProductName creation failed: %s", e)

            # --- The rest of the polling logic ---
            # (This part remains the same, reading status, power, etc.)
            # Alfen exposes status as ASCII in multiple registers (e.g. "0", "1", "2").
            rr_status = client.read_holding_registers(
                REG_STATUS, 5, slave=SOCKET_SLAVE_ID
            )
            if rr_status.isError():
                raise ConnectionError("Modbus error reading status")
            status_str = (
                "".join(
                    [chr((r >> 8) & 0xFF) + chr(r & 0xFF) for r in rr_status.registers]
                )
                .strip("\x00 ")
                .upper()
            )

            # Map Alfen Mode 3 state to Victron EVCS status
            if status_str in ("C2", "D2"):
                raw_status = 2  # Charging
            elif status_str in ("B1", "B2", "C1", "D1"):
                raw_status = 1  # Connected, not charging
            else:  # A, E, F, and others
                raw_status = 0  # Disconnected

            old_victron_status = service["/Status"]

            connected = raw_status >= 1
            charging = raw_status == 2

            new_victron_status = raw_status
            # Manual mode gating => WAIT_START when connected and autostart is disabled and StartStop is disabled
            if (
                current_mode == EVC_MODE.MANUAL
                and connected
                and start_stop == EVC_CHARGE.DISABLED
                and auto_start == 0
            ):
                new_victron_status = 6  # WAIT_START
            # AUTO: show WAIT_SUN when connected but effective setpoint is zero
            if current_mode == EVC_MODE.AUTO and connected:
                if intended_set_current <= 0.1:
                    new_victron_status = 4  # WAIT_SUN
            # SCHEDULED: outside window show WAIT_START
            if current_mode == EVC_MODE.SCHEDULED and connected:
                if not _is_within_schedule(time.time()):
                    new_victron_status = 6  # WAIT_START

            service["/Status"] = new_victron_status

            # Get total energy before checking for session start
            rr_e = client.read_holding_registers(REG_ENERGY, 4, slave=SOCKET_SLAVE_ID)
            total_energy_kwh = (
                BinaryPayloadDecoder.fromRegisters(
                    rr_e.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
                ).decode_64bit_float()
                / 1000.0
            )

            if new_victron_status == 2 and old_victron_status != 2:
                charging_start_time = time.time()
                session_start_energy_kwh = total_energy_kwh
            elif new_victron_status != 2:
                charging_start_time = 0
                session_start_energy_kwh = 0  # Reset on disconnect

            service["/ChargingTime"] = (
                time.time() - charging_start_time if charging_start_time > 0 else 0
            )

            # Calculate and publish session energy
            if session_start_energy_kwh > 0:
                session_energy = total_energy_kwh - session_start_energy_kwh
                service["/Ac/Energy/Forward"] = round(
                    session_energy if not math.isnan(session_energy) else 0, 3
                )
            else:
                service["/Ac/Energy/Forward"] = 0.0

            # Read and update MaxCurrent
            try:
                rr_max_c = client.read_holding_registers(
                    REG_MAX_CURRENT_APPLIED, 2, slave=SOCKET_SLAVE_ID
                )
                if not rr_max_c.isError():
                    max_current = BinaryPayloadDecoder.fromRegisters(
                        rr_max_c.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
                    ).decode_32bit_float()
                    service["/MaxCurrent"] = round(
                        max_current if not math.isnan(max_current) else 0, 1
                    )
            except Exception as e:
                logger.debug(f"MaxCurrent read failed: {e}")

            # (Voltage, Current, Power reading logic is unchanged)
            rr_v = client.read_holding_registers(REG_VOLTAGES, 6, slave=SOCKET_SLAVE_ID)
            decoder_v = BinaryPayloadDecoder.fromRegisters(
                rr_v.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            v1, v2, v3 = (
                decoder_v.decode_32bit_float(),
                decoder_v.decode_32bit_float(),
                decoder_v.decode_32bit_float(),
            )
            service["/Ac/L1/Voltage"] = round(v1 if not math.isnan(v1) else 0, 2)
            service["/Ac/L2/Voltage"] = round(v2 if not math.isnan(v2) else 0, 2)
            service["/Ac/L3/Voltage"] = round(v3 if not math.isnan(v3) else 0, 2)
            rr_c = client.read_holding_registers(REG_CURRENTS, 6, slave=SOCKET_SLAVE_ID)
            decoder_c = BinaryPayloadDecoder.fromRegisters(
                rr_c.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            i1, i2, i3 = (
                decoder_c.decode_32bit_float(),
                decoder_c.decode_32bit_float(),
                decoder_c.decode_32bit_float(),
            )
            service["/Ac/L1/Current"] = round(i1 if not math.isnan(i1) else 0, 2)
            service["/Ac/L2/Current"] = round(i2 if not math.isnan(i2) else 0, 2)
            service["/Ac/L3/Current"] = round(i3 if not math.isnan(i3) else 0, 2)
            current_a = round(max(i1, i2, i3), 2)
            service["/Ac/Current"] = current_a
            service["/Current"] = current_a
            service["/Ac/L1/Power"] = round(v1 * i1, 2)
            service["/Ac/L2/Power"] = round(v2 * i2, 2)
            service["/Ac/L3/Power"] = round(v3 * i3, 2)
            rr_p = client.read_holding_registers(REG_POWER, 2, slave=SOCKET_SLAVE_ID)
            power = BinaryPayloadDecoder.fromRegisters(
                rr_p.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            ).decode_32bit_float()
            service["/Ac/Power"] = round(power if not math.isnan(power) else 0)
            rr_ph = client.read_holding_registers(REG_PHASES, 1, slave=SOCKET_SLAVE_ID)
            service["/Ac/PhaseCount"] = rr_ph.registers[0]

            # Compute effective current
            effective_current = 0.0
            if connected:
                if current_mode == EVC_MODE.MANUAL:
                    if start_stop == EVC_CHARGE.ENABLED or auto_start == 1:
                        effective_current = intended_set_current
                    else:
                        effective_current = 0.0
                elif current_mode == EVC_MODE.AUTO:
                    # In AUTO, we pass through intended_set_current (expected to be managed by GX/EMS)
                    effective_current = intended_set_current
                elif current_mode == EVC_MODE.SCHEDULED:
                    if _is_within_schedule(time.time()):
                        effective_current = intended_set_current
                    else:
                        effective_current = 0.0

            # Write if changed or watchdog
            current_time = time.time()
            if abs(effective_current - last_sent_current) > 0.1 or (
                charging and current_time - last_current_set_time > 60
            ):
                ok = _write_current_with_verification(effective_current)
                if ok:
                    last_current_set_time = current_time
                    last_sent_current = effective_current
                    logger.info(
                        "Set effective current to %.2f A (mode: %s, intended: %.2f)",
                        effective_current,
                        current_mode.name,
                        intended_set_current,
                    )

            service["/Connected"] = 1
            logger.debug("Poll completed successfully")

        except Exception as e:
            logger.error(f"Poll error: {e}. The connection will be retried.")
            client.close()
            service["/Connected"] = 0
        return True

    GLib.timeout_add(1000, poll)  # Poll every 1 second
    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == "__main__":
    main()
