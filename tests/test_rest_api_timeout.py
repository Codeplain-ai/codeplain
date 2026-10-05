from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import Timeout

import plain2code_exceptions
from codeplain_REST_api import REPORT_TIMEOUT_SECONDS, REQUEST_TIMEOUT_SECONDS, CodeplainAPI


def _api():
    api = CodeplainAPI("test-key", MagicMock())
    api.api_url = "https://api.example.invalid"
    return api


def _ok_response():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True}
    response.raise_for_status.return_value = None
    return response


@patch("codeplain_REST_api.requests.post")
def test_post_request_always_sets_a_timeout(mock_post):
    """A render call must never wait without a limit.

    With no timeout, a reply that never arrives stops the render for as long as whatever
    runs it allows: no exception, no retry, nothing in the log. Renders have been lost
    that way after a call that had already succeeded on the server.
    """
    mock_post.return_value = _ok_response()

    _api().post_request("https://api.example.invalid/render", {}, {}, None)

    assert mock_post.call_args.kwargs["timeout"] == REQUEST_TIMEOUT_SECONDS
    assert mock_post.call_args.kwargs["timeout"] is not None


@patch("codeplain_REST_api.requests.post")
def test_an_explicit_timeout_still_wins(mock_post):
    """The report path sets its own, much shorter, limit and keeps it."""
    mock_post.return_value = _ok_response()

    _api().post_request("https://api.example.invalid/render_finished", {}, {}, None, timeout=REPORT_TIMEOUT_SECONDS)

    assert mock_post.call_args.kwargs["timeout"] == REPORT_TIMEOUT_SECONDS


@patch("codeplain_REST_api.time.sleep", return_value=None)
@patch("codeplain_REST_api.requests.post")
def test_a_timed_out_call_is_retried_and_then_fails_loudly(mock_post, _sleep):
    """A timeout is a network error: retry it, then stop with a message a user can act on.

    This is the whole point of the limit. Before, the call simply never returned.
    """
    mock_post.side_effect = Timeout("timed out")

    with pytest.raises(plain2code_exceptions.NetworkConnectionError):
        _api().post_request("https://api.example.invalid/render", {}, {}, None, num_retries=2)

    assert mock_post.call_count == 3  # the first attempt plus two retries
