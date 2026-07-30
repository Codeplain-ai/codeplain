# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is **codeplain**, a Python CLI tool that renders code from `***plain` specification files using the Codeplain API. The tool acts as a client that:
1. Parses `***plain` spec files (a domain-specific language for software specifications)
2. Sends specs to the Codeplain API (LLM-based code generation service)
3. Manages an iterative state-machine-driven render process with testing and refactoring cycles
4. Outputs generated code to a build folder

The codebase serves two audiences:
- **End users**: developers who write `***plain` specs and run `plain2code.py` to generate code
- **Internal devs**: maintaining the renderer itself (this codebase)

### Related Repository: plain2code_rest_api

This repository has a sibling repository **`plain2code_rest_api`** which contains the backend API service that this client communicates with. The two repositories are typically cloned as siblings:

```
parent-directory/
├── codeplain/              # This repository (client)
└── plain2code_rest_api/    # Backend API service
```

**When to work across both repositories:**
- API contract changes (request/response formats)
- Adding new endpoints or modifying existing ones
- Debugging communication issues between client and server
- End-to-end feature development that requires both client and server changes

When working on features that span both repositories, coordinate changes carefully to maintain backward compatibility or plan coordinated deployments.

## Development Setup

### Prerequisites
- Python 3.11+
- `CODEPLAIN_API_KEY` environment variable set

### Installation
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Git Hooks
A pre-push hook runs automatically on `git push` and executes:
- Black formatting check
- isort import sorting check  
- Flake8 linting
- Mypy type checking
- Full test suite with coverage

Hook is located at `.git/hooks/pre-push`.

## Common Commands

### Running the Tool
```bash
# Basic usage - render a .plain file
python plain2code.py path/to/file.plain

# Display account status (user info, credits, API key label)
python plain2code.py --status

# Dry run (parse and validate .plain files without rendering code)
# This parses the spec files, resolves imports/requires, but doesn't generate code
python plain2code.py path/to/file.plain --dry-run

# Enable file logging (writes to codeplain.log in same dir as .plain file)
python plain2code.py path/to/file.plain --log-to-file

# Headless mode (no TUI, logs to file only)
python plain2code.py path/to/file.plain --headless

# Render specific functionality range
python plain2code.py file.plain --render-range 1,3  # Render functionalities 1-3
python plain2code.py file.plain --render-from 2     # Resume from functionality 2

# Run with examples
cd examples/example_hello_world_python
python ../../plain2code.py hello_world_python.plain
cd build && python hello_world.py
```

### Testing
```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_plain_modules.py -v

# Run specific test
pytest tests/test_plain_modules.py::test_get_next_frid_within_same_module -v

# Run with coverage
coverage run -m pytest tests/ -v
coverage report
coverage html  # Generates htmlcov/index.html
```

### Code Quality
```bash
# Format code
black .

# Sort imports
isort .

# Lint
flake8 .

# Type check
mypy . --check-untyped-defs

# Run all checks (same as pre-push hook)
black . --check && isort . --check-only && flake8 . && mypy . --check-untyped-defs && coverage run -m pytest tests/ -v && coverage report
```

## Architecture

### High-Level Flow
1. **Entry Point** (`plain2code.py`): CLI argument parsing, logging setup, TUI initialization
2. **Module Loading** (`plain_modules.py`, `plain_file.py`): Parse `.plain` files into `PlainModule` objects with dependency trees
3. **Rendering Orchestration** (`module_renderer.py`): Coordinates render process across modules
4. **State Machine** (`render_machine/`): Hierarchical state machine drives the render lifecycle
5. **API Communication** (`codeplain_REST_api.py`): HTTP client for Codeplain API
6. **Code Generation** (`render_machine/code_renderer.py`): Main code renderer class that orchestrates the code generation workflow using a hierarchical state machine
7. **TUI** (`tui/`): Textual-based terminal UI showing render progress
8. **CLI Output** (`cli_output/`): Non-interactive terminal output formatting for status, dry-run, and render summaries

### Key Concepts

**Plain Modules**: A `.plain` file can `import` other `.plain` files, creating a module dependency tree. The renderer processes required modules first (depth-first), then the top module.

**Functionalities (FRIDs)**: Each functional requirement in a `.plain` file has a unique ID (FRID). The renderer processes them sequentially. State is checkpointed after each FRID.

**Render State Machine** (`render_machine/states.py`): Hierarchical FSM with states like:
- `IMPLEMENTING_FRID` → `PROCESSING_UNIT_TESTS` → `REFACTORING_CODE` → `PROCESSING_CONFORMANCE_TESTS`
- Transitions handled by triggers in `render_machine/triggers.py`
- Config in `render_machine/state_machine_config.py`

**Memory Management** (`memory_management.py`): Tracks previously rendered code to provide context to the LLM for incremental changes. Uses git commits to persist checkpoints.

**Partial Rendering** (`partial_rendering.py`): Detects when specs or code changed and determines the optimal starting point to resume rendering (avoids full re-renders).

**Event Bus** (`event_bus.py`): Pub/sub for UI updates. Events defined in `plain2code_events.py` (e.g., `LogMessageEmitted`, `RenderCompleted`).

### Directory Structure

