# Plain2Code CLI Reference

```text
usage: generate_cli.py [-h] [--verbose] [--base-folder BASE_FOLDER] [--build-folder BUILD_FOLDER] [--log-to-file | --no-log-to-file] [--log-file-name LOG_FILE_NAME] [--config-name CONFIG_NAME]
                       [--render-range RENDER_RANGE | --render-from RENDER_FROM] [--force-render] [--unittests-script UNITTESTS_SCRIPT] [--conformance-tests-folder CONFORMANCE_TESTS_FOLDER]
                       [--conformance-tests-script CONFORMANCE_TESTS_SCRIPT] [--prepare-environment-script PREPARE_ENVIRONMENT_SCRIPT] [--test-script-timeout TEST_SCRIPT_TIMEOUT] [--api [API]] [--api-key API_KEY]
                       [--full-plain] [--dry-run] [--replay-with REPLAY_WITH] [--template-dir TEMPLATE_DIR] [--copy-build] [--build-dest BUILD_DEST] [--copy-conformance-tests]
                       [--conformance-tests-dest CONFORMANCE_TESTS_DEST] [--render-machine-graph] [--logging-config-path] [--headless]
                       filename

Render plain code to target code.

positional arguments:
  filename              Path to the plain file to render. The directory containing this file has highest precedence for template loading, so you can place custom templates here to override the defaults. See --template-dir
                        for more details about template loading.

options:
  -h, --help            show this help message and exit
  --verbose, -v         Enable verbose output
  --base-folder BASE_FOLDER
                        Base folder for the build files
  --build-folder BUILD_FOLDER
                        Folder for build files
  --log-to-file, --no-log-to-file
                        Enable logging to a file. Defaults to True. Set to False to disable.
  --log-file-name LOG_FILE_NAME
                        Name of the log file. Defaults to 'codeplain.log'.Always resolved relative to the plain file directory.If file on this path already exists, the already existing log file will be overwritten by the
                        current logs.
  --render-range RENDER_RANGE
                        Specify a range of functional requirements to render (e.g. `1` , `2`, `3`). Use comma to separate start and end IDs. If only one ID is provided, only that requirement is rendered. Range is
                        inclusive of both start and end IDs.
  --render-from RENDER_FROM
                        Continue generation starting from this specific functional requirement (e.g. `2`). The requirement with this ID will be included in the output. The ID must match one of the functional requirements
                        in your plain file.
  --force-render        Force re-render of all the required modules.
  --unittests-script UNITTESTS_SCRIPT
                        Shell script to run unit tests on generated code. Receives the build folder path as its first argument (default: 'plain_modules').
  --conformance-tests-folder CONFORMANCE_TESTS_FOLDER
                        Folder for conformance test files
  --conformance-tests-script CONFORMANCE_TESTS_SCRIPT
                        Path to conformance tests shell script. Every conformance test script should accept two arguments: 1) Path to a folder (e.g. `plain_modules/module_name`) containing generated source code, 2) Path
                        to a subfolder of the conformance tests folder (e.g. `conformance_tests/subfoldername`) containing test files.
  --prepare-environment-script PREPARE_ENVIRONMENT_SCRIPT
                        Path to a shell script that prepares the testing environment. The script should accept the source code folder path as its first argument.
  --test-script-timeout TEST_SCRIPT_TIMEOUT
                        Timeout for test scripts in seconds. If not provided, the default timeout of 120 seconds is used.
  --api [API]           Alternative base URL for the API. Default: `https://api.codeplain.ai`
  --api-key API_KEY     API key used to access the API. If not provided, the `CODEPLAIN_API_KEY` environment variable is used.
  --full-plain          Full preview ***plain specification before code generation.Use when you want to preview context of all ***plain primitives that are going to be included in order to render the given module.
  --dry-run             Dry run preview of the code generation (without actually making any changes).
  --replay-with REPLAY_WITH
  --template-dir TEMPLATE_DIR
                        Path to a custom template directory. Templates are searched in the following order: 1) Directory containing the plain file, 2) Custom template directory (if provided through this argument), 3)
                        Built-in standard_template_library directory
  --copy-build          If set, copy the rendered contents of code in `--base-folder` folder to `--build-dest` folder after successful rendering.
  --build-dest BUILD_DEST
                        Target folder to copy rendered contents of code to (used only if --copy-build is set).
  --copy-conformance-tests
                        If set, copy the conformance tests of code in `--conformance-tests-folder` folder to `--conformance-tests-dest` folder successful rendering. Requires --conformance-tests-script.
  --conformance-tests-dest CONFORMANCE_TESTS_DEST
                        Target folder to copy conformance tests of code to (used only if --copy-conformance-tests is set).
  --render-machine-graph
                        If set, render the state machine graph.
  --logging-config-path
                        Path to the logging configuration file.
  --headless            Run in headless mode: no TUI, no terminal output except a single render-started message. All logs are written to the log file.

configuration:
  --config-name CONFIG_NAME
                        Path to the config file, defaults to config.yaml

```