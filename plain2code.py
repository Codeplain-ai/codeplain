import importlib.resources
import logging
import logging.config
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

import yaml
from liquid2.exceptions import TemplateNotFoundError

import codeplain_REST_api as codeplain_api
import file_utils
import plain_file
import plain_modules
import plain_spec
from cli_output import print_dry_run_output, print_exit_summary, print_status
from event_bus import EventBus
from module_renderer import ModuleRenderer
from partial_rendering import get_plain_module_render_state, get_render_choices
from plain2code_arguments import parse_arguments
from plain2code_console import console
from plain2code_events import RenderFailed
from plain2code_exceptions import (
    ConflictingRequirements,
    InvalidAPIKey,
    InvalidFridArgument,
    MissingAPIKey,
    MissingPreviousFunctionalitiesError,
    MissingResource,
    ModuleDoesNotExistError,
    NetworkConnectionError,
    OutdatedClientVersion,
    PlainSyntaxError,
    RenderCancelledError,
    RenderingCreditBalanceTooLow,
    UnsupportedResourceType,
)
from plain2code_logger import (
    LOGGER_NAME,
    CrashLogHandler,
    ElapsedTimeFormatter,
    IndentedFormatter,
    LoggingHandler,
    dump_crash_logs,
    get_log_file_path,
)
from plain2code_state import RunState
from system_config import system_config
from tui.plain2code_tui import Plain2CodeTUI
from tui.plain_module_render_choice_tui import PlainModuleRenderChoiceTUI

DEFAULT_TEMPLATE_DIRS = importlib.resources.files("standard_template_library")
RENDER_THREAD_SHUTDOWN_TIMEOUT = 0.7


def setup_logging(
    args,
    event_bus: EventBus,
    run_state: RunState,
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
    # We add the LoggingHandler to the root logger.
    root_logger = logging.getLogger(LOGGER_NAME)
    configured_log_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)  # Capture all logs; handlers will filter levels as needed

    formatter = IndentedFormatter("%(levelname)s:%(name)s:%(message)s")
    file_formatter = ElapsedTimeFormatter(run_state)

    if not headless:
        handler = LoggingHandler(event_bus, run_state)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    if log_to_file and log_file_path:
        try:
            file_handler = logging.FileHandler(log_file_path, mode="w", encoding="utf-8")
            file_handler.setFormatter(file_formatter)
            file_handler.setLevel(configured_log_level)
            root_logger.addHandler(file_handler)
        except Exception as e:
            console.warning(f"Failed to setup file logging to {log_file_path}: {str(e)}")
    else:
        # If file logging is disabled, use CrashLogHandler to buffer logs in memory
        # in case we need to dump them on crash.
        crash_handler = CrashLogHandler()
        crash_handler.setFormatter(file_formatter)
        crash_handler.setLevel(configured_log_level)
        root_logger.addHandler(crash_handler)


def _check_connection(codeplainAPI: codeplain_api.CodeplainAPI):
    """Check API connectivity and validate API key and client version."""
    response = codeplainAPI.connection_check(system_config.client_version)

    if not response.get("api_key_valid", False):
        raise InvalidAPIKey(
            "The provided API key is invalid. Please provide a valid API key using the CODEPLAIN_API_KEY environment variable "
            "or the --api-key argument.\n"
        )

    if not response.get("client_version_valid", False):
        min_version = response.get("min_client_version", "unknown")
        raise OutdatedClientVersion(
            "Outdated client version: "
            f"Your client version ({system_config.client_version}) is outdated. Minimum required version is {min_version}. "
            "Please update using: uv tool upgrade codeplain"
        )


