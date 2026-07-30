import importlib.resources
import os
import sys

import yaml

from plain2code_console import console

ENVIRONMENT_VAR = "CODEPLAIN_ENV"
PRODUCTION_ENV = "production"
DEVELOPMENT_ENV = "development"


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


__version__ = _resolve_version()
__installed__ = _resolve_installed()
__environment__ = _resolve_environment(__installed__)


class SystemConfig:
    """Manages system-level configuration including requirements and error messages."""

    def __init__(self):
        self.config = self._load_config()
        if "error_messages" not in self.config:
            raise KeyError("Missing 'error_messages' section in system_config.yaml")

        self.client_version = __version__
        self.installed = __installed__
        self.environment = __environment__
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
