# Codeplain plain2code renderer

Render ***plain source to software code using the Codeplain API.

## Codeplain.ai - Code Generation as a Service

Codeplain is a platform that generates software code using large language models based on requirements you specify in ***plain specification language.

Schematic overview of the Codeplain's code generation service

<img src="https://raw.githubusercontent.com/Codeplain-ai/plain2code_client/main/resources/codeplain_overview.png">

### Abstracting Away Code Generation Complexity with ***plain


***plain is a novel specification language that helps abstracting away complexity of using large language models for code generation.

An example application in ***plain

<img src="https://raw.githubusercontent.com/Codeplain-ai/plain2code_client/main/resources/plain_example.png" width="70%" height="70%">


## Getting started

### Prerequisites


#### System requirements

To run the plain2code client, you need Python 3.11 or a later version.

**Windows users:** Please install WSL (Windows Subsystem for Linux) as this is currently the supported environment for running plain code on Windows.

#### Authorization - Codeplain API Key

We are using Codeplain API Key to authorize requests to the Codeplain API. To get your Codeplain API Key, please contact Codeplain.ai support at support@codeplain.ai.

In order to generate code, you need to export the following environment variable:

```bash
export CODEPLAIN_API_KEY="your_actual_api_key_here"
```

### Installation Steps

1. Clone this repository
2. Set your Codeplain API key as an environment variable:
   ```
   export CODEPLAIN_API_KEY=your_api_key_here
   ```
3. (Recommended) Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
4. Install required libraries
   ```
   pip install -r requirements.txt
   ```

### Quick Start

After completing the installation steps above, you can immediately test the system with a simple "Hello World" example:

- Change to the example folder and run the example:
   ```
   cd examples/example_hello_world_python
   python ../../plain2code.py hello_world_python.plain
   ```

   *Note: Rendering will take a few minutes to complete.*

- The system will generate a Python application in the `build` directory. You can run it with:
   ```
   cd build
   python hello_world.py
   ```

## Additional Resources

### Examples and Sample Projects

- See the [examples](examples) folder for sample projects in Golang, Python, and React.
- For example application how to implement task manager in ***plain see [example-task-manager](https://github.com/Codeplain-ai/example-task-manager) repository.
- For example application how to implement SaaS connectors in ***plain see [example-saas-connectors](https://github.com/Codeplain-ai/example-saas-connectors) repository.

### Documentation

- For more details on the ***plain format, see the [***plain language specification](https://www.plainlang.org/docs/intro/).
- For step-by-step instructions for creating your first ***plain project see the [Kickstart your ***plain project](docs/starting_a_plain_project_from_scratch.md).
- For complete CLI documentation and usage examples, see [plain2code CLI documentation](docs/plain2code_cli.md).


