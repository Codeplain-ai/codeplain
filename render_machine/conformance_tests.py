import json
import os
from typing import Callable, Optional

import file_utils
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from plain_modules import PlainModule, get_module_tests_folder

CONFORMANCE_TESTS_DEFINITION_FILE_NAME = "conformance_tests.json"


class ConformanceTests:
    """Manages the state of conformance tests."""

    def __init__(
        self,
        modules_base_folder: str,
        conformance_tests_definition_file_name: str,
        resolve_module_tests_folder: Optional[Callable[[str], Optional[str]]] = None,
    ):
        self.modules_base_folder = modules_base_folder
        self.conformance_tests_definition_file_name = conformance_tests_definition_file_name
        # Optional resolver mapping a module name to its resolved tests folder. Lets an
        # archive-only ("<module>.module") required module resolve to its scratch extraction
        # instead of the (non-existent) default plain_modules/<module>/tests path.
        self._resolve_module_tests_folder = resolve_module_tests_folder

    def get_module_conformance_tests_folder(self, module_name: str) -> str:
        if self._resolve_module_tests_folder is not None:
            resolved = self._resolve_module_tests_folder(module_name)
            if resolved is not None:
                return resolved
        return get_module_tests_folder(self.modules_base_folder, module_name)

    def _get_full_conformance_tests_definition_file_name(self, module_name: str) -> str:
        return os.path.join(
            self.get_module_conformance_tests_folder(module_name),
            self.conformance_tests_definition_file_name,
        )

    def _resolve_folder_names(self, module_name: str, conformance_tests_json: dict) -> dict:
        """Turn each entry's on-disk relative ``folder_name`` into an absolute path rooted at the
        module's (resolved) tests folder. An already-absolute value is left as-is, so archives
        written by older builds still load."""
        base = self.get_module_conformance_tests_folder(module_name)
        resolved: dict = {}
        for frid, entry in conformance_tests_json.items():
            if isinstance(entry, dict) and "folder_name" in entry:
                entry = {**entry, "folder_name": os.path.join(base, entry["folder_name"])}
            resolved[frid] = entry
        return resolved

    def _relativize_folder_names(self, module_name: str, conformance_tests_json: dict) -> dict:
        """Turn each entry's absolute in-memory ``folder_name`` into a path relative to the module's
        tests folder, so the stored definition is location-independent (portable across projects and
        usable from a "<module>.module" archive). Does not mutate the input dict."""
        base = self.get_module_conformance_tests_folder(module_name)
        serializable: dict = {}
        for frid, entry in conformance_tests_json.items():
            if isinstance(entry, dict) and "folder_name" in entry:
                entry = {**entry, "folder_name": os.path.relpath(entry["folder_name"], base)}
            serializable[frid] = entry
        return serializable

    def get_conformance_tests_json(self, module_name: str) -> dict:
        try:
            with open(self._get_full_conformance_tests_definition_file_name(module_name), "r") as f:
                conformance_tests_json = json.load(f)
        except FileNotFoundError:
            return {}
        return self._resolve_folder_names(module_name, conformance_tests_json)

    def dump_conformance_tests_json(self, module_name: str, conformance_tests_json: dict) -> None:
        """Dump the conformance tests definition to the file. Folder names are stored relative to the
        module's tests folder so the definition is portable and works from a scratch extraction."""
        if os.path.exists(self.get_module_conformance_tests_folder(module_name)):
            console.debug(
                f"Storing conformance tests definition to {self._get_full_conformance_tests_definition_file_name(module_name)}"
            )
            serializable = self._relativize_folder_names(module_name, conformance_tests_json)
            with open(self._get_full_conformance_tests_definition_file_name(module_name), "w") as f:
                json.dump(serializable, f, indent=4)

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

        modules_list = [module_name] + [m.module_name for m in reversed(required_modules)]

        for copy_from_module in modules_list:
            if copy_from_module == current_testing_module_name:
                source_conformance_test_folder_name = original_conformance_test_folder_name
                break

            source_conformance_test_folder_name = (
                os.path.join(
                    self.get_module_conformance_tests_folder(copy_from_module), "." + current_testing_module_name
                )
                + conformance_test_subfolder_name
            )

            if os.path.exists(source_conformance_test_folder_name):
                break

        new_conformance_test_folder_name = (
            os.path.join(self.get_module_conformance_tests_folder(module_name), "." + current_testing_module_name)
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
            console.debug(
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
                console.debug(
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

        console.print_files(
            "Conformance test files fixed:",
            current_conformance_test_folder_name,
            response_files,
            style=console.OUTPUT_STYLE,
        )

    def fetch_all_existing_conformance_test_files(self, module_name: str) -> dict[str, str]:
        """Fetch the content of all existing conformance test files of the module.

        Files are collected from every conformance test subfolder of the module (one subfolder
        per functional requirement) and keyed as "<subfolder>/<relative file path>" so each
        file's suite remains identifiable. Hidden subfolders (copies of required modules' tests)
        and the conformance tests definition file (stored at the module folder root) are not
        included. Returns an empty dict when the module has no conformance tests yet.
        """
        all_files_content: dict[str, str] = {}
        module_folder = self.get_module_conformance_tests_folder(module_name)
        for folder_name in sorted(self.fetch_existing_conformance_test_folder_names(module_name)):
            folder_path = os.path.join(module_folder, folder_name)
            file_names = file_utils.list_all_text_files(folder_path)
            files_content = file_utils.get_existing_files_content(folder_path, file_names)
            for file_name, content in files_content.items():
                all_files_content[os.path.join(folder_name, file_name)] = content

        return all_files_content

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
