import argparse
import os
from typing import Optional, Sequence

from path_resolution import resolve_path
from plain2code_console import console
from plain2code_exceptions import AmbiguousConfigFileError
from plain2code_read_config import get_args_from_config

# Attribute on the parsed Namespace mapping each argument dest to its source:
# "cli" (explicit on the command line), "config" (from config.yaml), or
# "default" (neither -- the argparse default was used).
ARGUMENT_SOURCES = "argument_sources"

CODEPLAIN_API_KEY = os.getenv("CODEPLAIN_API_KEY")


DEFAULT_BUILD_FOLDER = "plain_modules"
DEFAULT_CONFORMANCE_TESTS_FOLDER = "conformance_tests"
DEFAULT_BUILD_DEST = "dist"
DEFAULT_CONFORMANCE_TESTS_DEST = "dist_conformance_tests"

UNIT_TESTS_SCRIPT_NAME = "unittests_script"
CONFORMANCE_TESTS_SCRIPT_NAME = "conformance_tests_script"
DEFAULT_LOG_FILE_NAME = "codeplain.log"
PREPARE_ENVIRONMENT_SCRIPT_NAME = "prepare_environment_script"


def _resolve_path_arg(
    arg_name: str,
    args,
    cwd: str,
    config_dir: Optional[str],
    spec_dir: str,
) -> Optional[str]:
    """Resolve a path-valued argument on ``args`` in-place using its recorded source.

    Returns the resolved absolute path, or ``None`` if the argument is unset.
    """
    original_value = getattr(args, arg_name, None)
    if original_value is None:
        return None

    source = getattr(args, ARGUMENT_SOURCES, {}).get(arg_name, "default")
    resolved = resolve_path(original_value, source, cwd=cwd, config_dir=config_dir, spec_dir=spec_dir)
    setattr(args, arg_name, resolved)
    return resolved


def _resolve_script_path(
    script_arg_name: str,
    args,
    cwd: str,
    config_dir: Optional[str],
    spec_dir: str,
) -> None:
    """Resolve a script-path argument and verify the script exists on disk.

    Scripts are expected to exist at parse time because we are about to execute
    them; missing directories, by contrast, are created on demand.
    """
    original_value = getattr(args, script_arg_name, None)
    resolved = _resolve_path_arg(script_arg_name, args, cwd, config_dir, spec_dir)
    if resolved is None:
        return

    if not os.path.exists(resolved):
        raise FileNotFoundError(
            f"File not found: Path for {script_arg_name} not found: {original_value} (resolved to {resolved})."
        )


def non_empty_string(s):
    if not s:
        raise argparse.ArgumentTypeError("The string cannot be empty.")
    return s


def frid_string(s):
    """Validate that the FRID is an integer."""
    if not s:
        raise argparse.ArgumentTypeError("The functionality ID cannot be empty.")

    try:
        int(s)
    except ValueError:
        raise argparse.ArgumentTypeError("Functionality ID string must be a number.")
    return s


def frid_range_string(s):
    """Validate that the string contains two frids separated by comma."""
    if not s:
        raise argparse.ArgumentTypeError("The range cannot be empty.")

    parts = s.split(",")
    if len(parts) > 2:
        raise argparse.ArgumentTypeError("Range must contain at most two functionality IDs separated by comma")

    for part in parts:
        frid_string(part)

    return s


