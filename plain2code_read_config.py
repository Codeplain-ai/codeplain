from argparse import ArgumentParser, Namespace
from typing import Any, Dict

import yaml

from plain2code_console import console


def load_config(config_file: str) -> Dict[str, Any]:
    """Load configuration from YAML file."""
    try:
        with open(config_file, "r") as f:
            return yaml.safe_load(f)
    except Exception as e:
        console.error(f"Error loading config file: {e}. Please check the config file path and the config file content.")
        raise e


def validate_config(config: Dict[str, Any], parser: ArgumentParser) -> None:
    """Validate the configuration against the parser."""
    actions = [action.dest for action in parser._actions]
    for action in parser._actions:
        if hasattr(action, "option_strings"):
            actions.extend(opt.lstrip("-") for opt in action.option_strings)

    for key in config.keys():
        if key not in actions:
            raise KeyError(f"Invalid configuration key: {key}")
    return config


def get_args_from_config(config_file: str, parser: ArgumentParser) -> Namespace:
    """
    Read configuration from YAML file and return args compatible with plain2code_arguments.py.

    Args:
        config_file: Path to the YAML config file
    Returns:
        Namespace object with arguments as defined in plain2code_arguments.py

    Raises:
        FileNotFoundError: If config file doesn't exist
        KeyError: If argument not found in parser arguments
    """

    args = Namespace()

    # Load config
    config = load_config(config_file)
    config = validate_config(config, parser)

    for action in parser._actions:
        # Create a list of possible config keys for this argument
        possible_keys = [action.dest]
        if hasattr(action, "option_strings"):
            # Add all option strings without leading dashes
            possible_keys.extend(opt.lstrip("-") for opt in action.option_strings)

        # Handling multi-named arguments like --verbose and -v
        config_value = None
        for key in possible_keys:
            if key in config:
                config_value = config[key]
                break

        if config_value is not None:
            setattr(args, action.dest, config_value)
    return args
