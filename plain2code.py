import importlib.resources
import logging
import logging.config
import os
import sys
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
from plain2code_exceptions import (
    ConflictingRequirements,
    CreditBalanceTooLow,
    InternalClientError,
    InternalServerError,
    InvalidAPIKey,
    InvalidFridArgument,
    LLMInternalError,
    MissingAPIKey,
    MissingPreviousFunctionalitiesError,
    MissingResource,
    NetworkConnectionError,
    OutdatedClientVersion,
    PlainSyntaxError,
)
from plain2code_logger import (
    CrashLogHandler,
    IndentedFormatter,
    RetryOnlyFilter,
    TuiLoggingHandler,
    dump_crash_logs,
    get_log_file_path,
)
from plain2code_state import RunState
from system_config import system_config
from tui.plain2code_tui import Plain2CodeTUI

TEST_SCRIPT_EXECUTION_TIMEOUT = 120  # 120 seconds

DEFAULT_TEMPLATE_DIRS = importlib.resources.files("standard_template_library")

MAX_UNITTEST_FIX_ATTEMPTS = 20
MAX_CONFORMANCE_TEST_FIX_ATTEMPTS = 20
MAX_CONFORMANCE_TEST_RUNS = 20
MAX_REFACTORING_ITERATIONS = 5
MAX_UNIT_TEST_RENDER_RETRIES = 2

MAX_ISSUE_LENGTH = 10000  # Characters.

UNRECOVERABLE_ERROR_EXIT_CODES = [69]
TIMEOUT_ERROR_EXIT_CODE = 124


def get_render_range(render_range, plain_source):
    render_range = render_range.split(",")
    range_end = render_range[1] if len(render_range) == 2 else render_range[0]

    return _get_frids_range(plain_source, render_range[0], range_end)


def get_render_range_from(start, plain_source):
    return _get_frids_range(plain_source, start)


def _get_frids_range(plain_source, start, end=None):
    frids = list(plain_spec.get_frids(plain_source))

    start = str(start)

    if start not in frids:
        raise InvalidFridArgument(f"Invalid start functional requirement ID: {start}. Valid IDs are: {frids}.")

    if end is not None:
        end = str(end)
        if end not in frids:
            raise InvalidFridArgument(f"Invalid end functional requirement ID: {end}. Valid IDs are: {frids}.")

        end_idx = frids.index(end) + 1
    else:
        end_idx = len(frids)

    start_idx = frids.index(start)
    if start_idx >= end_idx:
        raise InvalidFridArgument(
            f"Start functional requirement ID: {start} must be before end functional requirement ID: {end}."
        )

    return frids[start_idx:end_idx]


def setup_logging(
    args,
    event_bus: EventBus,
    log_to_file: bool,
    log_file_name: str,
    plain_file_path: Optional[str],
    render_id: str,
):
    # Set default level to INFO for everything not explicitly configured
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("langsmith").setLevel(logging.WARNING)
    logging.getLogger("git").setLevel(logging.WARNING)
    logging.getLogger("anthropic._base_client").setLevel(logging.WARNING)
    logging.getLogger("services.langsmith.langsmith_service").setLevel(logging.WARNING)
    logging.getLogger("repositories").setLevel(logging.WARNING)
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

    # Allow detailed retry logs for anthropic if needed
    logging.getLogger("anthropic._base_client").setLevel(logging.DEBUG)
    if logging.getLogger("anthropic._base_client").level == logging.DEBUG:
        logging.getLogger("anthropic._base_client").addFilter(RetryOnlyFilter())

    # The IndentedFormatter provides better multiline log readability.
    # We add the TuiLoggingHandler to the root logger.
    # CRITICAL: We must remove existing handlers (like StreamHandler) to prevent double-logging
    # that spills into the TUI dashboard.
    root_logger = logging.getLogger()
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    handler = TuiLoggingHandler(event_bus)
    formatter = IndentedFormatter("%(levelname)s:%(name)s:%(message)s")
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    if log_to_file and log_file_path:
        try:
            file_handler = logging.FileHandler(log_file_path, mode="w")
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            console.warning(f"Failed to setup file logging to {log_file_path}: {str(e)}")
    else:
        # If file logging is disabled, use CrashLogHandler to buffer logs in memory
        # in case we need to dump them on crash.
        crash_handler = CrashLogHandler()
        crash_handler.setFormatter(formatter)
        root_logger.addHandler(crash_handler)

    root_logger.info(f"Render ID: {render_id}")  # Ensure render ID is logged in to codeplain.log file


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

    console.info(f"Rendering {args.filename} to target code.")

    # Compute render range from either --render-range or --render-from
    render_range = None
    if args.render_range or args.render_from:
        # Parse the plain file to get the plain_source for FRID extraction
        _, plain_source, _ = plain_file.plain_file_parser(args.filename, template_dirs)

        if args.render_range:
            render_range = get_render_range(args.render_range, plain_source)
        elif args.render_from:
            render_range = get_render_range_from(args.render_from, plain_source)

    codeplainAPI = codeplain_api.CodeplainAPI(args.api_key, console)
    codeplainAPI.verbose = args.verbose
    assert args.api is not None and args.api != "", "API URL is required"
    codeplainAPI.api_url = args.api

    _check_connection(codeplainAPI)

    module_renderer = ModuleRenderer(
        codeplainAPI,
        args.filename,
        render_range,
        template_dirs,
        args,
        run_state,
        event_bus,
    )

    app = Plain2CodeTUI(
        event_bus=event_bus,
        worker_fun=module_renderer.render_module,
        render_id=run_state.render_id,
        unittests_script=args.unittests_script,
        conformance_tests_script=args.conformance_tests_script,
        prepare_environment_script=args.prepare_environment_script,
        state_machine_version=system_config.client_version,
        css_path="styles.css",
    )
    result = app.run()

    # If the app exited due to a worker error, re-raise it here
    # so it hits the exception handlers in main()
    if isinstance(result, Exception):
        raise result

    return


