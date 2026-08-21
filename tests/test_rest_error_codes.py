"""Tests for mapping API error codes onto typed client exceptions."""

from unittest.mock import MagicMock, patch

import pytest
import requests

import plain2code_exceptions
from codeplain_REST_api import CodeplainAPI


def test_conformance_fix_exhaustion_maps_to_its_typed_exception():
    """The server reports fix-attempt exhaustion as a structured error; the client raises
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


class TestErrorCodesAreReadOnEveryDeclinedStatus:
    """The client used to consult `error_code` only on 400, so every structured error the
    server returns with a 500 - its own `InternalServerError` among them - reached the user
    as `500 Server Error: INTERNAL SERVER ERROR for url: <internal endpoint>`. The message
    the code was paired with never printed."""

    @staticmethod
    def _api_returning(status, body):
        response = MagicMock()
        response.status_code = status
        response.ok = 200 <= status < 300
        response.json.return_value = body
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(f"{status} Server Error")

        api = CodeplainAPI(api_key="test-key", console=MagicMock())
        api.api_url = "https://api.example"
        return api, response

    def test_a_500_carrying_an_error_code_raises_the_typed_exception(self):
        api, response = self._api_returning(
            500,
            {
                "error_code": "ConformanceTestsFixExhausted",
                "message": "The conformance tests for functionality 1 still failed.",
            },
        )

        with patch("requests.post", return_value=response):
            with pytest.raises(plain2code_exceptions.ConformanceTestsFixExhausted, match="still failed"):
                api.post_request("https://api.example/x", {}, {}, None, num_retries=0)

    def test_the_generic_server_error_reaches_the_user_as_its_own_message(self):
        api, response = self._api_returning(500, {"error_code": "InternalServerError", "message": "boom"})

        with patch("requests.post", return_value=response):
            with pytest.raises(plain2code_exceptions.InternalServerError, match="render log"):
                api.post_request("https://api.example/x", {}, {}, None, num_retries=0)

    def test_a_success_is_returned_untouched(self):
        api, response = self._api_returning(200, {"result": "ok"})
        response.raise_for_status.side_effect = None

        with patch("requests.post", return_value=response):
            assert api.post_request("https://api.example/x", {}, {}, None, num_retries=0) == {"result": "ok"}
