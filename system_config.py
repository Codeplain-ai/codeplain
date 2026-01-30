import importlib.resources
import shutil
import sys

import yaml

from plain2code_console import console


class SystemConfig:
    """Manages system-level configuration including requirements and error messages."""

    def __init__(self):
        self.config = self._load_config()
        if "client_version" not in self.config:
            raise KeyError("Missing 'client_version' in system_config.yaml")
        if "system_requirements" not in self.config:
            raise KeyError("Missing 'system_requirements' section in system_config.yaml")
        if "error_messages" not in self.config:
            raise KeyError("Missing 'error_messages' section in system_config.yaml")

        self.client_version = self.config["client_version"]
        self.requirements = self.config["system_requirements"]
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

    def verify_requirements(self):
        """Verify all system requirements are met."""
        for req_data in self.requirements.values():
            if not shutil.which(req_data["command"]):
                console.error(req_data["error_message"])
                sys.exit(69)

    def get_error_message(self, message_key, **kwargs):
        """Get a formatted error message by its key."""
        if message_key not in self.error_messages:
            raise KeyError(f"Unknown error message key: {message_key}")
        return self.error_messages[message_key]["message"].format(**kwargs)


# Create a singleton instance
system_config = SystemConfig()
