import importlib.resources
import logging
import logging.config
import os
import signal
import sys
import threading
import traceback
from pathlib import Path
from types import TracebackType
from typing import Optional

import yaml
from liquid2.exceptions import TemplateNotFoundError
from requests.exceptions import RequestException

import codeplain_REST_api as codeplain_api
import file_utils
import plain_file
import plain_spec
from event_bus import EventBus
from module_renderer import ModuleRenderer
from plain2code_arguments import parse_arguments
from plain2code_console import console
from plain2code_events import RenderFailed
from plain2code_exceptions import (
    ConflictingRequirements,
    InternalClientError,
    InternalServerError,
    InvalidAPIKey,
    InvalidFridArgument,
    LLMInternalError,
    MissingAPIKey,
    MissingPreviousFunctionalitiesError,
    MissingResource,
    ModuleDoesNotExistError,
    NetworkConnectionError,
    OutdatedClientVersion,
    PlainSyntaxError,
    RenderCancelledError,
    RenderingCreditBalanceTooLow,
)
from plain2code_logger import (
    LOGGER_NAME,
    CrashLogHandler,
    IndentedFormatter,
    TuiLoggingHandler,
    dump_crash_logs,
    get_log_file_path,
)
from plain2code_state import RunState
from plain2code_utils import format_duration_hms, print_dry_run_output
from system_config import system_config
from tui.plain2code_tui import Plain2CodeTUI

DEFAULT_TEMPLATE_DIRS = importlib.resources.files("standard_template_library")
RENDER_THREAD_SHUTDOWN_TIMEOUT = 0.7


def print_exit_summary(
    run_state: RunState,
    spec_filename: str,
    error_message: Optional[str] = None,
    verbose: bool = False,
    exc_info: Optional[tuple[type[BaseException] | None, BaseException | None, TracebackType | None]] = None,
) -> None:
    console.quiet = False
    """Print render outcome after the TUI exits (terminal restored)."""

    msg = "\n[#79FC96]✓ rendering completed\n\n" if run_state.render_succeeded else "[#FF6B6B]✗ rendering failed\n\n"
    msg += f"  [#8E8F91]render id:\t\t\t[#FFFFFF]{run_state.render_id}\n"
    msg += f"  [#8E8F91]input file:\t\t\t[#FFFFFF]{spec_filename}\n"
    msg += f"  [#8E8F91]generated code folder:\t[#FFFFFF]{run_state.render_generated_code_path or '-'}\n\n"
    msg += f"[#8E8F91]functionalities  [#FFFFFF]{run_state.rendered_functionalities}  [#8E8F91]used credits  [#FFFFFF]{run_state.rendered_functionalities}  [#8E8F91]render time  [#FFFFFF]{format_duration_hms(run_state.render_time)}\n"
    console.info(msg)

    if not run_state.render_succeeded and error_message:
        console.error(error_message)
    if verbose and exc_info and exc_info[0] is not None:
        console.error("".join(traceback.format_exception(*exc_info)))
    console.quiet = True


def get_render_range(render_range, plain_source):
    render_range = render_range.split(",")
    range_end = render_range[1] if len(render_range) == 2 else render_range[0]

    return _get_frids_range(plain_source, render_range[0], range_end)


def get_render_range_from(start, plain_source):
    return _get_frids_range(plain_source, start)


def compute_render_range(args, plain_source_tree):
    """Compute render range from --render-range or --render-from arguments.

    Args:
        args: Parsed command line arguments
        plain_source_tree: Parsed plain source tree

    Returns:
        List of FRIDs to render, or None to render all
    """
    if args.render_range:
        return get_render_range(args.render_range, plain_source_tree)
    elif args.render_from:
        return get_render_range_from(args.render_from, plain_source_tree)
    return None


def _get_frids_range(plain_source, start, end=None):
    frids = list(plain_spec.get_frids(plain_source))

    start = str(start)

    if start not in frids:
        raise InvalidFridArgument(f"Invalid start functionality ID: {start}. Valid IDs are: {frids}.")

    if end is not None:
        end = str(end)
        if end not in frids:
            raise InvalidFridArgument(f"Invalid end functionality ID: {end}. Valid IDs are: {frids}.")

        end_idx = frids.index(end) + 1
    else:
        end_idx = len(frids)

    start_idx = frids.index(start)
    if start_idx >= end_idx:
        raise InvalidFridArgument(f"Start functionality ID: {start} must be before end functionality ID: {end}.")

    return frids[start_idx:end_idx]


