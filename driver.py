#!/usr/bin/env python3

import logging
import math
import os
import sys
import time  # NEW: Import time module for charging timer
import traceback

# Add this to make sure the script can be started from anywhere
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
REG_STATUS = 1201  # This is a single register (integer)
REG_AMPS_CONFIG = 1210
REG_PHASES = 1215

# --- Globals for charging timer ---
charging_start_time = 0


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
    if not client.connect():
        logger.error(
            "Failed to connect to Alfen charger at %s:%s", ALFEN_IP, ALFEN_PORT
        )
        sys.exit(1)
    logger.info(
        "Successfully connected to Alfen charger at %s:%s", ALFEN_IP, ALFEN_PORT
    )

    service_name = f"com.victronenergy.evcharger.alfen_{DEVICE_INSTANCE}"
    service = VeDbusService(service_name, register=False)

    def set_current_callback(path, value):
        try:
            logger.info("GUI request to set current to %s A", value)
            builder = BinaryPayloadBuilder(byteorder=Endian.BIG, wordorder=Endian.BIG)
            builder.add_32bit_float(float(value))
            payload = builder.to_registers()
            client.write_registers(REG_AMPS_CONFIG, payload, slave=ALFEN_SLAVE_ID)
            logger.info("Successfully sent new current setpoint to Alfen.")
            return True
        except Exception as e:
            logger.error("Set current error: %s\n%s", e, traceback.format_exc())
            return False

    # Add mandatory paths
    service.add_path("/Mgmt/ProcessName", __file__)
    service.add_path("/Mgmt/ProcessVersion", "1.2")
    service.add_path("/Mgmt/Connection", f"Modbus TCP at {ALFEN_IP}")
    service.add_path("/DeviceInstance", DEVICE_INSTANCE)
    service.add_path("/Connected", 0)

    # Add device information
    service.add_path("/ProductName", "Alfen Eve Pro Line")
    service.add_path("/ProductId", 0xA142)
    service.add_path("/FirmwareVersion", "N/A")
    service.add_path("/Serial", "ALFEN-001")

    # Add EV Charger specific paths
    service.add_path("/Status", 0)
    service.add_path(
        "/Mode", 1, writeable=True
    )  # 0=Manual, 1=Auto. For now, we'll just let it be set.
    service.add_path(
        "/SetCurrent", 0.0, writeable=True, onchangecallback=set_current_callback
    )
    service.add_path("/MaxCurrent", 32.0)
    service.add_path("/Enable", 1, writeable=True)
    service.add_path("/ChargingTime", 0)

    # NEW: Add overall AC Current path
    service.add_path("/Ac/Current", 0.0)

    # Add AC paths
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
        global charging_start_time
        try:
            # === STATUS ===
            # CHANGED: Read status as a single integer register
            rr_status = client.read_holding_registers(
                REG_STATUS, 1, slave=ALFEN_SLAVE_ID
            )
            if rr_status.isError():
                raise ConnectionError("Modbus error reading status")

            # Alfen status 2 seems to be charging, map to Victron status 2
            alfen_status = rr_status.registers[0]
            # Simple mapping, may need expanding if Alfen has more states
            # Victron Status: 0=Disconnected, 1=Connected, 2=Charging
            new_victron_status = 2 if alfen_status == 2 else 1

            old_victron_status = service["/Status"]
            service["/Status"] = new_victron_status

            # === CHARGING TIME ===
            # NEW: Calculate charging time
            if (
                new_victron_status == 2 and old_victron_status != 2
            ):  # Charging just started
                charging_start_time = time.time()
            elif new_victron_status != 2:  # Not charging
                charging_start_time = 0

            if charging_start_time > 0:
                service["/ChargingTime"] = time.time() - charging_start_time
            else:
                service["/ChargingTime"] = 0

            # === VOLTAGES ===
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

            # === CURRENTS ===
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

            # NEW: Populate overall AC Current for the main screen
            service["/Ac/Current"] = round(max(i1, i2, i3), 2)

            # === POWER & ENERGY ===
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

            # === SET CURRENT & PHASES ===
            # NEW: Read the configured current from the charger to display it correctly
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

            # If we get here, all is good
            service["/Connected"] = 1
            logger.debug("Poll completed successfully")

        except Exception as e:
            logger.error("Poll error: %s\n%s", e, traceback.format_exc())
            service["/Connected"] = 0  # Disconnect on error

        return True  # Keep the timer running

    GLib.timeout_add(1000, poll)  # Poll every 1000ms

    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == "__main__":
    main()