def render(args, run_state: RunState, event_bus: EventBus):  # noqa: C901
    template_dirs = file_utils.get_template_directories(args.filename, args.template_dir, DEFAULT_TEMPLATE_DIRS)

    # Compute render range from either --render-range or --render-from
    render_range = None
    if args.render_range or args.render_from:
        # Parse the plain file to get the plain_source for FRID extraction
        _, plain_source, _ = plain_file.plain_file_parser(args.filename, template_dirs)
        render_range = plain_spec.compute_render_range(args, plain_source)

    codeplainAPI = codeplain_api.CodeplainAPI(args.api_key, console)
    codeplainAPI.verbose = args.verbose
    assert args.api is not None and args.api != "", "API URL is required"
    codeplainAPI.api_url = args.api

    _check_connection(codeplainAPI)

    stop_event = threading.Event()
    enter_pause_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())

    plain_module = plain_modules.PlainModule(
        args.filename,
        args.build_folder,
        args.conformance_tests_folder,
        template_dirs,
    )

    render_choice = None
    if render_range is None:
        plain_module_render_state = get_plain_module_render_state(plain_module)
        if plain_module_render_state is not None:
            render_choices = get_render_choices(plain_module, plain_module_render_state, args.force_render)
            ask_user = True
            render_choice = None
            if len(render_choices) <= 2:
                # Last choice is Quit, first choice is the only other actionable choice
                render_choice = render_choices[list(render_choices.keys())[0]]
                ask_user = render_choice.is_destructive

            if ask_user and not args.headless:
                app = PlainModuleRenderChoiceTUI(
                    plain_module,
                    plain_module_render_state,
                    render_choices,
                    system_config.client_version,
                    run_state.render_id,
                    css_path="styles.css",
                )
                render_choice = app.run()
                if render_choice is None or (
                    render_choice.module is None
                    and render_choice.render_range is None
                    and render_choice.choice_type == "quit"
                ):
                    sys.exit(0)
            elif ask_user and args.headless:
                # ignore the default choice if it requires user input due to headless mode
                # fallback to --render-from
                # default choice is only used only when the action is not destructive
                render_choice = None

    if render_choice is not None and render_range is not None:
        raise Exception("Partial rendering and render range cannot be used together")

    module_renderer = ModuleRenderer(
        codeplainAPI,
        plain_module,
        render_choice,
        render_range,
        args,
        run_state,
        event_bus,
        stop_event=stop_event,
        enter_pause_event=enter_pause_event,
    )

    render_error: list[Exception] = []

    def run_render():
        try:
            module_renderer.render_module()
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
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_arguments()

    # Handle --version flag before any other initialization
    if args.version:
        console.print(f"codeplain version {system_config.client_version}")
        return

    # Handle --status flag before any other initialization
    if args.status:
        if not args.api_key:
            console.error(
                "Your API key is required. Please set the CODEPLAIN_API_KEY environment variable or provide it with the --api-key argument.\n"
            )
            return

        if not args.api:
            args.api = "https://api.codeplain.ai"

        try:
            print_status(args.api_key, args.api, system_config.client_version)
        except Exception as e:
            console.error(f"Error fetching status: {str(e)}")
        return

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
                render_range = plain_spec.compute_render_range(args, plain_source_tree)
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

    setup_logging(args, event_bus, run_state, args.log_to_file, args.log_file_name, args.filename, args.headless)

    exc_info = None
    error_message = None

    try:
        # Validate API key is present
        if not args.api_key:
            raise MissingAPIKey(
                "Your API key is required. Please set the CODEPLAIN_API_KEY environment variable or provide it with the --api-key argument.\n"
            )
        render(args, run_state, event_bus)
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt):
            error_message = "Keyboard interrupt"
        else:
            error_message = str(e) if str(e) else repr(e)

            if not isinstance(
                e,
                (
                    InvalidFridArgument,
                    FileNotFoundError,
                    MissingResource,
                    TemplateNotFoundError,
                    PlainSyntaxError,
                    MissingPreviousFunctionalitiesError,
                    MissingAPIKey,
                    InvalidAPIKey,
                    OutdatedClientVersion,
                    ConflictingRequirements,
                    RenderingCreditBalanceTooLow,
                    NetworkConnectionError,
                    ModuleDoesNotExistError,
                    UnsupportedResourceType,
                ),
            ):
                exc_info = sys.exc_info()
    finally:
        print_exit_summary(
            run_state,
            args.filename,
            error_message=error_message,
        )
        if exc_info:
            # Log traceback
            dump_crash_logs(args, run_state)

    if args.headless and (exc_info is not None or not run_state.render_succeeded):
        sys.exit(1)


if __name__ == "__main__":  # noqa: C901
    main()
