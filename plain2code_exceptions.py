from plain_parser.exceptions import (  # noqa: F401
    ImportedModuleWithFunctionalitiesError,
    InvalidFridArgument,
    InvalidLiquidVariableName,
    MissingFunctionalitiesError,
    ModuleDoesNotExistError,
    PlainSyntaxError,
    UnsupportedBase64Content,
    UnsupportedResourceType,
)


class FunctionalRequirementTooComplex(Exception):
    def __init__(self, message, proposed_breakdown=None):
        self.message = message
        self.proposed_breakdown = proposed_breakdown
        super().__init__(self.message)


class ConflictingRequirements(Exception):
    pass


class RenderingCreditBalanceTooLow(Exception):
    pass


class LLMInternalError(Exception):
    pass


class MissingResource(Exception):
    pass


class InternalClientError(Exception):
    pass


class MissingAPIKey(Exception):
    pass


class InvalidAPIKey(Exception):
    pass


class OutdatedClientVersion(Exception):
    pass


class InvalidGitRepositoryError(Exception):
    """Raised when the git repository is in an invalid state."""

    pass


class InternalServerError(Exception):
    pass


class MissingPreviousFunctionalitiesError(Exception):
    """Raised when trying to render from a FRID but previous FRID commits are missing."""

    pass


class NetworkConnectionError(Exception):
    """Raised when there is a network connectivity issue with the API server."""

    pass


class AmbiguousConfigFileError(Exception):
    """Raised when a config file is found in both the plain file directory and the current working directory."""

    pass


class RenderCancelledError(Exception):
    """Raised when the render is cancelled by the user closing the TUI."""

    pass


class GitNotInstalledError(Exception):
    """Raised when git is not installed or not found on PATH."""

    pass


class InvalidModuleArchiveError(Exception):
    """Raised when a ``<module>.module`` archive is missing, corrupt, or has an
    unexpected layout (not a zip, missing ``code/``/``tests/``, ``.git`` not a real
    directory, detached HEAD, or an unsafe member path)."""

    pass
