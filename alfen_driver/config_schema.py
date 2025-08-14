from typing import Any, Dict

from .config_validator import ConfigValidator


def get_config_schema() -> Dict[str, Any]:
    return {
        "sections": {
            "modbus": {
                "title": "Modbus",
                "type": "object",
                "fields": {
                    "ip": {"type": "string", "format": "ipv4", "title": "Charger IP"},
                    "port": {
                        "type": "integer",
                        "min": ConfigValidator.VALID_PORT_RANGE[0],
                        "max": ConfigValidator.VALID_PORT_RANGE[1],
                        "title": "Port",
                    },
                    "socket_slave_id": {
                        "type": "integer",
                        "min": ConfigValidator.VALID_SLAVE_ID_RANGE[0],
                        "max": ConfigValidator.VALID_SLAVE_ID_RANGE[1],
                        "title": "Socket slave ID",
                    },
                    "station_slave_id": {
                        "type": "integer",
                        "min": ConfigValidator.VALID_SLAVE_ID_RANGE[0],
                        "max": ConfigValidator.VALID_SLAVE_ID_RANGE[1],
                        "title": "Station slave ID",
                    },
                },
            },
            "defaults": {
                "title": "Defaults",
                "type": "object",
                "fields": {
                    "intended_set_current": {
                        "type": "number",
                        "min": ConfigValidator.VALID_CURRENT_RANGE[0],
                        "max": ConfigValidator.VALID_CURRENT_RANGE[1],
                        "step": 0.1,
                        "title": "Intended set current (A)",
                    },
                    "station_max_current": {
                        "type": "number",
                        "min": ConfigValidator.VALID_CURRENT_RANGE[0],
                        "max": ConfigValidator.VALID_CURRENT_RANGE[1],
                        "step": 0.1,
                        "title": "Station max current (A)",
                    },
                },
            },
            "controls": {
                "title": "Controls & Safety",
                "type": "object",
                "fields": {
                    "current_tolerance": {
                        "type": "number",
                        "min": 0.0,
                        "step": 0.01,
                        "title": "Verification tolerance (A)",
                    },
                    "update_difference_threshold": {
                        "type": "number",
                        "min": 0.0,
                        "step": 0.01,
                        "title": "Update threshold (A)",
                    },
                    "verification_delay": {
                        "type": "number",
                        "min": 0.0,
                        "step": 0.01,
                        "title": "Verification delay (s)",
                    },
                    "retry_delay": {
                        "type": "number",
                        "min": 0.0,
                        "step": 0.01,
                        "title": "Retry delay (s)",
                    },
                    "max_retries": {
                        "type": "integer",
                        "min": 1,
                        "title": "Max retries",
                    },
                    "watchdog_interval_seconds": {
                        "type": "integer",
                        "min": 1,
                        "title": "Watchdog interval (s)",
                    },
                    "max_set_current": {
                        "type": "number",
                        "min": 0.01,
                        "step": 0.1,
                        "title": "Max set current (A)",
                    },
                    "min_charge_duration_seconds": {
                        "type": "integer",
                        "min": 0,
                        "title": "Min charge duration (s)",
                    },
                    "current_update_interval": {
                        "type": "integer",
                        "min": 0,
                        "title": "Current update interval (ms)",
                    },
                    "verify_delay": {
                        "type": "integer",
                        "min": 0,
                        "title": "Verify delay (ms)",
                    },
                },
            },
            "logging": {
                "title": "Logging",
                "type": "object",
                "fields": {
                    "level": {
                        "type": "enum",
                        "values": ConfigValidator.VALID_LOG_LEVELS,
                        "title": "Level",
                    },
                    "file": {"type": "string", "title": "File path"},
                    "format": {
                        "type": "enum",
                        "values": ["structured", "simple"],
                        "title": "Format",
                    },
                    "max_file_size_mb": {
                        "type": "integer",
                        "min": 1,
                        "title": "Max file size (MB)",
                    },
                    "backup_count": {"type": "integer", "min": 0, "title": "Backups"},
                    "console_output": {"type": "boolean", "title": "Console output"},
                    "json_format": {"type": "boolean", "title": "JSON format"},
                },
            },
            "tibber": {
                "title": "Tibber (optional)",
                "type": "object",
                "fields": {
                    "enabled": {"type": "boolean", "title": "Enabled"},
                    "access_token": {"type": "string", "title": "Access token"},
                    "home_id": {"type": "string", "title": "Home ID"},
                    "charge_on_cheap": {"type": "boolean", "title": "Charge on CHEAP"},
                    "charge_on_very_cheap": {
                        "type": "boolean",
                        "title": "Charge on VERY_CHEAP",
                    },
                    "strategy": {
                        "type": "enum",
                        "values": ["level", "threshold", "percentile"],
                        "title": "Strategy",
                    },
                    "max_price_total": {
                        "type": "number",
                        "min": 0.0,
                        "step": 0.001,
                        "title": "Max price (threshold)",
                    },
                    "cheap_percentile": {
                        "type": "number",
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "title": "Cheap percentile",
                    },
                },
            },
            "schedule": {
                "title": "Schedules",
                "type": "list",
                "item": {
                    "type": "object",
                    "fields": {
                        "active": {"type": "boolean", "title": "Active"},
                        "days": {
                            "type": "array",
                            "items": {"type": "integer", "min": 0, "max": 6},
                            "ui": "days",
                            "title": "Days",
                        },
                        "start_time": {"type": "time", "title": "Start time"},
                        "end_time": {"type": "time", "title": "End time"},
                    },
                },
            },
            "registers": {
                "title": "Registers (advanced)",
                "type": "object",
                "advanced": True,
                "fields": {
                    # Expose a subset commonly tweaked; the rest rely on defaults
                    "station_max_current": {
                        "type": "integer",
                        "min": 0,
                        "title": "Station max current (reg 1100)",
                    },
                },
            },
            "web": {
                "title": "Web UI",
                "type": "object",
                "fields": {
                    "host": {"type": "string", "title": "Bind address"},
                    "port": {
                        "type": "integer",
                        "min": 1,
                        "max": 65535,
                        "title": "Port",
                    },
                },
            },
            "device_instance": {
                "title": "Device instance",
                "type": "integer",
                "min": ConfigValidator.VALID_DEVICE_INSTANCE_RANGE[0],
                "max": ConfigValidator.VALID_DEVICE_INSTANCE_RANGE[1],
            },
            "poll_interval_ms": {
                "title": "Poll interval (ms)",
                "type": "integer",
                "min": ConfigValidator.VALID_POLL_INTERVAL_RANGE[0],
                "max": ConfigValidator.VALID_POLL_INTERVAL_RANGE[1],
            },
            "timezone": {
                "title": "Timezone",
                "type": "string",
            },
        }
    }
