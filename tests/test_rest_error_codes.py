"""Tests for mapping API error codes onto typed client exceptions."""

from unittest.mock import MagicMock

import pytest

import plain2code_exceptions
from codeplain_REST_api import CodeplainAPI


def test_conformance_fix_exhaustion_maps_to_its_typed_exception():
    """The server reports fix-attempt exhaustion as a structured 400; the client raises
    the matching typed exception so the render fails with the server's message instead
    of a raw HTTP error."""
    api = CodeplainAPI(api_key="test-key", console=MagicMock())

    with pytest.raises(plain2code_exceptions.ConformanceTestsFixExhausted, match="after 10 attempts"):
        api._raise_for_error_code(
            {
                "error_code": "ConformanceTestsFixExhausted",
                "message": "Could not fix conformance tests issue for functional requirement 1 after 10 attempts.",
            }
        )


def test_an_unknown_error_code_still_falls_through_silently():
    api = CodeplainAPI(api_key="test-key", console=MagicMock())

    api._raise_for_error_code({"error_code": "SomeFutureCode", "message": "whatever"})  # does not raise


def test_the_exhaustion_error_is_expected_rather_than_a_crash():
    """A render outcome the user acts on. Left out of this tuple it reaches the top level as
    an unexpected exception and is reported to Sentry as a crash."""
    from plain2code import EXPECTED_EXCEPTIONS

    assert plain2code_exceptions.ConformanceTestsFixExhausted in EXPECTED_EXCEPTIONS
