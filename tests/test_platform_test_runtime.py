"""Tests for the platform-test runtime capability the client advertises.

Phase A of the `codeplain-tty` plan: the descriptor and the REST plumbing exist, but the
client advertises nothing until the broker's preflight lands. What is asserted here is
the contract those later phases build on — the version-1 descriptor shape, the gate
returning None, and the request payloads carrying the capability only when it is given.
"""

from unittest.mock import MagicMock

from codeplain_REST_api import CodeplainAPI
from render_machine.platform_test_runtime import (
    CODEPLAIN_TTY_COMMANDS,
    PROTOCOL_VERSION,
    advertised_platform_test_runtime,
    codeplain_tty_descriptor,
)


def make_api(recorded):
    api = CodeplainAPI(api_key="test-key", console=MagicMock())
    api.api_url = "http://api.invalid"

    def post_request(endpoint_url, headers, payload, run_state):
        recorded.append((endpoint_url, payload))
        return {"patched_response_files": [], "conformance_tests_plan_summary_string": ""}

    api.post_request = post_request
    return api


def render_conformance_tests(api, **kwargs):
    return api.render_conformance_tests(
        frid="2",
        functional_requirement_id="1",
        plain_source_tree={},
        linked_resources={},
        existing_files_content={},
        memory_files_content={},
        module_name="module",
        required_modules={},
        conformance_tests_folder_name="folder",
        conformance_tests_json={},
        all_acceptance_tests=[],
        run_state=MagicMock(),
        **kwargs,
    )


def test_the_descriptor_is_the_version_1_contract():
    descriptor = codeplain_tty_descriptor()

    assert descriptor == {
        "codeplain_tty": {
            "protocol_version": PROTOCOL_VERSION,
            "commands": list(CODEPLAIN_TTY_COMMANDS),
        }
    }
    assert PROTOCOL_VERSION == 1
    assert descriptor["codeplain_tty"]["commands"] == [
        "wait-for",
        "wait-until-absent",
        "send-text",
        "send-control",
        "send-hex",
        "size",
    ]


def test_nothing_is_advertised_before_the_broker_preflight_exists():
    assert advertised_platform_test_runtime() is None


def test_the_capability_is_omitted_from_the_payload_by_default():
    recorded = []
    api = make_api(recorded)

    render_conformance_tests(api)

    _, payload = recorded[0]
    assert "platform_test_runtime" not in payload


def test_the_capability_is_sent_when_provided():
    recorded = []
    api = make_api(recorded)

    render_conformance_tests(api, platform_test_runtime=codeplain_tty_descriptor())

    _, payload = recorded[0]
    assert payload["platform_test_runtime"] == codeplain_tty_descriptor()


def test_fix_conformance_tests_issue_carries_the_capability_when_provided():
    recorded = []
    api = make_api(recorded)
    api.post_request = lambda endpoint_url, headers, payload, run_state: recorded.append((endpoint_url, payload)) or []

    api.fix_conformance_tests_issue(
        frid="2",
        functional_requirement_id="1",
        plain_source_tree={},
        linked_resources={},
        existing_files_content={},
        memory_files_content={},
        module_name="module",
        conformance_tests_module_name="module",
        required_modules={},
        code_diff={},
        conformance_tests_files={},
        acceptance_tests=None,
        conformance_tests_issue="issue",
        implementation_fix_count=0,
        conformance_tests_folder_name="folder",
        current_testing_frid_high_level_implementation_plan=None,
        conflicting_requirements_count=0,
        run_state=MagicMock(),
        platform_test_runtime=codeplain_tty_descriptor(),
    )

    _, payload = recorded[0]
    assert payload["platform_test_runtime"] == codeplain_tty_descriptor()


def test_render_acceptance_tests_carries_the_capability_when_provided():
    recorded = []
    api = make_api(recorded)
    api.post_request = lambda endpoint_url, headers, payload, run_state: recorded.append((endpoint_url, payload)) or {}

    api.render_acceptance_tests(
        frid="2",
        plain_source_tree={},
        linked_resources={},
        existing_files_content={},
        memory_files_content={},
        conformance_tests_files={},
        module_name="module",
        required_modules={},
        acceptance_test="test",
        run_state=MagicMock(),
        platform_test_runtime=codeplain_tty_descriptor(),
    )

    _, payload = recorded[0]
    assert payload["platform_test_runtime"] == codeplain_tty_descriptor()
