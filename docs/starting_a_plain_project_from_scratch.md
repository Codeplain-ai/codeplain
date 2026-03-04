# How to Start a New ***plain Project from Scratch

This guide will walk you through creating your first ***plain project from scratch.
It assumes you have already:

✅ Met all [prerequisites](../README.md#prerequisites),
✅ Completed the [installation steps](../README.md/#installation-steps),
✅ Successfully rendered your [first example](../README.md#quick-start).

If you haven't done so yet, please refer to [README](../README.md).

After following this guide, you'll be equipped to turn your ideas into working code with ***plain.

## Project Structure Overview

Every ***plain project follows this basic structure:

```
my-new-project/
├── my_app.plain                        # Your application specification
├── config.yaml                         # CLI configuration
├── run_unittests_[language].sh         # Unit test script
├── run_conformance_tests_[language].sh # Conformance test script
├── build/                              # Generated
└── conformance_tests/                  # Generated
```

In this guide we will cover how to create each of these step by step.

## 1. Define Your .plain File

Create a `.plain` file. The following example shows how to specify the array sorting problem. For more details, see [***plain language specifications](https://www.plainlang.org/docs/intro/).

**Example: `array_sorting.plain`**
```plain
{% include "python-console-app-template.plain", main_executable_file_name: "array_sorting.py" %}

***Definitions:***
- The Array is an array of integers received as input.

***Functional Requirements:***
- The App should be extended to receive The Array
- Sort The Array.
- Display The Array.

    ***Acceptance Tests:***
    - When given input "5 2 8 1 9", The App should output "1 2 5 8 9"
    - When given input "1 2 3 4 5", The App should output "1 2 3 4 5"

```

- When including templates, use `--full-plain` flag to preview the complete specification including all template content before rendering. You can find predefined templates in [standard template library](../standard_template_library/). (This flag can be configured in your config file.)

## 2. Add Test Scripts

Include the appropriate test scripts to your project:

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
