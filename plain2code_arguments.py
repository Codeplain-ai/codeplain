import argparse
import os
import re

from plain2code_read_config import get_args_from_config

CODEPLAIN_API_KEY = os.getenv("CODEPLAIN_API_KEY")


DEFAULT_BUILD_FOLDER = "plain_modules"
DEFAULT_CONFORMANCE_TESTS_FOLDER = "conformance_tests"
DEFAULT_BUILD_DEST = "dist"
DEFAULT_CONFORMANCE_TESTS_DEST = "dist_conformance_tests"

UNIT_TESTS_SCRIPT_NAME = "unittests_script"
CONFORMANCE_TESTS_SCRIPT_NAME = "conformance_tests_script"
DEFAULT_LOG_FILE_NAME = "codeplain.log"
PREPARE_ENVIRONMENT_SCRIPT_NAME = "prepare_environment_script"


def process_test_script_path(script_arg_name, config):
    """Resolve script paths in config."""
    config_file = config.config_name
    script_input_path = getattr(config, script_arg_name, None)
    if script_input_path is None:
        return config

    # Check if the script path is absolute and keep the same path
    if isinstance(script_input_path, str) and script_input_path.startswith("/"):
        if not os.path.exists(script_input_path):
            raise FileNotFoundError(
                f"Path for {script_arg_name} not found: {script_input_path}. Set it to the absolute path or relative to the config file."
            )
        return config

    # Otherwise the script path is relative
    # First look for it in the config file directory, then the renderer directory
    config_dir = os.path.dirname(os.path.abspath(config_file))
    config_relative_path = os.path.join(config_dir, script_input_path)
    renderer_dir = os.path.dirname(os.path.abspath(__file__))
    renderer_relative_path = os.path.join(renderer_dir, script_input_path)
    if os.path.exists(config_relative_path):
        setattr(config, script_arg_name, config_relative_path)
    elif os.path.exists(renderer_relative_path):
        setattr(config, script_arg_name, renderer_relative_path)
    else:
        raise FileNotFoundError(
            f"Path for {script_arg_name} not found: {script_input_path}. Set it to the absolute path or relative to the config file."
        )
    return config


def non_empty_string(s):
    if not s:
        raise argparse.ArgumentTypeError("The string cannot be empty.")
    return s


def frid_string(s):
    """Validate that the string contains only numbers separated by dots."""
    if not s:
        raise argparse.ArgumentTypeError("The functional requirement ID cannot be empty.")

    if not re.match(r"^\d+(\.\d+)*$", s):
        raise argparse.ArgumentTypeError(
            "Functional requirement ID string must contain only numbers optionally separated by dots (e.g. '1', '1.2.3')"
        )
    return s


def frid_range_string(s):
    """Validate that the string contains two frids separated by comma."""
    if not s:
        raise argparse.ArgumentTypeError("The range cannot be empty.")

    parts = s.split(",")
    if len(parts) > 2:
        raise argparse.ArgumentTypeError("Range must contain at most two functional requirement IDs separated by comma")

    for part in parts:
        frid_string(part)

    return s


def update_args_with_config(args, parser):
    try:
        config_args = get_args_from_config(args.config_name, parser)
        # Get all action types from the parser
        action_types = {action.dest: action for action in parser._actions}

        # Update args with config values, but command line args take precedence
        for key, value in vars(config_args).items():
            # Skip if the argument was provided on command line
            if key in vars(args):
                arg_action = action_types.get(key)
                if arg_action and isinstance(arg_action, argparse._StoreAction):
                    # For regular arguments, only skip if explicitly provided
                    if getattr(args, key) is not None and (arg_action.default is None or value == arg_action.default):
                        continue
                elif arg_action and isinstance(arg_action, argparse._StoreTrueAction):
                    # For boolean flags, skip if True (explicitly set)
                    if getattr(args, key):
                        continue

            # Set the value from config
            if key in action_types:
                setattr(args, key, value)
            else:
                parser.error(f"Invalid argument: {key}")

    except Exception as e:
        parser.error(f"Error reading config file: {str(e)}")

    return args


