"""The pins of requirements.txt and pyproject.toml must say the same thing.

requirements.txt builds the development environment and the CI jobs. pyproject.toml is what the
wheel carries, so it is what an installed client gets. A pin changed in one file and not the other
passes every test here and still ships a client that cannot start.
"""

import os
import re
import tomllib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _name(requirement: str) -> str:
    return re.split(r"[=<>@\[]", requirement, maxsplit=1)[0].strip().lower()


def _requirements() -> dict[str, str]:
    with open(os.path.join(REPO, "requirements.txt"), encoding="utf-8") as f:
        lines = [line.strip() for line in f]
    return {_name(line): line for line in lines if line and not line.startswith("#")}


def _pyproject() -> tuple[dict[str, str], dict[str, str]]:
    with open(os.path.join(REPO, "pyproject.toml"), "rb") as f:
        pyproject = tomllib.load(f)["project"]
    runtime = {_name(item): item for item in pyproject["dependencies"]}
    dev = {_name(item): item for item in pyproject["optional-dependencies"]["dev"]}
    return runtime, dev


def test_every_pin_of_the_wheel_matches_requirements():
    requirements = _requirements()
    runtime, dev = _pyproject()

    mismatched = {
        name: (requirement, requirements.get(name))
        for name, requirement in {**runtime, **dev}.items()
        if requirements.get(name) != requirement
    }

    assert mismatched == {}, f"pyproject.toml and requirements.txt disagree: {mismatched}"


def test_requirements_names_nothing_the_wheel_leaves_out():
    requirements = _requirements()
    runtime, dev = _pyproject()

    missing = sorted(set(requirements) - set(runtime) - set(dev))

    assert missing == [], f"requirements.txt pins what pyproject.toml does not declare: {missing}"
