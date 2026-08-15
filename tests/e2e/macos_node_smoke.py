"""macOS smoke test: run Node through the installed package's `execute_script()`.

Invoked directly by the `e2e-macos` job, not by pytest — the rest of `tests/e2e/`
needs a Docker daemon or a Windows runner, and this case needs neither that nor the
live API. It exercises the one thing a macOS runner can prove cheaply: a real toolchain
launched through the terminal backend of the wheel built from this checkout.

Exit codes: 0 on success, 1 on a failed assertion, 69 when the environment cannot run
the case at all.
"""

import re
import shutil
import sys
from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parent.parent.parent
ENVIRONMENT_FAILURE = 69
VERSION_PATTERN = re.compile(r"^v\d+\.\d+\.\d+")


def fail(message):
    print(f"FAIL: {message}")
    return 1


def main():
    node = shutil.which("node")
    if node is None:
        print("Error: node is required for the macOS smoke test")
        return ENVIRONMENT_FAILURE

    from render_machine import render_utils

    installed_from = Path(render_utils.__file__).resolve()
    if installed_from.is_relative_to(CHECKOUT_ROOT):
        return fail(f"execute_script() was imported from the checkout at {installed_from}, not from the wheel")

    print(f"Running {node} --version through {installed_from}")
    exit_code, output, _ = render_utils.execute_script(node, ["--version"], "Smoke", timeout=60)

    if exit_code != 0:
        return fail(f"node exited with {exit_code}; output: {output!r}")
    if not VERSION_PATTERN.match(output.strip()):
        return fail(f"output is not a version: {output!r}")

    print(f"OK: node reported {output.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
