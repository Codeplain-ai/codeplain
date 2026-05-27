import importlib.resources
import sys

import yaml

from _version import __version__
from plain2code_console import console


class SystemConfig:
    """Manages system-level configuration including requirements and error messages."""

    def __init__(self):
        self.config = self._load_config()
        if "error_messages" not in self.config:
            raise KeyError("Missing 'error_messages' section in system_config.yaml")

        self.client_version = __version__
        self.error_messages = self.config["error_messages"]

    def _load_config(self):
        """Load system configuration from YAML file."""
        config_path = importlib.resources.files("config").joinpath("system_config.yaml")
        try:
            with config_path.open("r") as f:
                yaml_data = yaml.safe_load(f)
                return yaml_data
        except Exception as e:
            console.error(f"Failed to load system configuration: {e}")
            sys.exit(69)


# Create a singleton instance
system_config = SystemConfig()