def main():  # noqa: C901
    args = parse_arguments()

    event_bus = EventBus()

    if not args.api:
        args.api = "https://api.codeplain.ai"

    run_state = RunState(spec_filename=args.filename, replay_with=args.replay_with)

    setup_logging(args, event_bus, args.log_to_file, args.log_file_name, args.filename, run_state.render_id)

    exc_info = None
    try:
        # Validate API key is present
        if not args.api_key:
            raise MissingAPIKey(
                "API key is required. Please set the CODEPLAIN_API_KEY environment variable or provide it with the --api-key argument."
            )

        console.debug(f"Render ID: {run_state.render_id}")  # Ensure render ID is logged to the console
        render(args, run_state, event_bus)
    except InvalidFridArgument as e:
        exc_info = sys.exc_info()
        console.error(f"Invalid FRID argument: {str(e)}.\n")
    except FileNotFoundError as e:
        exc_info = sys.exc_info()
        console.error(f"File not found: {str(e)}\n")
        console.debug(f"Render ID: {run_state.render_id}")
    except TemplateNotFoundError as e:
        exc_info = sys.exc_info()
        console.error(f"Template not found: {str(e)}\n")
        console.error(system_config.get_error_message("template_not_found"))
    except PlainSyntaxError as e:
        exc_info = sys.exc_info()
        console.error(f"Plain syntax error: {str(e)}\n")
    except KeyboardInterrupt:
        exc_info = sys.exc_info()
        console.error("Keyboard interrupt")
        console.debug(f"Render ID: {run_state.render_id}")
    except RequestException as e:
        exc_info = sys.exc_info()
        console.error(f"Error rendering plain code: {str(e)}\n")
        console.debug(f"Render ID: {run_state.render_id}")
    except MissingPreviousFunctionalitiesError as e:
        exc_info = sys.exc_info()
        console.error(f"Error rendering plain code: {str(e)}\n")
        console.debug(f"Render ID: {run_state.render_id}")
    except MissingAPIKey as e:
        console.error(f"Missing API key: {str(e)}\n")
    except InvalidAPIKey as e:
        console.error(f"Invalid API key: {str(e)}\n")
    except OutdatedClientVersion as e:
        console.error(f"Outdated client version: {str(e)}\n")
    except (InternalServerError, InternalClientError):
        exc_info = sys.exc_info()
        console.error(
            f"Internal server error.\n\nPlease report the error to support@codeplain.ai with the attached {args.log_file_name} file."
        )
        console.debug(f"Render ID: {run_state.render_id}")
    except ConflictingRequirements as e:
        exc_info = sys.exc_info()
        console.error(f"Conflicting requirements: {str(e)}\n")
        console.debug(f"Render ID: {run_state.render_id}")
    except CreditBalanceTooLow as e:
        exc_info = sys.exc_info()
        console.error(f"Credit balance too low: {str(e)}\n")
        console.debug(f"Render ID: {run_state.render_id}")
    except LLMInternalError as e:
        exc_info = sys.exc_info()
        console.error(f"LLM internal error: {str(e)}\n")
        console.debug(f"Render ID: {run_state.render_id}")
    except MissingResource as e:
        exc_info = sys.exc_info()
        console.error(f"Missing resource: {str(e)}\n")
        console.debug(f"Render ID: {run_state.render_id}")
    except NetworkConnectionError as e:
        exc_info = sys.exc_info()
        console.error(f"Connection error: {str(e)}\n")
        console.error("Please check that your internet connection is working.")
        console.debug(f"Render ID: {run_state.render_id}")
    except Exception as e:
        exc_info = sys.exc_info()
        console.error(f"Error rendering plain code: {str(e)}\n")
        console.debug(f"Render ID: {run_state.render_id}")
    finally:
        if exc_info:
            # Log traceback using the logging system
            logging.error("Render crashed with exception:", exc_info=exc_info)
            dump_crash_logs(args)


if __name__ == "__main__":  # noqa: C901
    main()
