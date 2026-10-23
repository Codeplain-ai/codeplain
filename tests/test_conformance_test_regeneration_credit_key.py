"""Tests for the identifiers sent when conformance tests of an earlier functionality are recreated.

The API reserves credits for the functionality being rendered and keys the reservation on the frid
and module name the client sends. During regression the functionality under test can be an earlier
one, so the call must still name the functionality being rendered or the API refuses it with a 404.
"""

from types import SimpleNamespace

import pytest

import plain_spec
from render_machine.actions.render_conformance_tests import RenderConformanceTests
from render_machine.render_types import ConformanceTestsRunningContext

RENDERED_FRID = "3"
TESTED_FRID = "2"
MODULE_NAME = "task-manager"


class StopAfterFolderNameCall(Exception):
    """Ends the action once the call under test has been captured."""


class FakeCodeplainAPI:
    def __init__(self):
        self.folder_name_call = None

    def generate_folder_name_from_functional_requirement(self, **kwargs):
        self.folder_name_call = kwargs
        raise StopAfterFolderNameCall


class FakeConformanceTests:
    def fetch_existing_conformance_test_folder_names(self, module_name):
        return []


@pytest.fixture
def api():
    return FakeCodeplainAPI()


@pytest.fixture
def render_context(api):
    conformance_tests_running_context = ConformanceTestsRunningContext(
        current_testing_module_name=MODULE_NAME,
        current_testing_frid=TESTED_FRID,
        fix_attempts=0,
        conformance_tests_json={RENDERED_FRID: {"folder_name": "add_new_task_ui_verification"}},
        conformance_tests_render_attempts=1,
        current_testing_frid_specifications={
            plain_spec.TEST_REQUIREMENTS: ["- :ConformanceTests: are implemented with Unittest."],
            plain_spec.FUNCTIONAL_REQUIREMENTS: ["- The :TaskList: is displayed."],
        },
        should_prepare_testing_environment=False,
        frid_being_implemented=RENDERED_FRID,
    )
    return SimpleNamespace(
        codeplain_api=api,
        conformance_tests=FakeConformanceTests(),
        conformance_tests_running_context=conformance_tests_running_context,
        frid_context=SimpleNamespace(frid=RENDERED_FRID),
        module_name=MODULE_NAME,
        run_state=SimpleNamespace(render_id="eb93e01c-ac87-4566-b8e3-e4cdab63609c"),
    )


def capture_folder_name_call(render_context, api):
    with pytest.raises(StopAfterFolderNameCall):
        RenderConformanceTests()._render_conformance_tests(render_context)
    return api.folder_name_call


def test_folder_name_call_names_the_rendered_functionality(render_context, api):
    """The credit reservation is keyed on the rendered frid and module, so the call must send those."""
    call = capture_folder_name_call(render_context, api)

    assert call["frid"] == RENDERED_FRID
    assert call["module_name"] == MODULE_NAME


def test_folder_name_call_describes_the_functionality_under_test(render_context, api):
    """The folder is named after the functionality whose tests are recreated, not the rendered one."""
    call = capture_folder_name_call(render_context, api)

    assert call["functional_requirement"] == "- The :TaskList: is displayed."
