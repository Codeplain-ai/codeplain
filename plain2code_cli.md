# Plain2Code CLI Reference

```text
[1;34musage: [0m[1;35mgenerate_cli.py[0m [[32m-h[0m] [[36m--verbose[0m] [[36m--base-folder [33mBASE_FOLDER[0m] [[36m--build-folder [33mBUILD_FOLDER[0m] [[36m--log-to-file | --no-log-to-file[0m] [[36m--log-file-name [33mLOG_FILE_NAME[0m]
                       [[36m--config-name [33mCONFIG_NAME[0m] [[36m--render-range [33mRENDER_RANGE[0m | [36m--render-from [33mRENDER_FROM[0m] [[36m--force-render[0m] [[36m--unittests-script [33mUNITTESTS_SCRIPT[0m]
                       [[36m--conformance-tests-folder [33mCONFORMANCE_TESTS_FOLDER[0m] [[36m--conformance-tests-script [33mCONFORMANCE_TESTS_SCRIPT[0m]
                       [[36m--prepare-environment-script [33mPREPARE_ENVIRONMENT_SCRIPT[0m] [[36m--test-script-timeout [33mTEST_SCRIPT_TIMEOUT[0m] [[36m--api [33m[API][0m] [[36m--api-key [33mAPI_KEY[0m] [[36m--full-plain[0m] [[36m--dry-run[0m]
                       [[36m--replay-with [33mREPLAY_WITH[0m] [[36m--template-dir [33mTEMPLATE_DIR[0m] [[36m--copy-build[0m] [[36m--build-dest [33mBUILD_DEST[0m] [[36m--copy-conformance-tests[0m]
                       [[36m--conformance-tests-dest [33mCONFORMANCE_TESTS_DEST[0m] [[36m--render-machine-graph[0m] [[36m--logging-config-path [33mLOGGING_CONFIG_PATH[0m] [[36m--headless[0m] [[36m--status[0m] [[36m--version[0m]
                       [32m[filename][0m

Render plain code to target code. Path arguments resolve based on where they were written: values given on the command line are resolved against the current working directory, values
read from config.yaml are resolved against the config file's directory, and defaults are resolved against the directory containing the plain file. Absolute paths (and paths starting
with '~') are used as-is.

[1;34mpositional arguments:[0m
  [1;32mfilename[0m              Path to the plain file to render. The directory containing this file has highest precedence for template loading, so you can place custom templates here to
                        override the defaults. See --template-dir for more details about template loading.

[1;34moptions:[0m
  [1;32m-h[0m, [1;36m--help[0m            show this help message and exit
  [1;36m--verbose[0m, [1;32m-v[0m         Set default log level to DEBUG for TUI and file logs
  [1;36m--base-folder[0m [1;33mBASE_FOLDER[0m
                        Base folder for the build files
  [1;36m--build-folder[0m [1;33mBUILD_FOLDER[0m
                        Folder for build files
  [1;36m--log-to-file[0m, [1;36m--no-log-to-file[0m
                        Enable logging to a file. Defaults to True. Set to False to disable.
  [1;36m--log-file-name[0m [1;33mLOG_FILE_NAME[0m
                        Name of the log file. Defaults to 'codeplain.log'. If a file already exists at the resolved path, it will be overwritten by the current logs.
  [1;36m--render-range[0m [1;33mRENDER_RANGE[0m
                        Specify a range of functionalities to render (e.g. `1` , `2`, `3`). Use comma to separate start and end IDs. If only one functionality ID is provided, only that
                        functionality is rendered. Range is inclusive of both start and end IDs.
  [1;36m--render-from[0m [1;33mRENDER_FROM[0m
                        Continue generation starting from this specific functionality (e.g. `2`). The functionality with this ID will be included in the output. The functionality ID
                        must match one of the functionalities in your plain file.
  [1;36m--force-render[0m        Force re-render of all the required modules.
  [1;36m--unittests-script[0m [1;33mUNITTESTS_SCRIPT[0m
                        Shell script to run unit tests on generated code. Receives the build folder path as its first argument (default: 'plain_modules').
  [1;36m--conformance-tests-folder[0m [1;33mCONFORMANCE_TESTS_FOLDER[0m
                        Folder for conformance test files
  [1;36m--conformance-tests-script[0m [1;33mCONFORMANCE_TESTS_SCRIPT[0m
                        Path to conformance tests shell script. Every conformance test script should accept two arguments: 1) Path to a folder (e.g. `plain_modules/module_name`)
                        containing generated source code, 2) Path to a subfolder of the conformance tests folder (e.g. `conformance_tests/subfoldername`) containing test files.
  [1;36m--prepare-environment-script[0m [1;33mPREPARE_ENVIRONMENT_SCRIPT[0m
                        Path to a shell script that prepares the testing environment. The script should accept the source code folder path as its first argument.
  [1;36m--test-script-timeout[0m [1;33mTEST_SCRIPT_TIMEOUT[0m
                        Timeout for test scripts in seconds. If not provided, the default timeout of 120 seconds is used.
  [1;36m--api[0m [1;33m[API][0m           Alternative base URL for the API. Default: `https://api.codeplain.ai`
  [1;36m--api-key[0m [1;33mAPI_KEY[0m     API key used to access the API. If not provided, the `CODEPLAIN_API_KEY` environment variable is used.
  [1;36m--full-plain[0m          Full preview ***plain specification before code generation. Use when you want to preview context of all ***plain primitives that are going to be included in
                        order to render the given module.
  [1;36m--dry-run[0m             Dry run preview of the code generation (without actually making any changes).
  [1;36m--replay-with[0m [1;33mREPLAY_WITH[0m
  [1;36m--template-dir[0m [1;33mTEMPLATE_DIR[0m
                        Path to a custom template directory. Templates are searched in the following order: 1) Directory containing the plain file, 2) Custom template directory (if
                        provided through this argument), 3) Built-in standard_template_library directory
  [1;36m--copy-build[0m          If set, copy the rendered contents of code in `--base-folder` folder to `--build-dest` folder after successful rendering.
  [1;36m--build-dest[0m [1;33mBUILD_DEST[0m
                        Target folder to copy rendered contents of code to (used only if --copy-build is set).
  [1;36m--copy-conformance-tests[0m
                        If set, copy the conformance tests of code in `--conformance-tests-folder` folder to `--conformance-tests-dest` folder successful rendering. Requires
                        --conformance-tests-script.
  [1;36m--conformance-tests-dest[0m [1;33mCONFORMANCE_TESTS_DEST[0m
                        Target folder to copy conformance tests of code to (used only if --copy-conformance-tests is set).
  [1;36m--render-machine-graph[0m
                        If set, render the state machine graph.
  [1;36m--logging-config-path[0m [1;33mLOGGING_CONFIG_PATH[0m
                        Path to the logging configuration file.
  [1;36m--headless[0m            Run in headless mode: no TUI, no terminal output except a single render-started message. All logs are written to the log file.
  [1;36m--status[0m              Display account status including user information, API key label, and rendering credits. Does not render any code.
  [1;36m--version[0m             Display the client version and exit.

[1;34mconfiguration:[0m
  [1;36m--config-name[0m [1;33mCONFIG_NAME[0m
                        Name of the config file to look for. Looked up in the plain file directory and the current working directory. Defaults to config.yaml.

```