- `plain2code.py` - Main CLI entry point
- `plain2code_arguments.py` - CLI argument parsing and validation
- `plain2code_utils.py` - Pure utility functions (duration formatting, ambiguity messages)
- `plain_modules.py` - Module dependency resolution
- `plain_file.py` - `.plain` file parser
- `plain_spec.py` - Spec extraction and FRID utilities
- `module_renderer.py` - Orchestrates render for a module
- `render_machine/` - State machine implementation
  - `code_renderer.py` - Generates code via templates
  - `states.py` - State name constants
  - `triggers.py` - State transition logic
  - `render_context.py` - Shared context across states
  - `conformance_tests.py` - Test generation and execution
- `tui/` - Interactive terminal UI (Textual framework)
  - `plain2code_tui.py` - Main TUI app
  - `components.py` - Custom UI widgets
- `cli_output/` - Non-interactive CLI output formatting
  - `status.py` - Status display (`--status` flag)
  - `dry_run.py` - Dry run output (`--dry-run` flag)
  - `render_summary.py` - Render completion summary
- `codeplain_REST_api.py` - API client
- `memory_management.py` - Context tracking
- `git_utils.py` - Git operations for checkpointing
- `file_utils.py` - File I/O utilities
- `concept_utils.py` - Concept validation (***plain language feature)
- `plain2code_logger.py` - Custom logging with elapsed time timestamps
- `plain2code_console.py` - Rich console wrapper with custom styles
- `plain2code_state.py` - Runtime state (render ID, counters, timing)
- `standard_template_library/` - Built-in `.plain` templates (git subtree from `plainlang-examples`)
- `examples/` - Sample `.plain` projects

### Testing Architecture

Tests are in `tests/` and cover:
- `test_plain_modules.py` - Module loading, dependency resolution, metadata
- `test_plainfileparser.py` - `.plain` syntax parsing
- `test_partial_rendering.py` - Render resumption logic
- `test_git_utils.py` - Git checkpoint/restore operations
- `test_imports.py`, `test_requires.py` - Module dependency resolution

No integration tests hit the actual Codeplain API (would require API key + network). Testing focuses on parser, state management, and file operations.

### Logging

Two logging modes:
1. **Console (TUI)**: Logs displayed in TUI with relative timestamps `[HH:MM:SS]` (elapsed since render start, accounts for pauses)
2. **File**: Same format as TUI, written to `codeplain.log` in the `.plain` file's directory

`ElapsedTimeFormatter` (in `plain2code_logger.py`) uses `RunState` to calculate elapsed time. Format: `[HH:MM:SS] LEVEL logger_name: message`

## Important Notes

### Plain Specification Language
- `***plain` is a Markdown-like DSL for software specs
- Docs at https://www.plainlang.org/docs/
- Parser handles sections: definitions, functional specs, implementation reqs, test reqs, acceptance tests
- Concepts (domain terms) validated across modules via `concept_utils.py`

### Code Generation Process
- Templates in `standard_template_library/` use Liquid2 syntax
- Template search order: (1) `.plain` file dir, (2) `--template-dir`, (3) built-in standard lib
- Each module renders into its own tree under the build folder (configurable via `--build-folder`, default `plain_modules/`):
  - `<build-folder>/<module>/code/` — implementation code (its own git repo)
  - `<build-folder>/<module>/tests/` — conformance tests (its own git repo; only created when a conformance tests script is configured)
  - `<build-folder>/<module>/.codeplain/` — module metadata (`module_metadata.json`), not tracked in git
  - `<build-folder>/<module>/.memory/` — conformance test memory, not tracked in git
- Each FRID render produces a git commit in the `code/` and `tests/` repos for rollback capability

### Configuration
- `config.yaml` can be placed in `.plain` file dir or CWD
- Specifies test scripts, template dirs, build paths
- CLI args override config file values

### Running Plain2Code from Another Directory
The tool can be run from any directory by providing an absolute or relative path to the `.plain` file. All paths (build folder, log file, templates) are resolved relative to the `.plain` file's directory, not the current working directory, unless explicitly overridden via CLI arguments.

### Standard Template Library

`standard_template_library/` is a git subtree sourced from the `plainlang-examples` repository. It is **not** updated automatically — you must pull changes manually when the templates change upstream:

```bash
git subtree pull --prefix=standard_template_library git@github.com:Codeplain-ai/plainlang-examples.git subtree/standard-template-library --squash
```

The subtree does not track a branch pointer. The branch (`main` above) is just an argument passed at pull time — git uses the `git-subtree-split` hash embedded in the commit history to determine what's new since the last sync.

**Important:** You must pull from the `subtree/standard-template-library` branch, not `main`. This is a split branch containing only the `standard_template_library/` subdirectory contents at root level. Pulling from `main` would bring in the entire `plainlang-examples` repo.

If the split branch is outdated, regenerate it first in `plainlang-examples`:

```bash
# In plainlang-examples/
git subtree split --prefix=standard_template_library -b subtree/standard-template-library
git push origin subtree/standard-template-library
```

### Windows Support
Windows users must use WSL (Windows Subsystem for Linux). The codebase has some platform-specific script handling (`.ps1` for Windows, `.sh` for Unix).

### CRITICAL: No User-Specific Paths in Version Control
**Never commit files containing user-specific absolute paths** (e.g., `/Users/username/...`, `/home/username/...`, `C:\Users\...`) to version-controlled files like:
- `.claude/settings.json` - Use relative paths (`.venv/bin/python`) instead of absolute paths
- `config.yaml` or any config files
- Any documentation or scripts

User-specific paths should only exist in:
- `.claude/settings.local.json` (gitignored)
- Personal `.env` files (gitignored)
- User's global `~/.claude/settings.json`
