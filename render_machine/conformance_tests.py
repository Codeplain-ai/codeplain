import difflib
import json
import os

import file_utils
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from plain_modules import PlainModule

CONFORMANCE_TESTS_DEFINITION_FILE_NAME = "conformance_tests.json"


def _is_insertion_only_change(existing_content: str, new_content: str) -> bool:
    """Check that new_content only inserts lines into existing_content.

    Every existing line must survive, in order - the diff opcodes may contain only
    "equal" and "insert" operations.
    """
    matcher = difflib.SequenceMatcher(
        None, existing_content.splitlines(keepends=True), new_content.splitlines(keepends=True), autojunk=False
    )
    return all(opcode in ("equal", "insert") for opcode, *_ in matcher.get_opcodes())


class ConformanceTests:
    """Manages the state of conformance tests."""

    def __init__(
        self,
        conformance_tests_folder: str,
        conformance_tests_definition_file_name: str,
    ):
        self.conformance_tests_folder = conformance_tests_folder
        self.conformance_tests_definition_file_name = conformance_tests_definition_file_name

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
            console.debug(
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

        modules_list = [module_name] + [m.module_name for m in reversed(required_modules)]

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

    def get_module_suite_run_folder(
        self,
        module_name: str,
        required_modules: list[PlainModule],
        current_testing_module_name: str,
    ) -> str:
        """Resolve the folder to pass to the conformance test script for a whole-module run.

        For the module being rendered this is its own conformance tests folder. For a
        required module it is the most specific existing copy of that module's tests
        (mirroring get_source_conformance_test_folder_name at module granularity), falling
        back to the required module's own folder when no copy exists yet.
        """
        if current_testing_module_name == module_name:
            return self.get_module_conformance_tests_folder(module_name)

        modules_list = [module_name] + [m.module_name for m in reversed(required_modules)]

        for copy_from_module in modules_list:
            if copy_from_module == current_testing_module_name:
                break

            candidate = self.get_module_conformance_tests_folder(copy_from_module + "/." + current_testing_module_name)
            if os.path.exists(candidate):
                return candidate

        return self.get_module_conformance_tests_folder(current_testing_module_name)

    def fetch_all_existing_conformance_test_files(self, module_name: str) -> dict[str, str]:
        """Fetch the content of all existing conformance test files of the module.

        Files are collected from every conformance test subfolder of the module (one subfolder
        per functional requirement) and keyed as "<subfolder>/<relative file path>" so each
        file's suite remains identifiable, plus any shared setup files at the module folder
        root (keyed by their bare file name). Hidden subfolders (copies of required modules'
        tests) and the conformance tests definition file are not included. Returns an empty
        dict when the module has no conformance tests yet.
        """
        all_files_content: dict[str, str] = {}
        module_folder = self.get_module_conformance_tests_folder(module_name)
        for folder_name in sorted(self.fetch_existing_conformance_test_folder_names(module_name)):
            folder_path = os.path.join(module_folder, folder_name)
            file_names = file_utils.list_all_text_files(folder_path)
            files_content = file_utils.get_existing_files_content(folder_path, file_names)
            for file_name, content in files_content.items():
                all_files_content[os.path.join(folder_name, file_name)] = content

        if os.path.isdir(module_folder):
            root_file_names = [
                entry.name
                for entry in os.scandir(module_folder)
                if entry.is_file() and entry.name != self.conformance_tests_definition_file_name
            ]
            root_files_content = file_utils.get_existing_files_content(module_folder, sorted(root_file_names))
            all_files_content.update(root_files_content)

        return all_files_content

    def find_response_file_violations(
        self,
        module_name: str,
        current_subfolder_name: str,
        response_files: dict[str, str],
    ) -> list[str]:
        """Check a conformance tests render response against the two-tier immutability rule.

        Paths are relative to the module's conformance tests folder. Allowed: any file under
        the current functionality's subfolder, new files anywhere outside other
        functionalities' subfolders, and extensions of existing shared setup files at the
        suite root that only insert lines. Violations: files in other functionalities'
        subfolders (or required-module copies), the conformance tests definition file, and
        root-file changes that delete or modify existing lines.
        """
        violations = []
        module_folder = self.get_module_conformance_tests_folder(module_name)
        known_suite_folders = set(self.fetch_existing_conformance_test_folder_names(module_name))

        for file_name, content in response_files.items():
            path_parts = file_name.replace(os.sep, "/").split("/")
            top_level_name = path_parts[0]

            if top_level_name == current_subfolder_name:
                continue

            if len(path_parts) > 1:
                if top_level_name.startswith("."):
                    violations.append(f"{file_name}: files of required modules' test copies must not be changed")
                elif top_level_name in known_suite_folders:
                    violations.append(f"{file_name}: belongs to another functionality's test suite")
                continue

            if file_name == self.conformance_tests_definition_file_name:
                violations.append(f"{file_name}: the conformance tests definition file must not be changed")
                continue

            existing_file_path = os.path.join(module_folder, file_name)
            if os.path.exists(existing_file_path):
                with open(existing_file_path, "r") as f:
                    existing_content = f.read()
                if content is None or not _is_insertion_only_change(existing_content, content):
                    violations.append(
                        f"{file_name}: shared setup files may only be extended by adding lines - "
                        "existing lines must not be changed or removed"
                    )

        return violations

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
