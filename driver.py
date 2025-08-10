#!/usr/bin/env python3

import logging
import math
import os
import sys
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

# Hardcoded for now, replace with your Alfens IP
ALFEN_IP = "10.128.0.64"
ALFEN_PORT = 502
ALFEN_SLAVE_ID = 1
DEVICE_INSTANCE = 0

# Modbus registers from Alfen
REG_VOLTAGES = 306
REG_CURRENTS = 320
REG_POWER = 344
REG_ENERGY = 374
REG_STATUS = 1201
REG_AMPS_CONFIG = 1210
REG_PHASES = 1215


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
            logger.info("Setting current to %s A", value)
            builder = BinaryPayloadBuilder(byteorder=Endian.BIG, wordorder=Endian.BIG)
            builder.add_32bit_float(float(value))
            payload = builder.to_registers()
            client.write_registers(REG_AMPS_CONFIG, payload, slave=ALFEN_SLAVE_ID)
            logger.debug("Successfully set current to %s A", value)
            return True
        except Exception as e:
            logger.error("Set current error: %s\n%s", e, traceback.format_exc())
            return False

    # Add mandatory paths for GX/VRM visibility
    service.add_path("/Mgmt/ProcessName", __file__)
    service.add_path("/Mgmt/ProcessVersion", "1.1")
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
    service.add_path("/Mode", "Auto", writeable=True)
    service.add_path(
        "/SetCurrent", 6.0, writeable=True, onchangecallback=set_current_callback
    )
    service.add_path("/MaxCurrent", 32.0)
    service.add_path("/Enable", 1, writeable=True)
    service.add_path("/ChargingTime", 0)

    # Add AC paths (REMOVED 'gettext' ARGUMENT FROM ALL)
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
        try:
            rr_status = client.read_holding_registers(
                REG_STATUS, 5, slave=ALFEN_SLAVE_ID
            )
            if rr_status.isError():
                raise ConnectionError("Modbus error reading status")

            status_bytes = rr_status.registers
            status_str_raw = b"".join([reg.to_bytes(2, "big") for reg in status_bytes])
            status_str = (
                status_str_raw.decode("ascii", errors="ignore").strip("\x00").strip()
            )

            logger.debug(f"Raw status string from Alfen: '{status_str}'")

            # NOTE: You will still need to adjust this mapping based on your log output
            if "Charge" in status_str:
                status = 2  # Charging
            elif "Available" in status_str or "Vehicle connected" in status_str:
                status = 1  # Connected
            else:
                status = 0  # Disconnected / Unknown
            service["/Status"] = status

            # ... (rest of the poll function is the same)
            rr_v = client.read_holding_registers(REG_VOLTAGES, 6, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr_v.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            v1, v2, v3 = (
                decoder.decode_32bit_float(),
                decoder.decode_32bit_float(),
                decoder.decode_32bit_float(),
            )
            service["/Ac/L1/Voltage"] = v1 if not math.isnan(v1) else 0
            service["/Ac/L2/Voltage"] = v2 if not math.isnan(v2) else 0
            service["/Ac/L3/Voltage"] = v3 if not math.isnan(v3) else 0

            rr_c = client.read_holding_registers(REG_CURRENTS, 6, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr_c.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            i1, i2, i3 = (
                decoder.decode_32bit_float(),
                decoder.decode_32bit_float(),
                decoder.decode_32bit_float(),
            )
            service["/Ac/L1/Current"] = i1 if not math.isnan(i1) else 0
            service["/Ac/L2/Current"] = i2 if not math.isnan(i2) else 0
            service["/Ac/L3/Current"] = i3 if not math.isnan(i3) else 0

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
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr_p.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            power = decoder.decode_32bit_float()
            service["/Ac/Power"] = power if not math.isnan(power) else 0

            rr_e = client.read_holding_registers(REG_ENERGY, 4, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr_e.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            energy = decoder.decode_64bit_float() / 1000.0
            service["/Ac/Energy/Forward"] = energy if not math.isnan(energy) else 0

            rr_ph = client.read_holding_registers(REG_PHASES, 1, slave=ALFEN_SLAVE_ID)
            service["/Ac/PhaseCount"] = rr_ph.registers[0]

            service["/Connected"] = 1
            logger.debug("Poll completed successfully")

        except Exception as e:
            logger.error("Poll error: %s\n%s", e, traceback.format_exc())
            service["/Connected"] = 0

        return True

    GLib.timeout_add(1000, poll)

    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == "__main__":
    main()
