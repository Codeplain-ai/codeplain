# Contributing to Codeplain

Thanks for contributing to Codeplain.

## Before You Start

Check existing issues and pull requests before starting work.

Keep each contribution focused on one issue or a closely related set of changes.

## Contribution Workflow

1. Fork the Codeplain repository.
2. Make your changes on a branch in your fork.
3. Commit and push the changes to your fork.
4. Open a pull request against the Codeplain `main` branch.

## Configure Codeplain

Use your own Codeplain API key for local development and testing.

Never commit API keys, credentials, or other secrets.

## Test with a Plain Example

When relevant, test your change with an existing example from the `plainlang-examples` repository.

This provides a real `.plain` project for verifying the change in an actual Codeplain workflow.

## Run Tests

Run the tests relevant to your change before opening a pull request.

For changes that affect the CLI, rendering, or test execution, also test the change by running the relevant Codeplain command from the terminal.

Check that:

* the command returns the expected exit code,
* errors are clear and do not expose unintended tracebacks,
* existing Codeplain behavior still works.

For platform-specific changes, test the relevant `.sh` or `.ps1` workflow.

## Keep the Change Clean

Before submitting:

* remove temporary files and debugging output,
* avoid unrelated changes,
* add or update tests where needed,
* update documentation when behavior changes,
* verify that no secrets are included.

Review your changes with:

```bash
git status
git diff
```

## Pull Request

Include:

* what changed,
* why it changed,
* the related issue, when applicable,
* how the change was tested,
* the `plainlang-examples` example used, when relevant.
