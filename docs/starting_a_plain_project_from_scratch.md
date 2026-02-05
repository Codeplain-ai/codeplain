# Starting a New ***plain Project from Scratch

This guide will walk you through creating your first ***plain project from scratch. It assumes you have already:

✅ Installed `codeplain` CLI by following the installation guide on [*codeplain website](https://www.codeplain.ai/),
✅ Successfully rendered hello world example by following walkthrough guide while installing the `codeplain` CLI.

After following this guide, you'll be equipped to turn your ideas into working code with ***plain.

## Project Structure Overview

Let's say you're building a CLI app where you want to sort an array of integers in Python. We'll call this project `array_sorter`.

Every ***plain project follows this basic structure:

```
array_sorter/
├── array_sorter.plain                  # Your application specification
├── config.yaml                         # CLI configuration
├── run_unittests_python.sh             # Python unit test script
├── run_conformance_tests_python.sh     # Python conformance test script
├── plain_modules/                      # Rendered plain modules
└── conformance_tests/                  # Rendered conformance tests
```

In this guide we will cover how to create each of these step by step.

## 1. Define Your .plain File

Create a `.plain` file. The following example shows how to specify the array sorting problem. For more details, see [***plain language specifications](plain_language_specification.md).

`array_sorting.plain`

```plain
---
description: 'Sort an array of integers in Python'
import:
  - python-console-app-template
---

***definitions***

- :Array: is an array of integers received as input.

***implementation reqs***

- :App: should use merge sort algorithm to sort :Array:.

- :App: should receive :Array: as input as positional argument.

***functional specs***

- Sort :Array: in ascending order and display it through stdout.

    ***Acceptance Tests:***

    - When given input "5 2 8 1 9", The App should output "1 2 5 8 9"

    - When given input "1 2 3 4 5", The App should output "1 2 3 4 5"
```

In the example above, we started off from the module `python-console-app-template`. You can find predefined modules in the [standard template library](https://github.com/Codeplain-ai/codeplain/tree/main/standard_template_library).


## 2. Add Test Scripts

Include the appropriate test scripts to your project. If scripts are not needed to be customized, you can use the [predefined ones](https://github.com/Codeplain-ai/codeplain/tree/main/standard_template_library).

```bash
cp /path/to/plain2code_client/test_scripts/run_unittests_python.sh ./
cp /path/to/plain2code_client/test_scripts/run_conformance_tests_python.sh ./
```
- You may need to modify these scripts based on your specific project requirements.

## 3. Configure Parameters

Create a `config.yaml` (default name, which you can change with `--config-name` argument in the file) file to configure the plain2code CLI parameters.

Example of a basic `config.yaml` file:

```yaml

unittests-script: ./run_unittests_python.sh
conformance-tests-script: ./run_conformance_tests_python.sh
verbose: true

```
- Specify the test scripts so that ***plain knows how to run unit and conformance tests.
- Indicate whether to display detailed output during code generation like shown in output control. 
- For additional options and advanced configuration, see the [plain2code CLI documentation](plain2code_cli.md).

## 4. Generate & Run Your Project

```bash
python ../plain2code_client/plain2code.py my_app.plain
```
- Generated code will appear in build/ and conformance_tests/.


## 5. Notes
- `build/` and `conformance_tests/` folders are generated automatically
- These folders are excluded from git via `.gitignore`
- `dist/` and `dist_conformance_tests/` are created if you set `copy-build: true` and `copy-conformance-tests: true` in your config.yaml
- Always review generated code before using in production
- The `.plain` file is your source of truth - keep it well-documented and version-controlled
