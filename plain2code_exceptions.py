class FunctionalRequirementTooComplex(Exception):
    def __init__(self, message, proposed_breakdown=None):
        self.message = message
        self.proposed_breakdown = proposed_breakdown
        super().__init__(self.message)


class ConflictingRequirements(Exception):
    pass


class CreditBalanceTooLow(Exception):
    pass


class LLMInternalError(Exception):
    pass


class MissingResource(Exception):
    pass


class PlainSyntaxError(Exception):
    pass


class UnexpectedState(Exception):
    pass


class MissingAPIKey(Exception):
    pass


class InvalidAPIKey(Exception):
    pass


class OutdatedClientVersion(Exception):
    pass


class InvalidFridArgument(Exception):
    pass


class InvalidGitRepositoryError(Exception):
    """Raised when the git repository is in an invalid state."""

    pass


class InvalidLiquidVariableName(Exception):
    pass


class ModuleDoesNotExistError(Exception):
    pass


class InternalServerError(Exception):
    pass


class MissingPreviousFunctionalitiesError(Exception):
    """Raised when trying to render from a FRID but previous FRID commits are missing."""

    pass