def create_parser():
    """Create the argument parser without parsing arguments."""
    parser = argparse.ArgumentParser(description="Render plain code to target code.")

    parser.add_argument(
        "filename",
        type=str,
        help="Path to the plain file to render. The directory containing this file has highest precedence for template loading, "
        "so you can place custom templates here to override the defaults. See --template-dir for more details about template loading.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    parser.add_argument("--base-folder", type=str, help="Base folder for the build files")
    parser.add_argument(
        "--build-folder", type=non_empty_string, default=DEFAULT_BUILD_FOLDER, help="Folder for build files"
    )

    parser.add_argument(
        "--log-to-file",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable logging to a file. Defaults to True. Set to False to disable.",
    )
    parser.add_argument(
        "--log-file-name",
        type=str,
        default=DEFAULT_LOG_FILE_NAME,
        help=f"Name of the log file. Defaults to '{DEFAULT_LOG_FILE_NAME}'."
        "Always resolved relative to the plain file directory."
        "If file on this path already exists, the already existing log file will be overwritten by the current logs.",
    )

    # Add config file arguments
    config_group = parser.add_argument_group("configuration")
    config_group.add_argument(
        "--config-name",
        type=non_empty_string,
        default="config.yaml",
        help="Path to the config file, defaults to config.yaml",
    )

    render_range_group = parser.add_mutually_exclusive_group()
    render_range_group.add_argument(
        "--render-range",
        type=frid_range_string,
        help="Specify a range of functional requirements to render (e.g. `1` , `2`, `3`). "
        "Use comma to separate start and end IDs. If only one ID is provided, only that requirement is rendered. "
        "Range is inclusive of both start and end IDs.",
    )
    render_range_group.add_argument(
        "--render-from",
        type=frid_string,
        help="Continue generation starting from this specific functional requirement (e.g. `2`). "
        "The requirement with this ID will be included in the output. The ID must match one of the functional requirements in your plain file.",
    )

    parser.add_argument(
        "--force-render",
        action="store_true",
        default=False,
        help="Force re-render of all the required modules.",
    )

    parser.add_argument(
        "--unittests-script",
        type=str,
        help="Shell script to run unit tests on generated code. Receives the build folder path as its first argument (default: 'plain_modules').",
    )
    parser.add_argument(
        "--conformance-tests-folder",
        type=non_empty_string,
        default=DEFAULT_CONFORMANCE_TESTS_FOLDER,
        help="Folder for conformance test files",
    )
    parser.add_argument(
        "--conformance-tests-script",
        type=str,
        help="Path to conformance tests shell script. Every conformance test script should accept two arguments: "
        "1) Path to a folder (e.g. `plain_modules/module_name`) containing generated source code, "
        "2) Path to a subfolder of the conformance tests folder (e.g. `conformance_tests/subfoldername`) containing test files.",
    )

    parser.add_argument(
        "--prepare-environment-script",
        type=str,
        help="Path to a shell script that prepares the testing environment. The script should accept the source code folder path as its first argument.",
    )

    parser.add_argument(
        "--api",
        type=str,
        nargs="?",
        const="https://api.codeplain.ai",
        help="Alternative base URL for the API. Default: `https://api.codeplain.ai`",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=CODEPLAIN_API_KEY,
        help="API key used to access the API. If not provided, the `CODEPLAIN_API_KEY` environment variable is used.",
    )
    parser.add_argument(
        "--full-plain",
        action="store_true",
        help="Full preview ***plain specification before code generation."
        "Use when you want to preview context of all ***plain primitives that are going to be included in order to render the given module.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run preview of the code generation (without actually making any changes).",
    )
    parser.add_argument(
        "--replay-with",
        type=str,
        default=None,
        help="",
    )

    parser.add_argument(
        "--template-dir",
        type=str,
        default=None,
        help="Path to a custom template directory. Templates are searched in the following order: "
        "1) Directory containing the plain file, "
        "2) Custom template directory (if provided through this argument), "
        "3) Built-in standard_template_library directory",
    )
    parser.add_argument(
        "--copy-build",
        action="store_true",
        default=False,
        help="If set, copy the rendered contents of code in `--base-folder` folder to `--build-dest` folder after successful rendering.",
    )
    parser.add_argument(
        "--build-dest",
        type=non_empty_string,
        default=DEFAULT_BUILD_DEST,
        help="Target folder to copy rendered contents of code to (used only if --copy-build is set).",
    )
    parser.add_argument(
        "--copy-conformance-tests",
        action="store_true",
        default=False,
        help="If set, copy the conformance tests of code in `--conformance-tests-folder` folder to `--conformance-tests-dest` folder successful rendering. Requires --conformance-tests-script.",
    )
    parser.add_argument(
        "--conformance-tests-dest",
        type=non_empty_string,
        default=DEFAULT_CONFORMANCE_TESTS_DEST,
        help="Target folder to copy conformance tests of code to (used only if --copy-conformance-tests is set).",
    )

    parser.add_argument(
        "--render-machine-graph",
        action="store_true",
        default=False,
        help="If set, render the state machine graph.",
    )

    parser.add_argument(
        "--logging-config-path",
        action="store_true",
        default="logging_config.yaml",
        help="Path to the logging configuration file.",
    )

    return parser


def parse_arguments():
    parser = create_parser()

    args = parser.parse_args()
    args = update_args_with_config(args, parser)

    if args.build_folder == args.build_dest:
        parser.error("--build-folder and --build-dest cannot be the same")
    if args.conformance_tests_folder == args.conformance_tests_dest:
        parser.error("--conformance-tests-folder and --conformance-tests-dest cannot be the same")

    args.render_conformance_tests = args.conformance_tests_script is not None

    if not args.render_conformance_tests and args.copy_conformance_tests:
        parser.error("--copy-conformance-tests requires --conformance-tests-script to be set")

    if not args.log_to_file and args.log_file_name != DEFAULT_LOG_FILE_NAME:
        parser.error("--log-file-name cannot be used when --log-to-file is False.")

    if args.full_plain and args.dry_run:
        parser.error("--full-plain and --dry-run are mutually exclusive")

    script_arg_names = [UNIT_TESTS_SCRIPT_NAME, CONFORMANCE_TESTS_SCRIPT_NAME, PREPARE_ENVIRONMENT_SCRIPT_NAME]
    for script_name in script_arg_names:
        args = process_test_script_path(script_name, args)

    return args
