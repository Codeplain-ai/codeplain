import importlib.resources
import os
import re
import sys

import yaml

from plain2code_console import console

ENVIRONMENT_VAR = "CODEPLAIN_ENV"
PRODUCTION_ENV = "production"
DEVELOPMENT_ENV = "development"

PRODUCTION_API_URL = "https://api.codeplain.ai"
TEST_API_URL = "https://api.test.codeplain.ai"

# PEP 440 pre-release markers, matched against the end of the release segment:
# alpha/beta/release-candidate (0.4.0a1, 0.4.0b2, 0.4.0rc1) or a dev release
# (0.3.10.dev7, published on every merge to main). ".postN" alone is NOT a
# pre-release and must not match.
_PRERELEASE_RE = re.compile(r"(?:a|b|rc)\d+$|\.dev\d+$", re.IGNORECASE)


def _resolve_version() -> str:
    """Resolve the client version.

    For an installed package the version is read from its metadata (hatch-vcs
    bakes it in from the git tag at build time). When running from a source
    checkout that hasn't been installed, fall back to the nearest git tag.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("codeplain")
    except PackageNotFoundError:
        pass

    try:
        import git

        # Anchor to this source file's own location so we inspect the
        # codeplain checkout's repo, not the caller's working directory
        # (codeplain may be run from anywhere).
        source_dir = os.path.dirname(os.path.abspath(__file__))
        repo = git.Repo(source_dir, search_parent_directories=True)

        # Highest version tag, regardless of branch ancestry (a dev run may sit
        # on a feature branch that doesn't descend from the latest release tag).
        latest_tag = repo.git.tag("--list", "--sort=-v:refname").splitlines()[0]
        return latest_tag.lstrip("v")
    except Exception:
        return "0.0.0.dev0"


def _resolve_installed() -> bool:
    """Return True if codeplain was installed from a package index.

    An installed distribution that came from an index has no PEP 610
    ``direct_url.json``; installs from a local path, VCS or direct URL
    (``pip install -e .``, ``pip install .``, a git URL) do. A source checkout
    that was never installed has no distribution metadata at all.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        dist = distribution("codeplain")
    except PackageNotFoundError:
        return False
    except Exception:
        return False

    try:
        return dist.read_text("direct_url.json") is None
    except Exception:
        return False


def _resolve_environment(installed: bool) -> str:
    """Resolve the environment this run reports itself as.

    An explicit CODEPLAIN_ENV always wins. Otherwise a package installed from an
    index is a real user install ("production"), while anything else (a source
    checkout, an editable install) is a dev run.
    """
    explicit_env = os.environ.get(ENVIRONMENT_VAR, "").strip()
    if explicit_env:
        return explicit_env

    return PRODUCTION_ENV if installed else DEVELOPMENT_ENV


def _is_prerelease(version: str) -> bool:
    """Return True if ``version`` is a PEP 440 pre-release (alpha, beta, rc or dev).

    Deliberately dependency-free rather than using ``packaging``: that library
    is not a runtime dependency (it only reaches this checkout via dev tools),
    so it is absent from a real user install. The only versions that reach here
    are the ones our own build pipeline produces.
    """
    # Drop any local version segment ("+gb710c298") before inspecting.
    return _PRERELEASE_RE.search(version.split("+", 1)[0]) is not None


def _resolve_default_api_url(version: str) -> str:
    """Resolve the API this client talks to when ``--api`` is not given.

    Pre-release builds — the dev build published on every merge to main, and
    any alpha/beta/rc release — default to the test API, so an unreleased
    client is exercised against the unreleased backend. Stable releases default
    to production. ``--api`` always wins over this.
    """
    return TEST_API_URL if _is_prerelease(version) else PRODUCTION_API_URL


__version__ = _resolve_version()
__installed__ = _resolve_installed()
__environment__ = _resolve_environment(__installed__)
__default_api_url__ = _resolve_default_api_url(__version__)


class SystemConfig:
    """Manages system-level configuration including requirements and error messages."""

    def __init__(self):
        self.config = self._load_config()
        if "error_messages" not in self.config:
            raise KeyError("Missing 'error_messages' section in system_config.yaml")

        self.client_version = __version__
        self.installed = __installed__
        self.environment = __environment__
        self.default_api_url = __default_api_url__
        self.error_messages = self.config["error_messages"]

    def _load_config(self):
        """Load system configuration from YAML file."""
        config_path = importlib.resources.files("config").joinpath("system_config.yaml")
        try:
            with config_path.open("r") as f:
                yaml_data = yaml.safe_load(f)
                return yaml_data
        except Exception as e:
            console.error(f"Failed to load system configuration: {e}")
            sys.exit(69)


# Create a singleton instance
system_config = SystemConfig()