def setup_logging(
    args,
    event_bus: EventBus,
    log_to_file: bool,
    log_file_name: str,
    plain_file_path: Optional[str],
    headless: bool = False,
):
    # Set default level to INFO for everything not explicitly configured
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger(LOGGER_NAME).setLevel(logging.INFO)
    logging.getLogger("git").setLevel(logging.WARNING)
    logging.getLogger("transitions").setLevel(logging.ERROR)
    logging.getLogger("transitions.extensions.diagrams").setLevel(logging.ERROR)

    log_file_path = get_log_file_path(plain_file_path, log_file_name)

    # Try to load logging configuration from YAML file
    if args.logging_config_path and os.path.exists(args.logging_config_path):
        try:
            with open(args.logging_config_path, "r") as f:
                config = yaml.safe_load(f)
                logging.config.dictConfig(config)
                console.info(f"Loaded logging configuration from {args.logging_config_path}")
        except Exception as e:
            console.warning(f"Failed to load logging configuration from {args.logging_config_path}: {str(e)}")

    # The IndentedFormatter provides better multiline log readability.
    # We add the TuiLoggingHandler to the root logger.
    root_logger = logging.getLogger(LOGGER_NAME)
    configured_log_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)  # Capture all logs; handlers will filter levels as needed

    formatter = IndentedFormatter("%(levelname)s:%(name)s:%(message)s")

    if not headless:
        handler = TuiLoggingHandler(event_bus)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    if log_to_file and log_file_path:
        try:
            file_handler = logging.FileHandler(log_file_path, mode="w")
            file_handler.setFormatter(formatter)
            file_handler.setLevel(configured_log_level)
            root_logger.addHandler(file_handler)
        except Exception as e:
            console.warning(f"Failed to setup file logging to {log_file_path}: {str(e)}")
    else:
        # If file logging is disabled, use CrashLogHandler to buffer logs in memory
        # in case we need to dump them on crash.
        crash_handler = CrashLogHandler()
        crash_handler.setFormatter(formatter)
        crash_handler.setLevel(configured_log_level)
        root_logger.addHandler(crash_handler)


def _check_connection(codeplainAPI: codeplain_api.CodeplainAPI):
    """Check API connectivity and validate API key and client version."""
    response = codeplainAPI.connection_check(system_config.client_version)

    if not response.get("api_key_valid", False):
        raise InvalidAPIKey(
            "Provided API key is invalid. Please provide a valid API key using the CODEPLAIN_API_KEY environment variable "
            "or the --api-key argument."
        )

    if not response.get("client_version_valid", False):
        min_version = response.get("min_client_version", "unknown")
        raise OutdatedClientVersion(
            f"Your client version ({system_config.client_version}) is outdated. Minimum required version is {min_version}. "
            "Please update using:"
            "  uv tool upgrade codeplain"
        )


def render(args, run_state: RunState, event_bus: EventBus):  # noqa: C901
    template_dirs = file_utils.get_template_directories(args.filename, args.template_dir, DEFAULT_TEMPLATE_DIRS)

    # Compute render range from either --render-range or --render-from
    render_range = None
    if args.render_range or args.render_from:
        # Parse the plain file to get the plain_source for FRID extraction
        _, plain_source, _ = plain_file.plain_file_parser(args.filename, template_dirs)
        render_range = compute_render_range(args, plain_source)

    codeplainAPI = codeplain_api.CodeplainAPI(args.api_key, console)
    codeplainAPI.verbose = args.verbose
    assert args.api is not None and args.api != "", "API URL is required"
    codeplainAPI.api_url = args.api

    _check_connection(codeplainAPI)

    stop_event = threading.Event()
    enter_pause_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())

    module_renderer = ModuleRenderer(
        codeplainAPI,
        args.filename,
        render_range,
        template_dirs,
        args,
        run_state,
        event_bus,
        stop_event=stop_event,
        enter_pause_event=enter_pause_event,
    )

    render_error: list[Exception] = []
    run_state.render_succeeded = False

    def run_render():
        try:
            module_renderer.render_module()
            run_state.set_render_succeeded(True)
        except RenderCancelledError:
            pass  # TUI already closed, nothing to report
        except Exception as e:
            run_state.set_render_succeeded(False)
            render_error.append(e)
            event_bus.publish(RenderFailed(error_message=str(e)))

    if args.headless:
        console.info(f"Render started. Render ID: {run_state.render_id}")
        try:
            module_renderer.render_module()
            run_state.set_render_succeeded(True)
        except RenderCancelledError:
            run_state.set_render_succeeded(False)
            pass
        return
    else:
        render_thread = threading.Thread(target=run_render, daemon=True)
        app = Plain2CodeTUI(
            event_bus=event_bus,
            on_ready=render_thread.start,
            render_id=run_state.render_id,
            unittests_script=args.unittests_script,
            conformance_tests_script=args.conformance_tests_script,
            prepare_environment_script=args.prepare_environment_script,
            state_machine_version=system_config.client_version,
            enter_pause_event=enter_pause_event,
            css_path="styles.css",
        )
        app.run()

        stop_event.set()
        render_thread.join(timeout=RENDER_THREAD_SHUTDOWN_TIMEOUT)

    if render_error:
        raise render_error[0]


