# Victron-Alfen-Charger Integration

This project provides a Python script to integrate an Alfen Eve Pro Line EV charger with a Victron GX device via Modbus TCP and DBus. The charger is exposed as a first-class EV charger in the Victron ecosystem.

## Requirements

- Python 3.6+
- pymodbus==3.6.4 (install via pip)
- Victron Venus OS (for driver.py)
- Access to Alfen charger via Modbus TCP (configured as slave)

## Files

- **driver.py**: Main script to run on Victron GX. Connects to Alfen via Modbus and publishes data to DBus.
- **test_modbus.py**: Test script to verify Modbus communication with Alfen charger locally on your PC.

## Setup

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/victron-alfen-charger.git
   cd victron-alfen-charger
   ```

2. Create and activate a virtual environment:
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```
   pip install pymodbus==3.6.4
   ```

4. Update `ALFEN_IP` in the scripts to match your charger's IP address.

## Usage

### Testing Modbus Locally
Run the test script on your PC to check communication:
```
python3 test_modbus.py
```
It will poll and print charger data every 5 seconds.

### Running on Victron GX
1. Transfer `driver.py` to your Victron GX device (e.g., via SSH to `/data/`).
2. Install dependencies on the GX (Venus OS supports opkg; install python3, pip, and pymodbus).
3. Make executable: `chmod +x driver.py`.
4. Run as a service (e.g., add to `/data/rc.local` or create a systemd unit).
5. Restart the device or service to see the charger in the Victron interface.

## Notes
- The script assumes 3-phase configuration; adjust if needed.
- For production, add error handling, logging, and configuration options.
- Ensure the Alfen charger is set up with Active Load Balancing and Modbus TCP enabled.

## License
MIT License (or specify your license).