def resolve_config_file(config_name: str, plain_file_path: str):
    """
    Resolve the config file path by searching in two locations:
    1. Directory of the plain file
    2. Current working directory (where render is called from)

    Returns the resolved absolute path, or None if the file is not found in either location.
    Raises AmbiguousConfigFileError if the file exists in both locations (and they differ).
    """
    plain_file_dir = os.path.dirname(os.path.abspath(plain_file_path))
    cwd = os.getcwd()

    plain_dir_config = os.path.normpath(os.path.join(plain_file_dir, config_name))
    cwd_config = os.path.normpath(os.path.join(cwd, config_name))

    in_plain_dir = os.path.exists(plain_dir_config)
    in_cwd = os.path.exists(cwd_config)
    same_location = plain_dir_config == cwd_config

    if in_plain_dir and in_cwd and not same_location:
        raise AmbiguousConfigFileError(
            f"Config file '{config_name}' was found in two locations:\n"
            f"  - Plain file directory: {plain_file_dir}\n"
            f"  - Current working directory: {cwd}\n"
            f"Remove the config file from one of these locations to resolve the ambiguity."
        )

    if in_plain_dir:
        return plain_dir_config
    if in_cwd:
        return cwd_config
    return None


def _detect_cli_provided_keys(command_line: Optional[Sequence[str]] = None) -> set[str]:
    """Return the set of argument dests that were explicitly provided on the command line.

    Uses a second parser with every default replaced by ``argparse.SUPPRESS``, so
    any dest that ends up on the resulting namespace must have come from the
    command line.
    """
    tracker = create_parser()
    for action in tracker._actions:
        action.default = argparse.SUPPRESS
    tracked_ns, _ = tracker.parse_known_args(command_line)
    return set(vars(tracked_ns).keys())


def update_args_with_config(args, parser, cli_provided: set[str]):
    """Merge config.yaml values into ``args`` and record the source of each value.

    CLI-supplied values always win. Anything the CLI did not supply is taken
    from config.yaml if present, else left at its argparse default. The mapping
    from dest to source ("cli" / "config" / "default") is attached to ``args``
    as ``ARGUMENT_SOURCES`` so downstream code can resolve paths against the right
    base directory.
    """
    action_dests = {action.dest for action in parser._actions}
    sources: dict[str, str] = {dest: ("cli" if dest in cli_provided else "default") for dest in action_dests}

    try:
        resolved_config = resolve_config_file(args.config_name, args.filename)

        if resolved_config is None:
            console.info(f"No config file '{args.config_name}' found. Proceeding without one.")
            setattr(args, ARGUMENT_SOURCES, sources)
            return args

        args.config_name = resolved_config
        config_args = get_args_from_config(resolved_config, parser)

        for key, value in vars(config_args).items():
            if key not in action_dests:
                parser.error(f"Invalid argument: {key}")

            # CLI takes precedence over config.
            if key in cli_provided:
                continue

            setattr(args, key, value)
            sources[key] = "config"

    except AmbiguousConfigFileError as e:
        parser.error(str(e))
    except Exception as e:
        parser.error(f"Error reading config file: {str(e)}")

    setattr(args, ARGUMENT_SOURCES, sources)
    return args


def _add_arg(parser, *args, path=False, **kwargs):
    """Add an argument; tag as path-valued when ``path=True`` so ``parse_arguments`` resolves it."""
    action = parser.add_argument(*args, **kwargs)
    if path:
        action._is_path = True


