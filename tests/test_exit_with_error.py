"""The operator-facing message on a failed render.

`ExitWithError` prints the payload the failing action handed over, but not every path
into it supplies one — a render that gave up inside the unit-test fix loop arrives with
`None`, and the user was shown a bare `ERROR codeplain: None` with no reason. The encoded
payload already falls back to `last_error_message`; the console line must agree, so the
message a user sees is never less informative than the one the renderer returns.
"""

from unittest.mock import MagicMock, patch

from render_machine.actions.exit_with_error import ExitWithError
from render_machine.render_types import RenderError


def render_context(last_error_message=None):
    context = MagicMock()
    context.last_error_message = last_error_message
    context.frid_context.frid = "2"
    context.run_state.render_id = "render-id"
    return context


def executed_with(payload, last_error_message=None):
    context = render_context(last_error_message)
    with patch("render_machine.actions.exit_with_error.console") as console:
        outcome, encoded = ExitWithError().execute(context, payload)
    return console.error.call_args[0][0], outcome, encoded


def test_the_failing_action_s_own_message_is_shown():
    shown, outcome, _ = executed_with("Conformance tests could not be fixed.")

    assert shown == "Conformance tests could not be fixed."
    assert outcome == ExitWithError.SUCCESSFUL_OUTCOME


def test_a_missing_payload_falls_back_to_the_last_error_message():
    shown, _, _ = executed_with(None, last_error_message="The Unit Tests script has failed.")

    assert shown == "The Unit Tests script has failed."


def test_with_nothing_to_report_the_user_still_gets_words():
    shown, _, _ = executed_with(None)

    assert shown == "Unknown error"


def test_an_encoded_error_payload_is_unwrapped_to_its_reason():
    """The conformance-fix-exhausted path arrives as an encoded RenderError, which used
    to reach the user as a raw dict repr."""
    payload = RenderError.encode(message="Could not produce an implementation that passes.").to_payload()

    shown, _, _ = executed_with(payload)

    assert shown == "Could not produce an implementation that passes."


def test_the_shown_message_matches_the_encoded_one():
    """Two sources of truth for the same failure would let the log and the returned
    error disagree about why a render stopped."""
    shown, _, encoded = executed_with(None, last_error_message="The Unit Tests script has failed.")

    assert shown == encoded["error"]["message"]
