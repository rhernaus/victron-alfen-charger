# Configuration Guide for Alfen EV Charger Driver

This guide provides comprehensive documentation for configuring the Alfen EV Charger Driver with validation, error handling, and best practices.

## Table of Contents
- [Configuration Overview](#configuration-overview)
- [Configuration Validation](#configuration-validation)
- [Configuration Sections](#configuration-sections)
- [Error Messages and Solutions](#error-messages-and-solutions)
- [Examples](#examples)
- [Best Practices](#best-practices)

## Configuration Overview

The Alfen driver uses a YAML configuration file (`alfen_driver_config.yaml`) that is validated on startup to ensure correct settings and prevent runtime errors.

### Basic Configuration Structure

```yaml
# Required section - Modbus connection settings
modbus:
  ip: "192.168.1.100"  # Required: IP address of Alfen charger
  port: 502            # Optional: Modbus TCP port (default: 502)
  socket_slave_id: 1   # Optional: Slave ID for measurements (default: 1)
  station_slave_id: 200 # Optional: Slave ID for control (default: 200)

# Optional section - Default values
defaults:
  intended_set_current: 6.0  # Default charging current in amperes
  station_max_current: 32.0  # Maximum station current in amperes

# Optional section - Control limits
controls:
  max_set_current: 32.0  # Maximum settable current
  current_tolerance: 0.5  # Verification tolerance in amperes

# Global settings
device_instance: 0      # Venus OS device instance (0-255)
poll_interval_ms: 1000  # Polling interval in milliseconds
timezone: "Europe/Amsterdam"  # Timezone for schedules
```

## Configuration Validation

The driver includes comprehensive configuration validation that:
- Checks all required fields are present
- Validates data types and formats
- Verifies values are within acceptable ranges
- Checks relationships between settings
- Provides helpful error messages with suggestions

### Using the Validator

```python
from alfen_driver.config_validator import ConfigValidator
import yaml

# Load configuration
with open('alfen_driver_config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Validate configuration
validator = ConfigValidator()
is_valid, errors = validator.validate(config)

if not is_valid:
    for error in errors:
        print(f"❌ {error.field}: {error.message}")
        if error.suggestion:
            print(f"   → {error.suggestion}")
else:
    print("✅ Configuration is valid")
```

### Automatic Validation on Startup

The driver automatically validates configuration when loading:

```python
from alfen_driver.config import load_config

try:
    config = load_config("alfen_driver_config.yaml")
    print("Configuration loaded successfully")
except ConfigurationError as e:
    print(f"Configuration error: {e}")
    # Error message includes field names and suggestions
```

## Configuration Sections

### Modbus Section (Required)

Controls the Modbus TCP connection to the Alfen charger.

| Field | Type | Required | Default | Valid Range | Description |
|-------|------|----------|---------|-------------|-------------|
| `ip` | string | Yes | - | Valid IPv4 | Alfen charger IP address |
| `port` | integer | No | 502 | 1-65535 | Modbus TCP port |
| `socket_slave_id` | integer | No | 1 | 1-247 | Slave ID for measurements |
| `station_slave_id` | integer | No | 200 | 1-247 | Slave ID for control |

**Example:**
```yaml
modbus:
  ip: "192.168.1.100"
  port: 502
  socket_slave_id: 1
  station_slave_id: 200
```

### Registers Section (Optional)

Maps Modbus register addresses for different data types.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `voltages` | integer | 306 | Voltage registers start address |
| `currents` | integer | 320 | Current registers start address |
| `power` | integer | 344 | Power register address |
| `energy` | integer | 374 | Energy counter register |
| `status` | integer | 1201 | Status register address |
| `amps_config` | integer | 1210 | Current setting register |
| `phases` | integer | 1215 | Phase configuration register |

**Example:**
```yaml
registers:
  voltages: 306
  currents: 320
  power: 344
  energy: 374
```

### Defaults Section (Optional)

Sets default operational values.

| Field | Type | Default | Valid Range | Description |
|-------|------|---------|-------------|-------------|
| `intended_set_current` | float | 6.0 | 0-80 A | Default charging current |
| `station_max_current` | float | 32.0 | 0-80 A | Maximum station current |

**Example:**
```yaml
defaults:
  intended_set_current: 16.0  # 16A default charging
  station_max_current: 32.0   # 32A max for station
```

### Controls Section (Optional)

Defines control and safety limits.

| Field | Type | Default | Valid Range | Description |
|-------|------|---------|-------------|-------------|
| `max_set_current` | float | 64.0 | 0-80 A | Maximum settable current |
| `current_tolerance` | float | 0.5 | 0-5 A | Verification tolerance |
| `current_update_interval` | integer | 30000 | 1000-300000 ms | Current refresh interval |
| `verify_delay` | integer | 100 | 0-5000 ms | Verification delay |
| `max_retries` | integer | 3 | 1-10 | Maximum retry attempts |

**Example:**
```yaml
controls:
  max_set_current: 32.0
  current_tolerance: 0.5
  current_update_interval: 30000
  verify_delay: 100
  max_retries: 3
```

### Schedule Section (Optional)

Configures time-based charging schedules.

| Field | Type | Description |
|-------|------|-------------|
| `items` | list | List of schedule configurations |

Schedule item fields:
| Field | Type | Description |
|-------|------|-------------|
| `active` | boolean | Whether schedule is active |
| `days` | list[int] | Days of week (0=Mon, 6=Sun) |
| `start_time` | string | Start time in HH:MM format |
| `end_time` | string | End time in HH:MM format |
| `current` | float | Charging current for this schedule |

**Example:**
```yaml
schedule:
  items:
    - active: true
      days: [1, 2, 3, 4, 5]  # Weekdays
      start_time: "23:00"
      end_time: "07:00"
      current: 16.0
    - active: true
      days: [0, 6]  # Weekend
      start_time: "00:00"
      end_time: "23:59"
      current: 32.0
```

### Logging Section (Optional)

Configures logging behavior.

| Field | Type | Default | Valid Values | Description |
|-------|------|---------|--------------|-------------|
| `level` | string | "INFO" | DEBUG, INFO, WARNING, ERROR, CRITICAL | Log level |
| `file` | string | "/var/log/alfen_driver.log" | Valid path | Log file path |
| `max_file_size_mb` | integer | 10 | 1-100 | Max log file size (MB) |
| `backup_count` | integer | 5 | 0-100 | Number of backup files |
| `format` | string | See defaults | - | Log message format |

**Example:**
```yaml
logging:
  level: "INFO"
  file: "/var/log/alfen_driver.log"
  max_file_size_mb: 10  # 10MB
  backup_count: 5
```

### Global Settings

Top-level configuration settings.

| Field | Type | Default | Valid Range | Description |
|-------|------|---------|-------------|-------------|
| `device_instance` | integer | 0 | 0-255 | Venus OS device instance |
| `poll_interval_ms` | integer | 1000 | 100-60000 | Polling interval (ms) |
| `timezone` | string | "UTC" | Valid timezone | Schedule timezone |

## Error Messages and Solutions

### Common Validation Errors

#### Missing Required Field
```
❌ modbus.ip: Modbus IP address is required
   → Add 'ip' field with the Alfen charger's IP address (e.g., '192.168.1.100')
```

**Solution:** Add the missing field to your configuration:
```yaml
modbus:
  ip: "192.168.1.100"
```

#### Invalid IP Address Format
```
❌ modbus.ip: Invalid IP address format: 'charger.local'
   → Use a valid IPv4 address format (e.g., '192.168.1.100')
```

**Solution:** Use a valid IPv4 address instead of hostname:
```yaml
modbus:
  ip: "192.168.1.100"  # ✓ Valid
  # ip: "charger.local"  # ✗ Invalid
```

#### Value Out of Range
```
❌ defaults.intended_set_current: Current 100.0A is out of valid range (0.0, 80.0)
   → Use a current value between 0.0A and 80.0A
```

**Solution:** Use a value within the valid range:
```yaml
defaults:
  intended_set_current: 32.0  # ✓ Valid
  # intended_set_current: 100.0  # ✗ Out of range
```

#### Type Mismatch
```
❌ modbus.port: Port must be an integer, got str
   → Use an integer value between 1 and 65535 (default: 502)
```

**Solution:** Use the correct data type:
```yaml
modbus:
  port: 502    # ✓ Valid integer
  # port: "502"  # ✗ String instead of integer
```

#### Invalid Time Format
```
❌ schedule.items[0].start_time: Invalid time format: '8:00'
   → Use HH:MM format (e.g., '08:00' or '22:30')
```

**Solution:** Use proper HH:MM format:
```yaml
schedule:
  items:
    - start_time: "08:00"  # ✓ Valid
      # start_time: "8:00"  # ✗ Invalid format
```

#### Relationship Violation
```
❌ defaults.intended_set_current: Intended current 40.0A exceeds max set current 32.0A
   → Reduce intended current to 32.0A or less, or increase max_set_current
```

**Solution:** Ensure related values are consistent:
```yaml
defaults:
  intended_set_current: 16.0  # Less than max
controls:
  max_set_current: 32.0
```

### Validation Warnings

Warnings indicate potential issues but don't prevent operation:

```
⚠️ poll_interval_ms: Very short poll interval 100ms may cause high CPU usage
   → Consider using 1000ms or higher for normal operation
```

## Examples

### Minimal Configuration
```yaml
# Absolute minimum required configuration
modbus:
  ip: "192.168.1.100"
```

### Standard Home Configuration
```yaml
modbus:
  ip: "192.168.1.100"
  port: 502

defaults:
  intended_set_current: 16.0  # 16A for home charging
  station_max_current: 32.0   # 32A breaker

controls:
  max_set_current: 32.0
  current_tolerance: 0.5

device_instance: 0
poll_interval_ms: 1000
timezone: "Europe/Amsterdam"

logging:
  level: "INFO"
  file: "/var/log/alfen_driver.log"
```

### Advanced Configuration with Schedules
```yaml
modbus:
  ip: "192.168.1.100"
  port: 502
  socket_slave_id: 1
  station_slave_id: 200

registers:
  voltages: 306
  currents: 320
  power: 344
  energy: 374
  status: 1201
  amps_config: 1210
  phases: 1215

defaults:
  intended_set_current: 6.0
  station_max_current: 32.0

controls:
  max_set_current: 32.0
  current_tolerance: 0.5
  current_update_interval: 30000
  verify_delay: 100
  max_retries: 3

schedule:
  items:
    # Night charging on weekdays
    - active: true
      days: [0, 1, 2, 3, 4]
      start_time: "23:00"
      end_time: "07:00"
      current: 16.0

    # Weekend charging
    - active: true
      days: [5, 6]
      start_time: "00:00"
      end_time: "23:59"
      current: 32.0

device_instance: 0
poll_interval_ms: 1000
timezone: "Europe/Amsterdam"

logging:
  level: "INFO"
  file: "/var/log/alfen_driver.log"
  max_file_size_mb: 10
  backup_count: 5
```

## Best Practices

### 1. Start with Minimal Configuration
Begin with the minimum required settings and add optional sections as needed:
```yaml
modbus:
  ip: "192.168.1.100"
```

### 2. Use Configuration Validation
Always validate configuration before deployment:
```bash
python3 -c "from alfen_driver.config import load_config; load_config('alfen_driver_config.yaml')"
```

### 3. Set Appropriate Current Limits
Ensure current limits match your electrical installation:
```yaml
defaults:
  intended_set_current: 16.0  # Match your use case
  station_max_current: 32.0   # Match your breaker rating

controls:
  max_set_current: 32.0  # Never exceed installation capacity
```

### 4. Configure Logging for Production
Use appropriate log levels and rotation:
```yaml
logging:
  level: "INFO"  # Use INFO for production, DEBUG for troubleshooting
  file: "/var/log/alfen_driver.log"
  max_file_size_mb: 10  # 10MB
  backup_count: 5     # Keep 5 old logs
```

### 5. Test Schedules Carefully
Verify schedule times and days are correct:
```yaml
schedule:
  items:
    - active: false  # Start with inactive for testing
      days: [0, 1, 2, 3, 4]  # Monday-Friday
      start_time: "23:00"     # 11 PM
      end_time: "07:00"       # 7 AM
```

### 6. Monitor Poll Interval Impact
Balance responsiveness with CPU usage:
- 1000ms (1 second): Good balance for most cases
- 500ms: More responsive but higher CPU
- 2000ms+: Lower CPU but less responsive

### 7. Document Custom Settings
Add comments to explain non-default values:
```yaml
modbus:
  ip: "192.168.1.100"
  port: 502
  socket_slave_id: 1    # Alfen default
  station_slave_id: 200 # Alfen default for control registers
```

### 8. Version Control Configuration
Keep configuration in version control but exclude sensitive data:
```bash
# Create sample configuration
cp alfen_driver_config.yaml alfen_driver_config.sample.yaml
# Edit sample to remove sensitive data
# Commit sample, ignore actual config
echo "alfen_driver_config.yaml" >> .gitignore
```

## Troubleshooting Configuration Issues

### Configuration Won't Load
1. Check YAML syntax (use a YAML validator)
2. Ensure required fields are present
3. Check file permissions
4. Review validation error messages

### Validation Errors
1. Read the specific error message
2. Follow the suggestion provided
3. Check the field type and range
4. Verify relationships between fields

### Runtime Issues
1. Enable DEBUG logging to see configuration loading
2. Check that configuration matches hardware setup
3. Verify network connectivity to Modbus device
4. Ensure register addresses match your Alfen model

## Configuration Schema Reference

For programmatic access to the configuration schema:

```python
from alfen_driver.config_validator import ConfigValidator

validator = ConfigValidator()
schema = validator.get_config_schema()

# Print schema as JSON
import json
print(json.dumps(schema, indent=2))
```

This provides the complete schema with types, ranges, defaults, and descriptions for all configuration fields.