def create_parser():
    """Create the argument parser without parsing arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Render plain code to target code. "
            "Path arguments resolve based on where they were written: "
            "values given on the command line are resolved against the current working "
            "directory, values read from config.yaml are resolved against the config "
            "file's directory, and defaults are resolved against the directory containing "
            "the plain file. Absolute paths (and paths starting with '~') are used as-is."
        ),
    )

    _add_arg(
        parser,
        "filename",
        type=str,
        nargs="?",
        help="Path to the plain file to render. The directory containing this file has highest precedence for template loading, "
        "so you can place custom templates here to override the defaults. See --template-dir for more details about template loading.",
    )

    _add_arg(
        parser,
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Set default log level to DEBUG for TUI and file logs",
    )

    _add_arg(parser, "--base-folder", type=str, help="Base folder for the build files", path=True)

    _add_arg(
        parser,
        "--build-folder",
        type=non_empty_string,
        default=DEFAULT_BUILD_FOLDER,
        help="Folder for build files",
        path=True,
    )

    _add_arg(
        parser,
        "--log-to-file",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable logging to a file. Defaults to True. Set to False to disable.",
    )
    _add_arg(
        parser,
        "--log-file-name",
        type=str,
        default=DEFAULT_LOG_FILE_NAME,
        help=f"Name of the log file. Defaults to '{DEFAULT_LOG_FILE_NAME}'. "
        "If a file already exists at the resolved path, it will be overwritten by the current logs.",
        path=True,
    )

    # Add config file arguments
    config_group = parser.add_argument_group("configuration")
    _add_arg(
        config_group,
        "--config-name",
        type=non_empty_string,
        default="config.yaml",
        help="Name of the config file to look for. Looked up in the plain file directory and the current working directory. Defaults to config.yaml.",
    )

    render_range_group = parser.add_mutually_exclusive_group()
    _add_arg(
        render_range_group,
        "--render-range",
        type=frid_range_string,
        help="Specify a range of functionalities to render (e.g. `1` , `2`, `3`). "
        "Use comma to separate start and end IDs. If only one functionality ID is provided, only that functionality is rendered. "
        "Range is inclusive of both start and end IDs.",
    )
    _add_arg(
        render_range_group,
        "--render-from",
        type=frid_string,
        help="Continue generation starting from this specific functionality (e.g. `2`). "
        "The functionality with this ID will be included in the output. The functionality ID must match one of the functionalities in your plain file.",
    )

    _add_arg(
        parser,
        "--force-render",
        action="store_true",
        default=False,
        help="Force re-render of all the required modules.",
    )

    _add_arg(
        parser,
        "--unittests-script",
        type=str,
        help="Shell script to run unit tests on generated code. Receives the build folder path as its first argument (default: 'plain_modules').",
    )
    _add_arg(
        parser,
        "--conformance-tests-folder",
        type=non_empty_string,
        default=DEFAULT_CONFORMANCE_TESTS_FOLDER,
        help="Folder for conformance test files",
        path=True,
    )
    _add_arg(
        parser,
        "--conformance-tests-script",
        type=str,
        help="Path to conformance tests shell script. Every conformance test script should accept two arguments: "
        "1) Path to a folder (e.g. `plain_modules/module_name`) containing generated source code, "
        "2) Path to a subfolder of the conformance tests folder (e.g. `conformance_tests/subfoldername`) containing test files.",
    )

    _add_arg(
        parser,
        "--prepare-environment-script",
        type=str,
        help="Path to a shell script that prepares the testing environment. The script should accept the source code folder path as its first argument.",
    )

    _add_arg(
        parser,
        "--test-script-timeout",
        type=int,
        default=None,
        help="Timeout for test scripts in seconds. If not provided, the default timeout of 120 seconds is used.",
    )

    _add_arg(
        parser,
        "--api",
        type=str,
        nargs="?",
        const="https://api.codeplain.ai",
        help="Alternative base URL for the API. Default: `https://api.codeplain.ai`",
    )
    _add_arg(
        parser,
        "--api-key",
        type=str,
        default=CODEPLAIN_API_KEY,
        help="API key used to access the API. If not provided, the `CODEPLAIN_API_KEY` environment variable is used.",
    )
    _add_arg(
        parser,
        "--full-plain",
        action="store_true",
        help="Full preview ***plain specification before code generation. "
        "Use when you want to preview context of all ***plain primitives that are going to be included in order to render the given module.",
    )
    _add_arg(
        parser,
        "--dry-run",
        action="store_true",
        help="Dry run preview of the code generation (without actually making any changes).",
    )
    _add_arg(
        parser,
        "--replay-with",
        type=str,
        default=None,
        help="",
    )

    _add_arg(
        parser,
        "--template-dir",
        type=str,
        default=None,
        help="Path to a custom template directory. Templates are searched in the following order: "
        "1) Directory containing the plain file, "
        "2) Custom template directory (if provided through this argument), "
        "3) Built-in standard_template_library directory",
        path=True,
    )
    _add_arg(
        parser,
        "--copy-build",
        action="store_true",
        default=True,
        help="If set, copy the rendered contents of code in `--base-folder` folder to `--build-dest` folder after successful rendering.",
    )
    _add_arg(
        parser,
        "--build-dest",
        type=non_empty_string,
        default=DEFAULT_BUILD_DEST,
        help="Target folder to copy rendered contents of code to (used only if --copy-build is set).",
        path=True,
    )
    _add_arg(
        parser,
        "--copy-conformance-tests",
        action="store_true",
        default=False,
        help="If set, copy the conformance tests of code in `--conformance-tests-folder` folder to `--conformance-tests-dest` folder successful rendering. Requires --conformance-tests-script.",
    )
    _add_arg(
        parser,
        "--conformance-tests-dest",
        type=non_empty_string,
        default=DEFAULT_CONFORMANCE_TESTS_DEST,
        help="Target folder to copy conformance tests of code to (used only if --copy-conformance-tests is set).",
        path=True,
    )

    _add_arg(
        parser,
        "--render-machine-graph",
        action="store_true",
        default=False,
        help="If set, render the state machine graph.",
    )

    _add_arg(
        parser,
        "--logging-config-path",
        type=str,
        default="logging_config.yaml",
        help="Path to the logging configuration file.",
        path=True,
    )

    _add_arg(
        parser,
        "--headless",
        action="store_true",
        default=False,
        help="Run in headless mode: no TUI, no terminal output except a single render-started message. "
        "All logs are written to the log file.",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        default=False,
        help="Display account status including user information, API key label, and rendering credits. "
        "Does not render any code.",
    )

    parser.add_argument(
        "--version",
        action="store_true",
        default=False,
        help="Display the client version and exit.",
    )

    return parser


def parse_arguments(command_line: Optional[Sequence[str]] = None):
    parser = create_parser()

    args = parser.parse_args(command_line)
    cli_provided = _detect_cli_provided_keys(command_line)

    if args.version:
        if args.dry_run or args.full_plain:
            parser.error("--version cannot be used with --dry-run or --full-plain")
        return args

    if args.status:
        if args.dry_run or args.full_plain:
            parser.error("--status cannot be used with --dry-run or --full-plain")
        return args

    # Validate filename is provided when needed
    if not args.version and not args.status and not args.filename:
        parser.error("the following arguments are required: filename")

    args = update_args_with_config(args, parser, cli_provided)

    cwd = os.getcwd()

    spec_dir = os.path.dirname(os.path.abspath(args.filename))
    # args.config_name is the resolved absolute path when a config file was found,
    # otherwise it is still just the lookup name (e.g. "config.yaml").
    config_dir = os.path.dirname(args.config_name) if os.path.isabs(args.config_name) else None

    for action in parser._actions:
        if getattr(action, "_is_path", False):
            _resolve_path_arg(action.dest, args, cwd, config_dir, spec_dir)

    if args.build_folder == args.build_dest:
        parser.error("--build-folder and --build-dest cannot be the same")
    if args.conformance_tests_folder == args.conformance_tests_dest:
        parser.error("--conformance-tests-folder and --conformance-tests-dest cannot be the same")

    args.render_conformance_tests = args.conformance_tests_script is not None

    if not args.render_conformance_tests and args.copy_conformance_tests:
        parser.error("--copy-conformance-tests requires --conformance-tests-script to be set")

    if not args.log_to_file and args.argument_sources.get("log_file_name") != "default":
        parser.error("--log-file-name cannot be used when --log-to-file is False.")

    if args.full_plain and args.dry_run:
        parser.error("--full-plain and --dry-run are mutually exclusive")

    script_arg_names = [UNIT_TESTS_SCRIPT_NAME, CONFORMANCE_TESTS_SCRIPT_NAME, PREPARE_ENVIRONMENT_SCRIPT_NAME]
    for script_name in script_arg_names:
        _resolve_script_path(script_name, args, cwd, config_dir, spec_dir)

    return args
