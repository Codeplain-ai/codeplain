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
├── build/                              # Generated final code
├── plain_modules/                      # Generated modules code
└── conformance_tests/                  # Generated conformanece tests code
```

In this guide we will cover how to create each of these step by step.

## 1. Define Your .plain File

Create a `.plain` file. The following example shows how to specify the array sorting problem. For more details, see [***plain language specifications](https://www.plainlang.org/docs/).

**Example: `array_sorting.plain`**
```plain
---
description: 'Example showing how to specify the array sorting problem'
import:
  - python-console-app-template
---

***definitions***
- :Array: is an array of integers received as input.

***functional specs***
- :App: should be extended to receive :Array:
- Sort :Array:
- Display :Array:

    ***acceptance tests***
    - When given input "5 2 8 1 9", :App: should output "1 2 5 8 9"
    - When given input "1 2 3 4 5", :App: should output "1 2 3 4 5"

```

`python-console-app-template` is predefined template providing specification of a typical Python console application. Check [standard template library](../standard_template_library/) for available predefined templates.

## 2. Add Test Scripts

Include the appropriate test scripts to your project:

```bash
cp /path/to/plain2code_client/test_scripts/run_unittests_python.sh ./
cp /path/to/plain2code_client/test_scripts/run_conformance_tests_python.sh ./
```
You may need to modify these scripts based on your specific project requirements.

## 3. Configure Parameters

Create a `config.yaml` (default name, which you can change with `--config-name` argument in the file) file to configure the plain2code CLI parameters.

Example of a basic `config.yaml` file:

```yaml

unittests-script: ./run_unittests_python.sh
conformance-tests-script: ./run_conformance_tests_python.sh
copy-build: true
build-dest: build
verbose: true

```
- Specify the test scripts so that ***plain knows how to run unit and conformance tests.
- Indicate whether to display detailed output during code generation like shown in output control. 
- For additional options and advanced configuration, see the [plain2code CLI documentation](plain2code_cli.md).

## 4. Generate & Run Your Project

```bash
codeplain my_app.plain
```

After rendering is completed the generated code will be available in build/ folder.
