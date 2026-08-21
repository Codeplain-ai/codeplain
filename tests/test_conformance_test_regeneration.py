"""Regenerating a conformance test after the fix attempts run out.

Two things went wrong on the same path. The regeneration branch was taken for any test
that exhausted its fix attempts, including one belonging to a required module - which
RenderConformanceTests cannot rebuild coherently, because it writes the replacement under
the module being rendered while recording the entry against the module the test belongs
to. And the handler removed the recorded entry with an unguarded `pop`, so a state where
the entry was already gone ended the render with a bare `KeyError` naming the
functionality id.
"""

from unittest.mock import MagicMock

from render_machine.actions.fix_conformance_test import (
    MAX_CONFORMANCE_TEST_FIX_ATTEMPTS,
    MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS,
    FixConformanceTest,
)
from render_machine.render_context import RenderContext


def exhausted_context(current_module="inventory_api", testing_module="inventory_api", entry=None):
    """A conformance loop one attempt short of its limit, with its regeneration budget intact."""
    render_context = MagicMock()
    render_context.module_name = current_module
    render_context.frid_context.frid = "2"

    ctx = render_context.conformance_tests_running_context
    ctx.fix_attempts = MAX_CONFORMANCE_TEST_FIX_ATTEMPTS - 1
    ctx.conformance_tests_render_attempts = MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS - 1
    ctx.current_testing_module_name = testing_module
    ctx.current_testing_frid = "2"
    ctx.get_conformance_tests_json.return_value = {} if entry is None else {"2": entry}
    return render_context


class TestOnlyTheRenderedModulesTestIsRegenerated:
    def test_the_current_modules_test_is_regenerated(self):
        render_context = exhausted_context()

        outcome, payload = FixConformanceTest().execute(render_context, None)

        assert outcome == FixConformanceTest.REGENERATE_CONFORMANCE_TESTS_OUTCOME
        assert payload is None
        assert render_context.conformance_tests_running_context.regenerating_conformance_tests is True

    def test_a_required_modules_test_fails_the_render_instead(self):
        """The replacement would be written for the wrong module, so there is nothing to
        regenerate - the render has failed."""
        render_context = exhausted_context(current_module="inventory_api", testing_module="store_core")

        outcome, payload = FixConformanceTest().execute(render_context, None)

        assert outcome == FixConformanceTest.LIMIT_EXCEEDED_OUTCOME
        assert payload["error"]["message"]
        assert render_context.conformance_tests_running_context.regenerating_conformance_tests is not True


class TestRegenerationSurvivesAMissingEntry:
    @staticmethod
    def _context_with(entry):
        context = RenderContext.__new__(RenderContext)
        ctx = MagicMock()
        ctx.current_testing_frid = "2"
        ctx.current_testing_module_name = "store_core"
        ctx.conformance_tests_render_attempts = 0
        ctx.get_conformance_tests_json.return_value = {} if entry is None else {"2": entry}
        context.conformance_tests_running_context = ctx
        return context, ctx

    def test_a_recorded_entry_is_removed_and_its_folder_deleted(self, monkeypatch):
        deleted = []
        monkeypatch.setattr("render_machine.render_context.file_utils.delete_folder", deleted.append)
        context, ctx = self._context_with({"folder_name": "/tmp/tests/store_core_2"})

        context._handle_test_regeneration()

        assert deleted == ["/tmp/tests/store_core_2"]
        assert ctx.conformance_tests_render_attempts == 1
        assert ctx.fix_attempts == 0

    def test_a_missing_entry_does_not_end_the_render(self, monkeypatch):
        """This raised `KeyError('2')` out of the render, an hour into it, reported to the
        user as the bare string `'2'`."""
        deleted = []
        monkeypatch.setattr("render_machine.render_context.file_utils.delete_folder", deleted.append)
        context, ctx = self._context_with(None)

        context._handle_test_regeneration()

        assert deleted == []
        assert ctx.conformance_tests_render_attempts == 1
        assert ctx.fix_attempts == 0
