#!/usr/bin/env python3

import logging
import math
import sys
import traceback

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from pymodbus.client import ModbusTcpClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadBuilder, BinaryPayloadDecoder
from vedbus import VeDbusService

# Hardcoded for now, replace with your Alfens IP
ALFEN_IP = "10.128.0.64"  # Change this to the actual IP
ALFEN_PORT = 502
ALFEN_SLAVE_ID = 1

# Modbus registers from Alfen
REG_VOLTAGES = 306  # 6 words (3 floats)
REG_CURRENTS = 320  # 6 words (3 floats)
REG_POWER = 344  # 2 words (float)
REG_ENERGY = 374  # 4 words (double)
REG_STATUS = 1201  # 5 words (string)
REG_AMPS_CONFIG = 1210  # 2 words (float, writable)
REG_PHASES = 1215  # 1 word (uint16, writable)


def main():
    DBusGMainLoop(set_as_default=True)

    # Set up logging
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

    service = VeDbusService("com.victronenergy.evcharger.alfen_0")

    def set_current_callback(path, value):
        try:
            logger.info("Setting current to %s", value)
            builder = BinaryPayloadBuilder(byteorder=Endian.BIG, wordorder=Endian.BIG)
            builder.add_32bit_float(float(value))
            payload = builder.to_registers()
            client.write_registers(REG_AMPS_CONFIG, payload, slave=ALFEN_SLAVE_ID)
            service["/SetCurrent"] = value
            logger.debug("Successfully set current to %s", value)
            return True
        except Exception as e:
            logger.error("Set current error: %s\n%s", e, traceback.format_exc())
            return False

    # Add required paths
    service.add_path("/ProductName", "Alfen Eve Pro Line")
    service.add_path("/ProductId", 0xFFFF)  # Placeholder
    service.add_path("/FirmwareVersion", 1)  # Placeholder
    service.add_path("/Serial", "ALFEN-001")  # Placeholder
    service.add_path("/Status", 0)
    service.add_path("/Mode", "Auto", writable=True)
    service.add_path("/Ac/Power", 0.0)
    service.add_path("/Ac/Energy/Forward", 0.0)
    service.add_path(
        "/SetCurrent", 6.0, writable=True, onchangecallback=set_current_callback
    )
    service.add_path("/MaxCurrent", 32.0)  # Assuming max 32A
    service.add_path("/Enable", 1, writable=True)
    service.add_path("/Ac/PhaseCount", 3)  # Assume 3 phases, update if needed
    service.add_path("/Ac/L1/Voltage", 0.0)
    service.add_path("/Ac/L1/Current", 0.0)
    service.add_path("/Ac/L1/Power", 0.0)
    service.add_path("/Ac/L2/Voltage", 0.0)
    service.add_path("/Ac/L2/Current", 0.0)
    service.add_path("/Ac/L2/Power", 0.0)
    service.add_path("/Ac/L3/Voltage", 0.0)
    service.add_path("/Ac/L3/Current", 0.0)
    service.add_path("/Ac/L3/Power", 0.0)
    service.add_path("/ChargingTime", 0)  # Placeholder

    def poll():
        try:
            logger.debug("Starting poll")
            # Read status
            rr = client.read_holding_registers(REG_STATUS, 5, slave=ALFEN_SLAVE_ID)
            status_bytes = [r for r in rr.registers]
            status_str = "".join(chr(b & 0xFF) for b in status_bytes).strip("\x00")
            # Map to Victron status codes (approximate)
            if status_str.startswith("A"):
                status = 0  # Disconnected
            elif status_str.startswith("B"):
                status = 1  # Connected
            elif status_str.startswith("C"):
                status = 2  # Charging
            elif status_str.startswith("D"):
                status = 3  # Charged
            else:
                status = 99  # Error
            service["/Status"] = status

            # Read voltages
            rr = client.read_holding_registers(REG_VOLTAGES, 6, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            v1 = decoder.decode_32bit_float()
            v2 = decoder.decode_32bit_float()
            v3 = decoder.decode_32bit_float()
            service["/Ac/L1/Voltage"] = v1 if not math.isnan(v1) else 0
            service["/Ac/L2/Voltage"] = v2 if not math.isnan(v2) else 0
            service["/Ac/L3/Voltage"] = v3 if not math.isnan(v3) else 0

            # Read currents
            rr = client.read_holding_registers(REG_CURRENTS, 6, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            i1 = decoder.decode_32bit_float()
            i2 = decoder.decode_32bit_float()
            i3 = decoder.decode_32bit_float()
            service["/Ac/L1/Current"] = i1 if not math.isnan(i1) else 0
            service["/Ac/L2/Current"] = i2 if not math.isnan(i2) else 0
            service["/Ac/L3/Current"] = i3 if not math.isnan(i3) else 0

            # Calculate powers (P = V * I)
            service["/Ac/L1/Power"] = (
                service["/Ac/L1/Voltage"] * service["/Ac/L1/Current"]
            )
            service["/Ac/L2/Power"] = (
                service["/Ac/L2/Voltage"] * service["/Ac/L2/Current"]
            )
            service["/Ac/L3/Power"] = (
                service["/Ac/L3/Voltage"] * service["/Ac/L3/Current"]
            )

            # Read power
            rr = client.read_holding_registers(REG_POWER, 2, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            power = decoder.decode_32bit_float()
            service["/Ac/Power"] = power if not math.isnan(power) else 0

            # Read energy
            rr = client.read_holding_registers(REG_ENERGY, 4, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            energy = decoder.decode_64bit_float() / 1000.0  # to kWh
            service["/Ac/Energy/Forward"] = energy if not math.isnan(energy) else 0

            # Read phases
            rr = client.read_holding_registers(REG_PHASES, 1, slave=ALFEN_SLAVE_ID)
            phases = rr.registers[0]
            service["/Ac/PhaseCount"] = phases
            logger.debug("Poll completed successfully")
        except Exception as e:
            logger.error("Poll error: %s\n%s", e, traceback.format_exc())

        return True

    # Add more callbacks if needed, e.g. for Mode, Enable, Phases

    GLib.timeout_add(1000, poll)  # Poll every second

    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == "__main__":
    main()
