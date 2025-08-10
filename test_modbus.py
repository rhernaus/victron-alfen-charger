#!/usr/bin/env python3

import math
import sys
import time

from pymodbus.client import ModbusTcpClient
from pymodbus.payload import BinaryPayloadDecoder

# Configuration
ALFEN_IP = "10.128.0.64"  # Your Alfens IP
ALFEN_PORT = 502
ALFEN_SLAVE_ID = 1

# Modbus registers from Alfen
REG_VOLTAGES = 306  # 6 words (3 floats)
REG_CURRENTS = 320  # 6 words (3 floats)
REG_POWER = 344  # 2 words (float)
REG_ENERGY = 374  # 4 words (double)
REG_STATUS = 1201  # 5 words (string)
REG_AMPS_CONFIG = 1210  # 2 words (float)
REG_PHASES = 1215  # 1 word (uint16)
# Hypothesis: 1211 holds a validity period (seconds) for the set current
REG_SET_CURRENT_VALID_SECS = 1211  # 1 word (uint16?)


def main():
    client = ModbusTcpClient(host=ALFEN_IP, port=ALFEN_PORT)
    if not client.connect():
        print("Failed to connect to Alfen charger")
        sys.exit(1)

    print(
        "Connected to Alfen charger. Polling data every 5 seconds. Press Ctrl+C to stop."
    )

    try:
        while True:
            try:
                # Read status
                rr = client.read_holding_registers(REG_STATUS, 5, slave=ALFEN_SLAVE_ID)
                status_bytes = [r for r in rr.registers]
                status_str = "".join(chr(b & 0xFF) for b in status_bytes).strip("\x00")
                print(f"Status: {status_str}")

                # Read voltages
                rr = client.read_holding_registers(
                    REG_VOLTAGES, 6, slave=ALFEN_SLAVE_ID
                )
                decoder = BinaryPayloadDecoder.fromRegisters(
                    rr.registers, byteorder=">", wordorder=">"
                )
                v1 = decoder.decode_32bit_float()
                v2 = decoder.decode_32bit_float()
                v3 = decoder.decode_32bit_float()
                print(
                    f"Voltages: L1={v1 if not math.isnan(v1) else 0:.2f}V, L2={v2 if not math.isnan(v2) else 0:.2f}V, L3={v3 if not math.isnan(v3) else 0:.2f}V"
                )

                # Read currents
                rr = client.read_holding_registers(
                    REG_CURRENTS, 6, slave=ALFEN_SLAVE_ID
                )
                decoder = BinaryPayloadDecoder.fromRegisters(
                    rr.registers, byteorder=">", wordorder=">"
                )
                i1 = decoder.decode_32bit_float()
                i2 = decoder.decode_32bit_float()
                i3 = decoder.decode_32bit_float()
                print(
                    f"Currents: L1={i1 if not math.isnan(i1) else 0:.2f}A, L2={i2 if not math.isnan(i2) else 0:.2f}A, L3={i3 if not math.isnan(i3) else 0:.2f}A"
                )

                # Read power
                rr = client.read_holding_registers(REG_POWER, 2, slave=ALFEN_SLAVE_ID)
                decoder = BinaryPayloadDecoder.fromRegisters(
                    rr.registers, byteorder=">", wordorder=">"
                )
                power = decoder.decode_32bit_float()
                print(f"Power: {power if not math.isnan(power) else 0:.2f}W")

                # Read energy
                rr = client.read_holding_registers(REG_ENERGY, 4, slave=ALFEN_SLAVE_ID)
                decoder = BinaryPayloadDecoder.fromRegisters(
                    rr.registers, byteorder=">", wordorder=">"
                )
                energy = decoder.decode_64bit_float() / 1000.0  # to kWh
                print(f"Energy: {energy if not math.isnan(energy) else 0:.2f} kWh")

                # Read phases
                rr = client.read_holding_registers(REG_PHASES, 1, slave=ALFEN_SLAVE_ID)
                phases = rr.registers[0]
                print(f"Phases: {phases}")

                # Read current config with diagnostics
                rr = client.read_holding_registers(
                    REG_AMPS_CONFIG, 2, slave=ALFEN_SLAVE_ID
                )
                regs = rr.registers
                dec_bb = BinaryPayloadDecoder.fromRegisters(
                    regs, byteorder=">", wordorder=">"
                )
                curr_bb = dec_bb.decode_32bit_float()
                dec_bl = BinaryPayloadDecoder.fromRegisters(
                    regs, byteorder=">", wordorder="<"
                )
                curr_bl = dec_bl.decode_32bit_float()
                print(
                    f"Configured Current raw={regs} dec_bb={curr_bb if not math.isnan(curr_bb) else 0:.2f}A dec_bl={curr_bl if not math.isnan(curr_bl) else 0:.2f}A"
                )

                print("---")

            except Exception as e:
                print(f"Poll error: {e}")

            time.sleep(5)  # Poll every 5 seconds

    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        client.close()


if __name__ == "__main__":
    main()
