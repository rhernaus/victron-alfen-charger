#!/usr/bin/env python3

import logging
import math
import os  # Import os for path joining
import sys
import traceback

# Add this to make sure the script can be started from anywhere, including the Victron run scripts
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
ALFEN_IP = "10.128.0.64"  # Change this to the actual IP
ALFEN_PORT = 502
ALFEN_SLAVE_ID = 1
DEVICE_INSTANCE = 0  # Match this to the instance number in your service name

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

    # Correct service name with the instance
    service_name = f"com.victronenergy.evcharger.alfen_{DEVICE_INSTANCE}"
    service = VeDbusService(service_name, register=False)

    def set_current_callback(path, value):
        try:
            logger.info("Setting current to %s A", value)
            builder = BinaryPayloadBuilder(byteorder=Endian.BIG, wordorder=Endian.BIG)
            builder.add_32bit_float(float(value))
            payload = builder.to_registers()
            client.write_registers(REG_AMPS_CONFIG, payload, slave=ALFEN_SLAVE_ID)
            # The service path will be updated in the next poll loop
            logger.debug("Successfully set current to %s A", value)
            return True
        except Exception as e:
            logger.error("Set current error: %s\n%s", e, traceback.format_exc())
            return False

    # === Add mandatory paths for GX/VRM visibility ===
    service.add_path("/Mgmt/ProcessName", __file__)
    service.add_path("/Mgmt/ProcessVersion", "1.0")
    service.add_path(
        "/Mgmt/Connection", f"Modbus TCP at {ALFEN_IP}"
    )  # CHANGED: More descriptive
    service.add_path("/DeviceInstance", DEVICE_INSTANCE)  # ADDED
    service.add_path("/Connected", 0)  # ADDED: Crucial for visibility

    # Add device information
    service.add_path("/ProductName", "Alfen Eve Pro Line")
    service.add_path(
        "/ProductId", 0xA142
    )  # CHANGED: Use correct ID for generic EV Charger
    service.add_path(
        "/FirmwareVersion", "N/A"
    )  # Placeholder, can be read from device if available
    service.add_path("/Serial", "ALFEN-001")  # Placeholder

    # Add EV Charger specific paths
    service.add_path(
        "/Status", 0
    )  # 0: Disconnected; 1: Connected; 2: Charging; 3: Charged; 4: Waiting for start; 5: Error
    service.add_path("/Mode", "Auto", writeable=True)  # Manual, Auto, Scheduled
    service.add_path(
        "/SetCurrent", 6.0, writeable=True, onchangecallback=set_current_callback
    )
    service.add_path("/MaxCurrent", 32.0)  # Assuming max 32A
    service.add_path("/Enable", 1, writeable=True)  # For start/stop charging
    service.add_path("/ChargingTime", 0)

    # Add AC paths
    service.add_path("/Ac/Power", 0.0, gettext=lambda p, v: f"{v:.0f}W")
    service.add_path("/Ac/Energy/Forward", 0.0, gettext=lambda p, v: f"{v:.2f}kWh")
    service.add_path("/Ac/PhaseCount", 0)
    service.add_path("/Ac/L1/Voltage", 0.0, gettext=lambda p, v: f"{v:.1f}V")
    service.add_path("/Ac/L1/Current", 0.0, gettext=lambda p, v: f"{v:.1f}A")
    service.add_path("/Ac/L1/Power", 0.0, gettext=lambda p, v: f"{v:.0f}W")
    service.add_path("/Ac/L2/Voltage", 0.0, gettext=lambda p, v: f"{v:.1f}V")
    service.add_path("/Ac/L2/Current", 0.0, gettext=lambda p, v: f"{v:.1f}A")
    service.add_path("/Ac/L2/Power", 0.0, gettext=lambda p, v: f"{v:.0f}W")
    service.add_path("/Ac/L3/Voltage", 0.0, gettext=lambda p, v: f"{v:.1f}V")
    service.add_path("/Ac/L3/Current", 0.0, gettext=lambda p, v: f"{v:.1f}A")
    service.add_path("/Ac/L3/Power", 0.0, gettext=lambda p, v: f"{v:.0f}W")

    # Register the service after adding all paths
    service.register()

    def poll():
        try:
            # Read status
            rr_status = client.read_holding_registers(
                REG_STATUS, 5, slave=ALFEN_SLAVE_ID
            )
            if rr_status.isError():
                raise Exception("Modbus error reading status")

            status_bytes = [r for r in rr_status.registers]
            # Decode the string from registers, handling potential high/low byte order issues
            status_str_raw = b"".join([reg.to_bytes(2, "big") for reg in status_bytes])
            status_str = (
                status_str_raw.decode("ascii", errors="ignore").strip("\x00").strip()
            )

            logger.debug(
                f"Raw status string from Alfen: '{status_str}'"
            )  # ADDED: Debugging for status

            # Map to Victron status codes
            if status_str.startswith(
                "Available"
            ):  # Example, adjust to actual Alfen status strings
                status = 1  # Connected
            elif status_str.startswith("Charge"):
                status = 2  # Charging
            elif status_str.startswith("Vehicle connected"):
                status = 1  # Connected
            else:  # Fallback for other states like 'Ready', 'Finishing'
                status = 1  # Consider them as 'Connected' unless it's an error
            service["/Status"] = status

            # Read voltages
            rr_v = client.read_holding_registers(REG_VOLTAGES, 6, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr_v.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            v1 = decoder.decode_32bit_float()
            v2 = decoder.decode_32bit_float()
            v3 = decoder.decode_32bit_float()
            service["/Ac/L1/Voltage"] = v1 if not math.isnan(v1) else 0
            service["/Ac/L2/Voltage"] = v2 if not math.isnan(v2) else 0
            service["/Ac/L3/Voltage"] = v3 if not math.isnan(v3) else 0

            # Read currents
            rr_c = client.read_holding_registers(REG_CURRENTS, 6, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr_c.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            i1 = decoder.decode_32bit_float()
            i2 = decoder.decode_32bit_float()
            i3 = decoder.decode_32bit_float()
            service["/Ac/L1/Current"] = i1 if not math.isnan(i1) else 0
            service["/Ac/L2/Current"] = i2 if not math.isnan(i2) else 0
            service["/Ac/L3/Current"] = i3 if not math.isnan(i3) else 0

            # Calculate powers per phase (P = V * I)
            service["/Ac/L1/Power"] = (
                service["/Ac/L1/Voltage"] * service["/Ac/L1/Current"]
            )
            service["/Ac/L2/Power"] = (
                service["/Ac/L2/Voltage"] * service["/Ac/L2/Current"]
            )
            service["/Ac/L3/Power"] = (
                service["/Ac/L3/Voltage"] * service["/Ac/L3/Current"]
            )

            # Read total power
            rr_p = client.read_holding_registers(REG_POWER, 2, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr_p.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            power = decoder.decode_32bit_float()
            service["/Ac/Power"] = power if not math.isnan(power) else 0

            # Read total energy
            rr_e = client.read_holding_registers(REG_ENERGY, 4, slave=ALFEN_SLAVE_ID)
            decoder = BinaryPayloadDecoder.fromRegisters(
                rr_e.registers, byteorder=Endian.BIG, wordorder=Endian.BIG
            )
            energy = decoder.decode_64bit_float() / 1000.0  # to kWh
            service["/Ac/Energy/Forward"] = energy if not math.isnan(energy) else 0

            # Read phases
            rr_ph = client.read_holding_registers(REG_PHASES, 1, slave=ALFEN_SLAVE_ID)
            phases = rr_ph.registers[0]
            service["/Ac/PhaseCount"] = phases

            # If we get here, all polls were successful
            service["/Connected"] = 1  # ADDED: Set to connected
            logger.debug("Poll completed successfully")

        except Exception as e:
            logger.error("Poll error: %s\n%s", e, traceback.format_exc())
            service["/Connected"] = 0  # ADDED: Set to disconnected on error

        return True  # Keep the timer running

    # Poll every second
    GLib.timeout_add(1000, poll)

    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == "__main__":
    main()
