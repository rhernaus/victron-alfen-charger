"""Configuration and state persistence for Alfen driver."""

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


class PersistenceManager:
    """Manages persistent configuration and state."""

    def __init__(self, config_path: str = "/data/alfen_driver_config.json"):
        self.config_path = Path(config_path)
        self._state: Dict[str, Any] = {}
        self._load_state()

    def _load_state(self) -> None:
        """Load persisted state from disk."""
        if not self.config_path.exists():
            logger.info(f"No existing state file at {self.config_path}")
            return

        try:
            with open(self.config_path) as f:
                self._state = json.load(f)
            logger.info(f"Loaded state from {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            self._state = {}

    def save_state(self) -> bool:
        """Save current state to disk."""
        try:
            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            # Write atomically using temp file
            temp_path = self.config_path.with_suffix(".tmp")
            with open(temp_path, "w") as f:
                json.dump(self._state, f, indent=2)
            temp_path.replace(self.config_path)

            logger.debug(f"Saved state to {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """Get a persisted value."""
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value to be persisted."""
        self._state[key] = value

    def update(self, data: Dict[str, Any]) -> None:
        """Update multiple values at once."""
        self._state.update(data)

    def get_section(self, section: str) -> Dict[str, Any]:
        """Get all values in a section."""
        result = self._state.get(section, {})
        return result if isinstance(result, dict) else {}

    def set_section(self, section: str, data: Dict[str, Any]) -> None:
        """Set all values in a section."""
        self._state[section] = data

    def clear(self) -> None:
        """Clear all persisted state."""
        self._state = {}

    @property
    def mode(self) -> int:
        """Get persisted charging mode."""
        value = self.get("mode", 0)
        return int(value) if value is not None else 0

    @mode.setter
    def mode(self, value: int) -> None:
        """Set persisted charging mode."""
        self.set("mode", value)

    @property
    def start_stop(self) -> int:
        """Get persisted start/stop state."""
        value = self.get("start_stop", 1)
        return int(value) if value is not None else 1

    @start_stop.setter
    def start_stop(self, value: int) -> None:
        """Set persisted start/stop state."""
        self.set("start_stop", value)

    @property
    def set_current(self) -> float:
        """Get persisted set current."""
        value = self.get("set_current", 6.0)
        return float(value) if value is not None else 6.0

    @set_current.setter
    def set_current(self, value: float) -> None:
        """Set persisted set current."""
        self.set("set_current", value)
