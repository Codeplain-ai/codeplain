"""Regression tests: spec-derived text with square brackets must never be parsed as Textual markup."""

import asyncio

from textual.widgets import Static

from event_bus import EventBus
from plain2code_state import RunState
from tui.components import FRIDProgress, ProgressItem, RenderingInfoBox, SubstateLine, TUIComponents
from tui.models import Substate
from tui.plain2code_tui import Plain2CodeTUI
from tui.widget_helpers import display_error_message, display_success_message, update_progress_item_substates

# Exact acceptance-test text that crashed the TUI with
# "MarkupError: closing tag '[/#888888]' does not match any open tag":
# the unterminated "[b" swallowed the timer's opening tag.
UNTERMINATED_BRACKET_TEXT = (
    "Rendering acceptance test: With a=1, b=2, c=3, d=4: ZRANK z c should reply 2, "
    'ZREVRANK z c WITHSCORE should reply [1, "3"], ZRANK z zz should reply null, '
    "ZCOUNT z (1 3 should reply 2, and ZLEXCOUNT z [b + should reply 3."
)
# Terminated brackets used to be silently eaten as a markup tag ("LPOP key ").
TERMINATED_BRACKET_TEXT = "LPOP key [count] and RPOP key [count] reply one element"


def _make_app(run_state: RunState, event_bus: EventBus) -> Plain2CodeTUI:
    return Plain2CodeTUI(
        event_bus=event_bus,
        run_state=run_state,
        on_ready=lambda: None,
        render_id="test-render-id",
        unittests_script=None,
        conformance_tests_script="run_conformance_tests.sh",
        prepare_environment_script=None,
        state_machine_version="0.0.0",
        css_path="styles.css",
    )


def test_substate_line_keeps_bracket_text_verbatim():
    for text in (UNTERMINATED_BRACKET_TEXT, TERMINATED_BRACKET_TEXT):
        line = SubstateLine(text, "    ", ProgressItem.PROCESSING)._format_line()
        assert text in line.plain
        assert line.plain.endswith("(0s)")
        # Only the timer carries the muted style; the text itself is unstyled.
        assert len(line.spans) == 1
        assert line.plain[line.spans[0].start : line.spans[0].end] == "(0s)"


def test_substate_with_brackets_renders_without_markup_error():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        async with app.run_test() as pilot:
            widget_id = TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value
            update_progress_item_substates(
                app, widget_id, [Substate(UNTERMINATED_BRACKET_TEXT), Substate(TERMINATED_BRACKET_TEXT)]
            )
            await pilot.pause()
            await pilot.pause()

            lines = app.query(".substate-line-text").results(Static)
            rendered = [str(line.content) for line in lines]
            assert any(UNTERMINATED_BRACKET_TEXT in text for text in rendered)
            assert any(TERMINATED_BRACKET_TEXT in text for text in rendered)

    asyncio.run(scenario())


def test_status_messages_keep_brackets():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        async with app.run_test() as pilot:
            status = app.query_one(f"#{TUIComponents.RENDER_STATUS_WIDGET.value}", Static)

            display_success_message(app, "out/[weird] path")
            await pilot.pause()
            assert "rendering completed!" in str(status.content)
            assert "generated code folder: out/[weird] path" in str(status.content)

            display_error_message(app, "Error: [Errno 2] No such file: run_tests.sh [b")
            await pilot.pause()
            assert "[Errno 2] No such file: run_tests.sh [b" in str(status.content)

    asyncio.run(scenario())


# A functionality quote as the render TUI receives it: several lines, square brackets included.
MULTILINE_FUNCTIONALITY_TEXT = (
    "Functionality 3: :User: should be able to add a :Task: [optional]\n"
    "  - The :Task: must have non-empty content.\n"
    "  - The :Task: is appended to the end of the list."
)


def test_functionality_text_collapses_and_expands_with_ctrl_o():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        async with app.run_test() as pilot:
            frid_progress = app.query_one(f"#{TUIComponents.FRID_PROGRESS.value}", FRIDProgress)
            info_box = frid_progress.query_one(RenderingInfoBox)
            info_box.update_functionality(MULTILINE_FUNCTIONALITY_TEXT)
            await pilot.pause()

            widget = info_box.functionality_widget
            assert widget is not None

            # Collapsed by default: first line only, with the expand hint.
            collapsed = str(widget.content)
            assert "Functionality 3: :User: should be able to add a :Task: [optional]" in collapsed
            assert "non-empty content" not in collapsed
            assert RenderingInfoBox.EXPAND_HINT in collapsed

            await pilot.press("ctrl+o")
            await pilot.pause()
            expanded = str(widget.content)
            assert "non-empty content" in expanded
            assert "appended to the end of the list" in expanded
            assert RenderingInfoBox.COLLAPSE_HINT in expanded

            await pilot.press("ctrl+o")
            await pilot.pause()
            assert str(widget.content) == collapsed

    asyncio.run(scenario())


def test_single_line_functionality_text_has_no_hint():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        async with app.run_test() as pilot:
            info_box = app.query_one(f"#{TUIComponents.FRID_PROGRESS.value}", FRIDProgress).query_one(RenderingInfoBox)
            info_box.update_functionality("Functionality 1: :User: should be able to add a :Task:")
            await pilot.pause()

            widget = info_box.functionality_widget
            assert widget is not None
            assert str(widget.content) == "Functionality 1: :User: should be able to add a :Task:"

    asyncio.run(scenario())
