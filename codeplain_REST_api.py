import time
from typing import Optional

import requests

import plain2code_exceptions
from plain2code_state import RunState

MAX_RETRIES = 4
RETRY_DELAY = 3

# TODO: Handle connection errors
RETRY_ERROR_CODES = [
    "LLMInternalError",
]

# Mapping from API error codes to exception classes
ERROR_CODE_EXCEPTIONS = {
    "FunctionalRequirementTooComplex": plain2code_exceptions.FunctionalRequirementTooComplex,
    "ConflictingRequirements": plain2code_exceptions.ConflictingRequirements,
    "CreditBalanceTooLow": plain2code_exceptions.CreditBalanceTooLow,
    "LLMInternalError": plain2code_exceptions.LLMInternalError,
    "MissingResource": plain2code_exceptions.MissingResource,
    "PlainSyntaxError": plain2code_exceptions.PlainSyntaxError,
    "InternalServerError": plain2code_exceptions.InternalServerError,
}


class CodeplainAPI:

    def __init__(self, api_key, console):
        self.api_key = api_key
        self.console = console

    @property
    def api_url(self):
        return self._api_url

    @api_url.setter
    def api_url(self, value):
        self._api_url = value

    def _extend_payload_with_run_state(self, payload: dict, run_state: RunState):
        run_state.increment_call_count()
        payload["render_state"] = run_state.to_dict()

    def _raise_for_error_code(self, response_json):
        """Raise appropriate exception based on error code in response."""
        error_code = response_json.get("error_code")
        if error_code not in ERROR_CODE_EXCEPTIONS:
            return

        exception_class = ERROR_CODE_EXCEPTIONS[error_code]
        message = response_json.get("message", "")

        # FunctionalRequirementTooComplex has an extra parameter
        if error_code == "FunctionalRequirementTooComplex":
            raise exception_class(message, response_json.get("proposed_breakdown"))

        raise exception_class(message)

    def post_request(
        self, endpoint_url, headers, payload, run_state: Optional[RunState], num_retries: int = MAX_RETRIES
    ):
        if run_state is not None:
            self._extend_payload_with_run_state(payload, run_state)

        retry_delay = RETRY_DELAY
        response_json = None

        for attempt in range(num_retries + 1):
            try:
                response = requests.post(endpoint_url, headers=headers, json=payload)

                try:
                    response_json = response.json()
                except requests.exceptions.JSONDecodeError as e:
                    print(f"Failed to decode JSON response: {e}. Response text: {response.text}")
                    raise

                if response.status_code == requests.codes.bad_request and "error_code" in response_json:
                    self._raise_for_error_code(response_json)

                response.raise_for_status()
                return response_json

            except Exception as e:
                if response_json is not None and "error_code" in response_json:
                    if response_json["error_code"] not in RETRY_ERROR_CODES:
                        raise e

                if attempt < num_retries:
                    self.console.info(f"Error on attempt {attempt + 1}/{num_retries + 1}: {e}")
                    self.console.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    self.console.error(f"Max retries ({num_retries}) exceeded. Last error: {e}")
                    raise e

    def connection_check(self, client_version):
        endpoint_url = f"{self.api_url}/connection_check"
        headers = {"Content-Type": "application/json"}
        payload = {
            "api_key": self.api_key,
            "client_version": client_version,
        }
        return self.post_request(endpoint_url, headers, payload, None, num_retries=0)

    def render_functional_requirement(
        self,
        frid: str,
        plain_source_tree: dict,
        linked_resources: dict,
        existing_files_content: dict,
        memory_files_content: dict,
        module_name: str,
        required_modules: dict,
        run_state: RunState,
    ) -> dict[str, str]:
        """
        Renders the content of a functional requirement based on the provided ID,
        corresponding sections from a Plain document, and existing files' content.

        Args:
            frid (str): The unique identifier for the functional requirement to be rendered.
            plain_source_tree (dict): A dictionary containing the plain source tree.
            linked_resources (dict): A dictionary where the keys represent filenames of linked
                                        resources and the values are dictionaries containing
                                        the content of the linked resources and the sections
                                        they are linked to.
            existing_files_content (dict): A dictionary where the keys represent filenames
                                        and the values are the content of those files.
            memory_files_content (dict): A dictionary where the keys represent memory filenames
                                        and the values are the content of those files.
            module_name (str): The name of the module to render the functional requirement for.
            required_modules (dict): A dictionary where the keys represent module names
                                     and the values are lists of functionalities implemented in those modules.
            run_state (RunState): The current state of the rendering process.
        Returns:
            dict[str, str]: A dictionary where the keys are filenames and the values
                            are the rendered code for those files.

        Raises:
            ValueError: If the frid is invalid or the necessary sections cannot be found.
        """
        endpoint_url = f"{self.api_url}/render_functional_requirement"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
            "plain_source_tree": plain_source_tree,
            "linked_resources": linked_resources,
            "existing_files_content": existing_files_content,
            "memory_files_content": memory_files_content,
            "module_name": module_name,
            "required_modules": required_modules,
        }

        return self.post_request(endpoint_url, headers, payload, run_state)

    def fix_unittests_issue(
        self,
        frid,
        plain_source_tree,
        linked_resources,
        existing_files_content,
        module_name: str,
        required_modules,
        unittests_issue,
        run_state: RunState,
    ):
        endpoint_url = f"{self.api_url}/fix_unittests_issue"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
            "plain_source_tree": plain_source_tree,
            "linked_resources": linked_resources,
            "existing_files_content": existing_files_content,
            "module_name": module_name,
            "required_modules": required_modules,
            "unittests_issue": unittests_issue,
            "unittest_batch_id": run_state.unittest_batch_id,
        }

        return self.post_request(endpoint_url, headers, payload, run_state)

    def create_conformance_test_memory(
        self,
        frid,
        plain_source_tree,
        linked_resources,
        existing_files_content,
        memory_files_content,
        module_name,
        required_modules,
        code_diff,
        conformance_tests_files,
        acceptance_tests,
        conformance_tests_issue,
        conformance_tests_folder_name,
        previous_conformance_tests_issue_old,
        run_state: RunState,
    ):
        endpoint_url = f"{self.api_url}/create_conformance_test_memory"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
            "plain_source_tree": plain_source_tree,
            "linked_resources": linked_resources,
            "existing_files_content": existing_files_content,
            "memory_files_content": memory_files_content,
            "module_name": module_name,
            "required_modules": required_modules,
            "code_diff": code_diff,
            "conformance_tests_files": conformance_tests_files,
            "acceptance_tests": acceptance_tests,
            "conformance_tests_issue": conformance_tests_issue,
            "conformance_tests_folder_name": conformance_tests_folder_name,
            "previous_conformance_tests_issue": previous_conformance_tests_issue_old,
        }

        return self.post_request(endpoint_url, headers, payload, run_state)

    def refactor_source_files_if_needed(self, frid, files_to_check, existing_files_content, run_state: RunState):
        endpoint_url = f"{self.api_url}/refactor_source_files_if_needed"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
            "files_to_check": list(files_to_check),
            "existing_files_content": existing_files_content,
        }

        return self.post_request(endpoint_url, headers, payload, run_state)

    def render_conformance_tests(
        self,
        frid,
        functional_requirement_id,
        plain_source_tree,
        linked_resources,
        existing_files_content,
        memory_files_content,
        module_name: str,
        required_modules,
        conformance_tests_folder_name,
        conformance_tests_json,
        all_acceptance_tests,
        run_state: RunState,
    ):
        endpoint_url = f"{self.api_url}/render_conformance_tests"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
            "functional_requirement_id": functional_requirement_id,
            "plain_source_tree": plain_source_tree,
            "linked_resources": linked_resources,
            "existing_files_content": existing_files_content,
            "memory_files_content": memory_files_content,
            "module_name": module_name,
            "required_modules": required_modules,
            "conformance_tests_folder_name": conformance_tests_folder_name,
            "conformance_tests_json": conformance_tests_json,
            "all_acceptance_tests": all_acceptance_tests,
        }

        response = self.post_request(endpoint_url, headers, payload, run_state)
        return response["patched_response_files"], response["conformance_tests_plan_summary_string"]

    def generate_folder_name_from_functional_requirement(
        self,
        frid,
        functional_requirement,
        existing_folder_names,
        run_state: RunState,
    ):
        endpoint_url = f"{self.api_url}/generate_folder_name_from_functional_requirement"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
            "functional_requirement": functional_requirement,
            "existing_folder_names": existing_folder_names,
        }

        return self.post_request(endpoint_url, headers, payload, run_state)

    def fix_conformance_tests_issue(
        self,
        frid,
        functional_requirement_id,
        plain_source_tree,
        linked_resources,
        existing_files_content,
        memory_files_content,
        module_name: str,
        conformance_tests_module_name: str,
        required_modules,
        code_diff,
        conformance_tests_files,
        acceptance_tests,
        conformance_tests_issue,
        implementation_fix_count,
        conformance_tests_folder_name,
        current_testing_frid_high_level_implementation_plan: Optional[str],
        run_state: RunState,
    ):
        endpoint_url = f"{self.api_url}/fix_conformance_tests_issue"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
            "functional_requirement_id": functional_requirement_id,
            "plain_source_tree": plain_source_tree,
            "linked_resources": linked_resources,
            "existing_files_content": existing_files_content,
            "memory_files_content": memory_files_content,
            "module_name": module_name,
            "conformance_tests_module_name": conformance_tests_module_name,
            "required_modules": required_modules,
            "code_diff": code_diff,
            "conformance_tests_files": conformance_tests_files,
            "conformance_tests_issue": conformance_tests_issue,
            "implementation_fix_count": implementation_fix_count,
            "conformance_tests_folder_name": conformance_tests_folder_name,
            "current_testing_frid_high_level_implementation_plan": current_testing_frid_high_level_implementation_plan,
        }

        if acceptance_tests is not None:
            payload["acceptance_tests"] = acceptance_tests

        return self.post_request(endpoint_url, headers, payload, run_state)

    def render_acceptance_tests(
        self,
        frid,
        plain_source_tree,
        linked_resources,
        existing_files_content,
        memory_files_content,
        conformance_tests_files,
        module_name: str,
        required_modules,
        acceptance_test,
        run_state: RunState,
    ):
        """
        Renders acceptance tests based on the provided parameters.

        Args:
            frid (str): The unique identifier for the functional requirement.
            plain_source_tree (dict): A dictionary containing the plain source tree.
            linked_resources (dict): A dictionary where the keys represent resource names
                                    and the values are the content of those resources.
            existing_files_content (dict): A dictionary where the keys represent code base
                                        filenames and the values are the content of those files.
            memory_files_content (dict): A dictionary where the keys represent memory filenames
                                        and the values are the content of those files.
            conformance_tests_files (dict): A dictionary containing conformance test files.
            required_modules (dict): A dictionary where the keys represent module names
                                     and the values are lists of functionalities implemented in those modules.
            acceptance_test (dict): A dictionary containing acceptance test information.

        Returns:
            dict: The rendered acceptance tests.

        Raises:
            Exception: If the request fails or returns an error.
        """
        endpoint_url = f"{self.api_url}/render_acceptance_tests"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
            "plain_source_tree": plain_source_tree,
            "linked_resources": linked_resources,
            "existing_files_content": existing_files_content,
            "memory_files_content": memory_files_content,
            "conformance_tests_files": conformance_tests_files,
            "module_name": module_name,
            "required_modules": required_modules,
            "acceptance_test": acceptance_test,
        }

        return self.post_request(endpoint_url, headers, payload, run_state)

    def analyze_rendering(
        self,
        frid,
        plain_source_tree,
        linked_resources,
        existing_files_content,
        module_name: str,
        required_modules,
        implementation_code_diff,
        fixed_implementation_code_diff,
        run_state: RunState,
    ):
        endpoint_url = f"{self.api_url}/analyze_rendering"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
            "plain_source_tree": plain_source_tree,
            "linked_resources": linked_resources,
            "existing_files_content": existing_files_content,
            "module_name": module_name,
            "required_modules": required_modules,
            "implementation_code_diff": implementation_code_diff,
            "fixed_implementation_code_diff": fixed_implementation_code_diff,
        }

        return self.post_request(endpoint_url, headers, payload, run_state)

    def finish_functional_requirement(self, frid, run_state: RunState):
        endpoint_url = f"{self.api_url}/finish_functional_requirement"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
        }

        return self.post_request(endpoint_url, headers, payload, run_state)

    def fail_functional_requirement(self, frid, run_state: RunState):
        endpoint_url = f"{self.api_url}/fail_functional_requirement"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        payload = {
            "frid": frid,
        }

        return self.post_request(endpoint_url, headers, payload, run_state)

    def summarize_finished_conformance_tests(
        self,
        frid,
        plain_source_tree,
        linked_resources,
        conformance_test_files_content,
        module_name: str,
        required_modules,
        run_state: RunState,
    ):
        endpoint_url = f"{self.api_url}/summarize_finished_conformance_tests"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        payload = {
            "frid": frid,
            "plain_source_tree": plain_source_tree,
            "linked_resources": linked_resources,
            "conformance_test_files_content": conformance_test_files_content,
            "module_name": module_name,
            "required_modules": required_modules,
        }

        return self.post_request(endpoint_url, headers, payload, run_state)
