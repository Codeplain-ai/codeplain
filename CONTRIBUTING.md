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

## Pre-Releases

Everything below happens on real PyPI — there is no separate test index.
Pre-releases are invisible to ordinary installs: pip and uv skip them during
resolution, so `pip install codeplain` and `pip install --upgrade codeplain`
always serve the latest *stable* release.

### Dev builds (automatic)

Every merge to `main` publishes a dev release such as `0.3.10.dev7`, so the tip
of main is always installable from the real index:

```bash
pip install --pre codeplain==0.3.10.dev7
uv tool install --prerelease allow codeplain
```

Versions are `<next-patch>.dev<run-number>`, so they sort after the last
release and before the next one. See `.github/workflows/publish-dev-to-pypi.yml`.
If that workflow is ever dispatched against a commit that is itself a release
tag, it skips rather than publishing a dev build that would sort below the
release.

### Release candidates (manual)

To get a specific build in front of people before it becomes the default
install, cut a PEP 440 pre-release.

Tag and publish a GitHub release as usual, but with a pre-release version and
the release marked "pre-release":

```
v0.4.0a1    alpha
v0.4.0b1    beta
v0.4.0rc1   release candidate
```

`publish-to-pypi.yml` handles these with no special casing: hatch-vcs reads the
version straight off the tag (`v0.4.0rc1` -> `0.4.0rc1`).

Opt in the same way:

```bash
pip install --pre codeplain==0.4.0rc1
```

### Which API a build talks to

Pre-release builds default to the **test** API, so an unreleased client is
exercised against the unreleased backend:

| build | default API |
| --- | --- |
| stable release (`0.3.9`) | `https://api.codeplain.ai` |
| dev build (`0.3.10.dev7`) | `https://api.test.codeplain.ai` |
| alpha/beta/rc (`0.4.0rc1`) | `https://api.test.codeplain.ai` |

`--api` always overrides this. Resolution lives in
`system_config._resolve_default_api_url`, keyed off the version baked into the
package at build time — so nothing needs configuring at install time.

A source checkout reports the nearest git tag as its version, so it is treated
as stable and keeps pointing at production; use `--api` to aim it elsewhere.

Because every build above goes to real PyPI, dependency resolution and the
install path are exactly what an end user gets — the main reason this is
preferred over publishing to TestPyPI.
