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

```bash
git clone https://github.com/yourusername/victron-alfen-charger.git
cd victron-alfen-charger
```

2. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Install dependencies:

```bash
pip install pymodbus==3.6.4
```

4. Update `ALFEN_IP` in the scripts to match your charger's IP address.

## Usage

### Testing Modbus Locally

Run the test script on your PC to check communication:

```bash
python3 test_modbus.py
```

It will poll and print charger data every 5 seconds.

### Running on Victron GX

Victron GX devices run Venus OS, a Linux-based system. You'll need SSH access enabled on the GX (via Settings > Services > SSH).

1. SSH into your Victron GX device (default username: root, set password in Venus OS settings).

2. Update package list and install required tools (git, python3, pip):

   ```bash
   opkg update
   opkg install git
   opkg install python3
   opkg install python3-pip
   ```

3. Clone the repository:

   ```bash
   cd /data
   git clone https://github.com/rhernaus/victron-alfen-charger.git
   cd victron-alfen-charger
   ```

4. Install pymodbus:

   ```bash
   pip install pymodbus==3.6.4
   ```

5. Configure the script:
   - Edit `driver.py` with a text editor (e.g., vi or nano; install nano if needed with `opkg install nano`).
   - Update `ALFEN_IP` to your charger's IP address.
   - Adjust other settings like slave ID or registers if necessary.

6. Make the script executable:

   ```bash
   chmod +x driver.py
   ```

7. Test the script manually:

   ```bash
   ./driver.py
   ```

   Check for errors and ensure it connects to the charger and publishes to DBus.

8. Set up as a persistent service:
   - Option 1: Add to `/data/rc.local` (create if it doesn't exist):
     ```bash
     echo '/data/victron-alfen-charger/driver.py &' >> /data/rc.local
     chmod +x /data/rc.local
     ```
   - Option 2: Create a systemd service (advanced):
     Create `/etc/systemd/system/alfen-driver.service`:

     ```ini
     [Unit]
     Description=Alfen EV Charger Driver
     After=multi-user.target

     [Service]
     ExecStart=/data/victron-alfen-charger/driver.py
     Restart=always

     [Install]
     WantedBy=multi-user.target
     ```

     Then:

     ```bash
     systemctl daemon-reload
     systemctl enable alfen-driver.service
     systemctl start alfen-driver.service
     ```

9. Reboot the GX device:

   ```bash
   reboot
   ```

10. Verify in the Victron interface: The charger should appear under Devices as "Alfen Eve Pro Line".

If issues arise, check logs with `systemctl status alfen-driver` (if using systemd) or debug manually.

## Notes

- The script assumes 3-phase configuration; adjust if needed.
- For production, add error handling, logging, and configuration options.
- Ensure the Alfen charger is set up with Active Load Balancing and Modbus TCP enabled.

## License

MIT License (or specify your license).
