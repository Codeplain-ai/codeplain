import json
import os

import file_utils
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from plain_modules import PlainModule

CONFORMANCE_TESTS_DEFINITION_FILE_NAME = "conformance_tests.json"


class ConformanceTests:
    """Manages the state of conformance tests."""

    def __init__(
        self,
        conformance_tests_folder: str,
        conformance_tests_definition_file_name: str,
        verbose: bool,
    ):
        self.conformance_tests_folder = conformance_tests_folder
        self.conformance_tests_definition_file_name = conformance_tests_definition_file_name
        self.verbose = verbose

    def get_module_conformance_tests_folder(self, module_name: str) -> str:
        return os.path.join(self.conformance_tests_folder, module_name)

    def _get_full_conformance_tests_definition_file_name(self, module_name: str) -> str:
        return os.path.join(
            self.get_module_conformance_tests_folder(module_name),
            self.conformance_tests_definition_file_name,
        )

    def get_conformance_tests_json(self, module_name: str) -> dict:
        try:
            with open(self._get_full_conformance_tests_definition_file_name(module_name), "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def dump_conformance_tests_json(self, module_name: str, conformance_tests_json: dict) -> None:
        """Dump the conformance tests definition to the file."""
        if os.path.exists(self.get_module_conformance_tests_folder(module_name)):
            if self.verbose:
                console.info(
                    f"Storing conformance tests definition to {self._get_full_conformance_tests_definition_file_name(module_name)}"
                )
            with open(self._get_full_conformance_tests_definition_file_name(module_name), "w") as f:
                json.dump(conformance_tests_json, f, indent=4)

    def fetch_existing_conformance_test_folder_names(self, module_name: str) -> list[str]:
        if os.path.isdir(self.get_module_conformance_tests_folder(module_name)):
            existing_folder_names = file_utils.list_folders_in_directory(
                self.get_module_conformance_tests_folder(module_name)
            )
            # Remove hidden folders (those starting with '.')
            existing_folder_names = [folder for folder in existing_folder_names if not folder.startswith(".")]
        else:
            # This happens if we're rendering the first FRID (without previously created conformance tests)
            existing_folder_names = []

        return existing_folder_names

    def get_source_conformance_test_folder_name(
        self,
        module_name: str,
        required_modules: list[PlainModule],
        current_testing_module_name: str,
        original_conformance_test_folder_name: str,
    ) -> tuple[str, str]:
        original_prefix = self.get_module_conformance_tests_folder(current_testing_module_name)
        if not original_conformance_test_folder_name.startswith(original_prefix):
            raise InternalClientError(
                f"Unexpected conformance test folder name prefix {original_prefix} for {original_conformance_test_folder_name}!"
            )

        conformance_test_subfolder_name = original_conformance_test_folder_name[len(original_prefix) :]

        modules_list = [module_name] + [m.name for m in reversed(required_modules)]

        for copy_from_module in modules_list:
            if copy_from_module == current_testing_module_name:
                source_conformance_test_folder_name = original_conformance_test_folder_name
                break

            source_conformance_test_folder_name = (
                self.get_module_conformance_tests_folder(copy_from_module + "/." + current_testing_module_name)
                + conformance_test_subfolder_name
            )

            if os.path.exists(source_conformance_test_folder_name):
                break

        new_conformance_test_folder_name = (
            self.get_module_conformance_tests_folder(module_name + "/." + current_testing_module_name)
            + conformance_test_subfolder_name
        )

        return source_conformance_test_folder_name, new_conformance_test_folder_name

    def store_conformance_tests_files(
        self,
        module_name: str,
        required_modules: list[PlainModule],
        current_testing_module_name: str,
        current_conformance_test_folder_name: str,
        response_files: dict[str, str],
        existing_conformance_test_files: list[str],
    ):
        if module_name != current_testing_module_name:
            console.info(
                f"Storing conformance tests files for module '{current_testing_module_name}' inside module '{module_name}'"
            )

            [source_conformance_test_folder_name, new_conformance_test_folder_name] = (
                self.get_source_conformance_test_folder_name(
                    module_name,
                    required_modules,
                    current_testing_module_name,
                    current_conformance_test_folder_name,
                )
            )

            if source_conformance_test_folder_name != module_name:
                console.info(
                    f"Creating folder {new_conformance_test_folder_name} for a copy of conformance tests {source_conformance_test_folder_name}"
                )

                if not os.path.exists(new_conformance_test_folder_name):
                    file_utils.copy_folder_content(
                        source_conformance_test_folder_name,
                        new_conformance_test_folder_name,
                    )

            current_conformance_test_folder_name = new_conformance_test_folder_name

        file_utils.store_response_files(
            current_conformance_test_folder_name,
            response_files,
            existing_conformance_test_files,
        )

        if self.verbose:
            console.print_files(
                "Conformance test files fixed:",
                current_conformance_test_folder_name,
                response_files,
                style=console.OUTPUT_STYLE,
            )

    def fetch_existing_conformance_test_files(
        self,
        module_name: str,
        required_modules: list[PlainModule],
        current_testing_module_name: str,
        current_conformance_test_folder_name: str,
    ) -> tuple[list[str], dict[str, str]]:
        if module_name != current_testing_module_name:
            [current_conformance_test_folder_name, _] = self.get_source_conformance_test_folder_name(
                module_name,
                required_modules,
                current_testing_module_name,
                current_conformance_test_folder_name,
            )

        existing_conformance_test_files = file_utils.list_all_text_files(current_conformance_test_folder_name)
        existing_conformance_test_files_content = file_utils.get_existing_files_content(
            current_conformance_test_folder_name, existing_conformance_test_files
        )
        return existing_conformance_test_files, existing_conformance_test_files_content