def main():  # noqa: C901
    args = parse_arguments()

    # Handle early-exit flags before heavy initialization
    if args.dry_run or args.full_plain:
        template_dirs = file_utils.get_template_directories(args.filename, args.template_dir, DEFAULT_TEMPLATE_DIRS)

        try:
            if args.full_plain:
                module_name = Path(args.filename).stem
                plain_source = plain_file.read_module_plain_source(module_name, template_dirs)
                [full_plain_source, _] = file_utils.get_loaded_templates(template_dirs, plain_source)
                console.info("Full plain text:\n")
                console.info(full_plain_source)
                return

            if args.dry_run:
                console.info("Printing dry run output...\n")
                _, plain_source_tree, _ = plain_file.plain_file_parser(args.filename, template_dirs)
                render_range = compute_render_range(args, plain_source_tree)
                print_dry_run_output(plain_source_tree, render_range)
                return
        except Exception as e:
            console.error(f"Error: {str(e)}")
            return

    event_bus = EventBus()

    if not args.api:
        args.api = "https://api.codeplain.ai"

    run_state = RunState(spec_filename=args.filename, replay_with=args.replay_with)

    if args.headless:
        # Suppress Rich console output.
        console.quiet = True

    setup_logging(args, event_bus, args.log_to_file, args.log_file_name, args.filename, args.headless)

    exc_info = None
    error_message = None

    try:
        # Validate API key is present
        if not args.api_key:
            raise MissingAPIKey(
                "API key is required. Please set the CODEPLAIN_API_KEY environment variable or provide it with the --api-key argument."
            )
        render(args, run_state, event_bus)
    except InvalidFridArgument as e:
        error_message = f"Invalid FRID argument: {str(e)}.\n"
    except FileNotFoundError as e:
        error_message = f"File not found: {str(e)}\n"
    except MissingResource as e:
        error_message = f"Missing resource: {str(e)}\n"
    except TemplateNotFoundError as e:
        error_message = f"""Template not found: {str(e)}\n
The required template could not be found. Templates are searched in the following order (highest to lowest precedence):

    1. The directory containing your .plain file
    2. The directory specified by --template-dir (if provided)
    3. The built-in 'standard_template_library' directory

Please ensure that the missing template exists in one of these locations, or specify the correct --template-dir if using custom templates.
        """
    except PlainSyntaxError as e:
        error_message = f"Plain syntax error: {str(e)}\n"
    except KeyboardInterrupt:
        error_message = "Keyboard interrupt"
    except RequestException as e:
        error_message = f"Error rendering plain code: {str(e)}\n"
    except MissingPreviousFunctionalitiesError as e:
        error_message = f"Error rendering plain code: {str(e)}\n"
    except MissingAPIKey as e:
        error_message = f"Missing API key: {str(e)}\n"
    except InvalidAPIKey as e:
        error_message = f"Invalid API key: {str(e)}\n"
    except OutdatedClientVersion as e:
        error_message = f"Outdated client version: {str(e)}\n"
    except (InternalServerError, InternalClientError):
        exc_info = sys.exc_info()
        error_message = f"Internal server error.\n\nPlease report the error to support@codeplain.ai with the attached {args.log_file_name} file."
    except ConflictingRequirements as e:
        error_message = f"Conflicting requirements: {str(e)}\n"
    except RenderingCreditBalanceTooLow as e:
        error_message = f"Credit balance too low: {str(e)}\n"
    except LLMInternalError as e:
        exc_info = sys.exc_info()
        error_message = f"LLM internal error: {str(e)}\n"
    except NetworkConnectionError as e:
        error_message = f"Connection error: {str(e)}\n\nPlease check that your internet connection is working."
    except ModuleDoesNotExistError as e:
        error_message = str(e)
    except Exception as e:
        exc_info = sys.exc_info()
        error_message = f"Error rendering plain code: {str(e)}\n"
    finally:
        print_exit_summary(
            run_state,
            args.filename,
            error_message=error_message,
            verbose=args.verbose,
            exc_info=exc_info,
        )
        if exc_info:
            # Log traceback
            dump_crash_logs(args)


if __name__ == "__main__":  # noqa: C901
    main()
