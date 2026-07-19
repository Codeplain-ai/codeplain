"""Tests for RunState render-time accounting."""

import time

from plain2code_state import RunState


def test_get_live_render_time_includes_in_progress_segment():
    run_state = RunState(spec_filename="x.plain")
    run_state.render_time_accumulated = 100
    run_state.last_render_start_timestamp = time.monotonic() - 5
    # 100 banked + ~5 in the current segment.
    assert run_state.get_live_render_time() == 105


def test_add_to_render_time_banks_and_resets_segment():
    run_state = RunState(spec_filename="x.plain")
    run_state.last_render_start_timestamp = time.monotonic() - 10

    run_state.add_to_render_time()

    # The 10s segment is banked...
    assert run_state.render_time_accumulated == 10
    # ...and the segment start is reset, so the value is not re-counted afterwards.
    assert run_state.get_live_render_time() == 10


def test_add_to_render_time_is_cumulative_across_segments():
    run_state = RunState(spec_filename="x.plain")

    run_state.last_render_start_timestamp = time.monotonic() - 3
    run_state.add_to_render_time()
    run_state.last_render_start_timestamp = time.monotonic() - 4
    run_state.add_to_render_time()

    assert run_state.render_time_accumulated == 7
