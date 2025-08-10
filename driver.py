#!/usr/bin/env python3

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

# --- Configuration ---
ALFEN_IP = "10.128.0.64"
ALFEN_PORT = 502
ALFEN_SLAVE_ID = 1
DEVICE_INSTANCE = 0

# --- Modbus Registers ---
REG_VOLTAGES = 306
REG_CURRENTS = 320
REG_POWER = 344
REG_ENERGY = 374
REG_STATUS = 1201
REG_AMPS_CONFIG = 1210
REG_PHASES = 1215

# --- Globals ---
charging_start_time = 0
# NEW: Global for watchdog timer
last_current_set_time = 0


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
            client.write_registers(REG_AMPS_CONFIG, payload, slave=ALFEN_SLAVE_ID)
            rr = client.read_holding_registers(REG_AMPS_CONFIG, 2, slave=ALFEN_SLAVE_ID)
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
        global last_current_set_time
        try:
            # Clamp within sensible bounds (6..32 A typical for AC per phase)
            target = max(0.0, min(64.0, float(value)))
            logger.info("GUI request to set current to %.2f A", target)
            ok = _write_current_with_verification(target)
            if ok:
                logger.info("Successfully set current setpoint to %.2f A.", target)
                last_current_set_time = time.time()
                return True
            logger.error("Failed to verify current setpoint write")
            return False
        except Exception as e:
            logger.error("Set current error: %s\n%s", e, traceback.format_exc())
            return False

    # --- D-Bus Path Definitions ---
    service.add_path("/Mgmt/ProcessName", __file__)
    service.add_path("/Mgmt/ProcessVersion", "1.4")
    service.add_path("/Mgmt/Connection", f"Modbus TCP at {ALFEN_IP}")
    service.add_path("/DeviceInstance", DEVICE_INSTANCE)
    service.add_path("/Connected", 0)
    service.add_path("/ProductName", "Alfen Eve Pro Line")
    service.add_path("/ProductId", 0xA142)
    service.add_path("/FirmwareVersion", "N/A")
    service.add_path("/Serial", "ALFEN-001")
    service.add_path("/Status", 0)
    service.add_path("/Mode", 1, writeable=True)
    service.add_path(
        "/SetCurrent", 0.0, writeable=True, onchangecallback=set_current_callback
    )
    service.add_path("/MaxCurrent", 32.0)
    service.add_path("/Enable", 1, writeable=True)
    service.add_path("/ChargingTime", 0)
    # EVCS UI expects both "/Current" and "/Ac/Current"; publish both
    service.add_path("/Current", 0.0)
    service.add_path("/Ac/Current", 0.0)
    service.add_path("/Ac/Power", 0.0)
    service.add_path("/Ac/Energy/Forward", 0.0)
    service.add_path("/Ac/PhaseCount", 0)
    service.add_path("/Ac/L1/Voltage", 0.0)
    service.add_path("/Ac/L1/Current", 0.0)
    service.add_path("/Ac/L1/Power", 0.0)
    service.add_path("/Ac/L2/Voltage", 0.0)
    service.add_path("/Ac/L2/Current", 0.0)
    service.add_path("/Ac/L2/Power", 0.0)
    service.add_path("/Ac/L3/Voltage", 0.0)
    service.add_path("/Ac/L3/Current", 0.0)
    service.add_path("/Ac/L3/Power", 0.0)

    service.register()

    def poll():
        global charging_start_time, last_current_set_time
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

            # --- The rest of the polling logic ---
            # (This part remains the same, reading status, power, etc.)
            # Alfen exposes status as ASCII in multiple registers (e.g. "0", "1", "2").
            rr_status = client.read_holding_registers(
                REG_STATUS, 5, slave=ALFEN_SLAVE_ID
            )
            if rr_status.isError():
                raise ConnectionError("Modbus error reading status")
            status_str = "".join(chr(r & 0xFF) for r in rr_status.registers).strip(
                "\x00 "
            )
            # Map Alfen values to Victron EVCS status: 0=disconnected, 1=connected, 2=charging
            try:
                status_code = int(status_str[:1]) if status_str else 0
            except Exception:
                status_code = 0
            if status_code >= 2:
                new_victron_status = 2
            elif status_code == 1:
                new_victron_status = 1
            else:
                new_victron_status = 0
            old_victron_status = service["/Status"]
            service["/Status"] = new_victron_status
            if new_victron_status == 2 and old_victron_status != 2:
                charging_start_time = time.time()
            elif new_victron_status != 2:
                charging_start_time = 0
            service["/ChargingTime"] = (
                time.time() - charging_start_time if charging_start_time > 0 else 0
            )

            # (Voltage, Current, Power reading logic is unchanged)
            rr_v = client.read_holding_registers(REG_VOLTAGES, 6, slave=ALFEN_SLAVE_ID)
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
            rr_c = client.read_holding_registers(REG_CURRENTS, 6, slave=ALFEN_SLAVE_ID)
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
            service["/Ac/L1/Power"] = (
                service["/Ac/L1/Voltage"] * service["/Ac/L1/Current"]
            )
            service["/Ac/L2/Power"] = (
                service["/Ac/L2/Voltage"] * service["/Ac/L2/Current"]
            )
            service["/Ac/L3/Power"] = (
                service["/Ac/L3/Voltage"] * service["/Ac/L3/Current"]
            )
            rr_p = client.read_holding_registers(REG_POWER, 2, slave=ALFEN_SLAVE_ID)
            power = BinaryPayloadDecoder.fromRegisters(
                rr_p.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            ).decode_32bit_float()
            service["/Ac/Power"] = round(power if not math.isnan(power) else 0)
            rr_e = client.read_holding_registers(REG_ENERGY, 4, slave=ALFEN_SLAVE_ID)
            energy = (
                BinaryPayloadDecoder.fromRegisters(
                    rr_e.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
                ).decode_64bit_float()
                / 1000.0
            )
            service["/Ac/Energy/Forward"] = round(
                energy if not math.isnan(energy) else 0, 3
            )
            rr_sc = client.read_holding_registers(
                REG_AMPS_CONFIG, 2, slave=ALFEN_SLAVE_ID
            )
            set_current = BinaryPayloadDecoder.fromRegisters(
                rr_sc.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            ).decode_32bit_float()
            service["/SetCurrent"] = round(
                set_current if not math.isnan(set_current) else 0, 1
            )
            rr_ph = client.read_holding_registers(REG_PHASES, 1, slave=ALFEN_SLAVE_ID)
            service["/Ac/PhaseCount"] = rr_ph.registers[0]

            # === NEW: WATCHDOG HANDLING LOGIC ===
            # If charging, and it's been 60 seconds since last set, re-send the current.
            if service["/Status"] == 2 and (time.time() - last_current_set_time > 60):
                current_setpoint = float(service["/SetCurrent"])
                logger.info(
                    f"Watchdog: Re-sending current setpoint of {current_setpoint} A to Alfen."
                )
                if _write_current_with_verification(current_setpoint):
                    last_current_set_time = time.time()

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
