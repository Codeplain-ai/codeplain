from __future__ import annotations

import json
import os

from git.exc import NoSuchPathError

import git_utils
import plain_file
import plain_spec
from plain2code_exceptions import MissingPreviousFunctionalitiesError, ModuleDoesNotExistError
from render_machine.implementation_code_helpers import ImplementationCodeHelpers

CODEPLAIN_MEMORY_SUBFOLDER = ".memory"
CODEPLAIN_METADATA_FOLDER = ".codeplain"
MODULE_METADATA_FILENAME = "module_metadata.json"
MODULE_FUNCTIONALITIES = "functionalities"
REQUIRED_MODULES_FUNCTIONALITIES = "required_modules_functionalities"


class PlainModule:
    def __init__(self, filename: str, build_folder: str, conformance_tests_folder: str, template_dirs: list[str]):
        self.filename = filename
        self.build_folder = build_folder
        self.conformance_tests_folder = conformance_tests_folder
        self.template_dirs = template_dirs
        module_name, plain_source, required_modules_names = plain_file.plain_file_parser(
            self.filename, self.template_dirs
        )
        self.module_name = module_name
        resources_list = []
        self.plain_source = plain_source
        self.required_modules_names = required_modules_names
        plain_spec.collect_linked_resources(plain_source, resources_list, None, True)
        self.resources_list = resources_list
        self.required_modules = []
        if len(required_modules_names) > 0:
            self.required_modules = [
                PlainModule(
                    plain_file.get_filename_from_module_name(module_name),
                    self.build_folder,
                    self.conformance_tests_folder,
                    self.template_dirs,
                )
                for module_name in required_modules_names
            ]

    @property
    def all_required_modules(self) -> list[PlainModule]:
        all_required_modules = []
        for required_module in self.required_modules:
            if len(required_module.required_modules) > 0:
                all_required_modules.extend(required_module.all_required_modules)

            all_required_modules.append(required_module)

        return all_required_modules

    @property
    def module_conformance_tests_folder(self):
        return os.path.join(self.conformance_tests_folder, self.module_name)

    @property
    def module_build_folder(self):
        return os.path.join(self.build_folder, self.module_name)

    def get_codeplain_folder(self):
        return os.path.join(self.module_build_folder, CODEPLAIN_METADATA_FOLDER)

    def get_last_rendered_frid(self) -> tuple[str, str | None]:
        if len(self.required_modules) == 0:
            return git_utils.get_last_finished_frid(self.module_build_folder)

        module_name, frid = git_utils.get_last_finished_frid(self.module_build_folder)
        if module_name is not None and frid is not None:
            return module_name, frid

        for module in reversed(self.required_modules):
            last_rendered_module, last_rendered_frid = module.get_last_rendered_frid()
            if last_rendered_module is not None and last_rendered_frid is not None:
                return last_rendered_module, last_rendered_frid

        return None, None

    def get_repo(self):
        try:
            repo = git_utils.get_repo_info(self.module_build_folder)
        except NoSuchPathError:
            repo = None

        return repo

    def load_module_metadata(self) -> dict | None:
        codeplain_folder = self.get_codeplain_folder()
        if not os.path.exists(codeplain_folder):
            return None

        metadata_path = os.path.join(codeplain_folder, MODULE_METADATA_FILENAME)
        if not os.path.exists(metadata_path):
            return None

        with open(metadata_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_module_source_hash(self) -> str:
        return plain_spec.get_hash_value([self.plain_source] + self.resources_list)

    def get_module_code_hash(self) -> str:
        return ImplementationCodeHelpers.calculate_build_folder_hash(self.module_build_folder)

    def has_required_modules_code_changed(
        self,
    ) -> bool:
        if self.required_modules is None or len(self.required_modules) == 0:
            return False

        module_metadata = self.load_module_metadata()

        if not module_metadata or "required_modules_code_hash" not in module_metadata:
            return True

        previous_module = self.required_modules[-1]
        return module_metadata["required_modules_code_hash"] != previous_module.get_module_code_hash()

    def has_plain_spec_changed(self) -> bool:
        module_metadata = self.load_module_metadata()

        if not module_metadata:
            return True

        if "source_hash" not in module_metadata:
            return True

        return module_metadata["source_hash"] != self.get_module_source_hash()

    def _get_module_functional_requirements(self) -> list[str]:
        module_functional_requirements = []

        for functional_requirement in self.plain_source[plain_spec.FUNCTIONAL_REQUIREMENTS]:
            module_functional_requirements.append(functional_requirement["markdown"])

        return module_functional_requirements

    def get_functionalities(self) -> dict[str, list[str]]:
        module_metadata = self.load_module_metadata()
        if module_metadata is None:
            raise ModuleDoesNotExistError(f"Module {self.module_name} does not exist or has no metadata.")

        if REQUIRED_MODULES_FUNCTIONALITIES in module_metadata:
            functionalities = module_metadata[REQUIRED_MODULES_FUNCTIONALITIES]
        else:
            functionalities = {}

        functionalities[self.module_name] = module_metadata[MODULE_FUNCTIONALITIES]

        return functionalities

    def save_module_metadata(
        self,
    ):
        codeplain_folder = self.get_codeplain_folder()
        os.makedirs(codeplain_folder, exist_ok=True)

        module_metadata = {
            "source_hash": self.get_module_source_hash(),
            MODULE_FUNCTIONALITIES: self._get_module_functional_requirements(),
        }

        if self.required_modules is not None and len(self.required_modules) > 0:
            previous_module = self.required_modules[-1]
            module_metadata["required_modules_code_hash"] = previous_module.get_module_code_hash()

        required_modules_functionalities = {}
        for required_module in self.required_modules:
            required_modules_functionalities.update(required_module.get_functionalities())

        if required_modules_functionalities:
            module_metadata[REQUIRED_MODULES_FUNCTIONALITIES] = required_modules_functionalities

        metadata_path = os.path.join(codeplain_folder, MODULE_METADATA_FILENAME)
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(module_metadata, f, indent=4)

    def _ensure_module_folders_exist(self, first_render_frid: str, render_conformance_tests: bool) -> tuple[str, str]:
        """
        Ensure that build and conformance test folders exist for the module.

        Args:
            first_render_frid: The first FRID in the render range

        Returns:
            tuple[str, str]: (build_folder_path, conformance_tests_path)

        Raises:
            MissingPreviousFridCommitsError: If any required folders are missing
        """

        if not os.path.exists(self.module_build_folder):
            raise MissingPreviousFunctionalitiesError(
                f"Cannot start rendering from functionality {first_render_frid} for module '{self.module_name}' because the source code folder does not exist.\n\n"
                f"To fix this, please render the module from the beginning by running:\n"
                f"  codeplain {self.module_name}{plain_file.PLAIN_SOURCE_FILE_EXTENSION}"
            )

        if not os.path.exists(self.module_conformance_tests_folder) and render_conformance_tests:
            raise MissingPreviousFunctionalitiesError(
                f"Cannot start rendering from functionality {first_render_frid} for module '{self.module_name}' because the conformance tests folder does not exist.\n\n"
                f"To fix this, please render the module from the beginning by running:\n"
                f"  codeplain {self.module_name}{plain_file.PLAIN_SOURCE_FILE_EXTENSION}"
            )

    def _ensure_frid_commit_exists(
        self,
        frid: str,
        first_render_frid: str,
        render_conformance_tests: bool,
    ) -> None:
        """
        Ensure commit exists for a single FRID in both repositories.

        Args:
            frid: The FRID to check
            first_render_frid: The first FRID in the render range (for error messages)
            render_conformance_tests: Whether to check for conformance tests

        Raises:
            MissingPreviousFridCommitsError: If the commit is missing
        """
        # Check in build folder
        if not git_utils.has_commit_for_frid(self.module_build_folder, frid, self.module_name):
            raise MissingPreviousFunctionalitiesError(
                f"Cannot start rendering from functionality {first_render_frid} for module '{self.module_name}' because the implementation of the previous functionality ({frid}) hasn't been completed yet.\n\n"
                f"To fix this, please render the missing functionality ({frid}) first by running:\n"
                f"  codeplain {self.module_name}{plain_file.PLAIN_SOURCE_FILE_EXTENSION} --render-from {frid}"
            )

        # Check in conformance tests folder (only if conformance tests are enabled)
        if render_conformance_tests:
            if not git_utils.has_commit_for_frid(self.module_conformance_tests_folder, frid, self.module_name):
                raise MissingPreviousFunctionalitiesError(
                    f"Cannot start rendering from functionality {first_render_frid} for module '{self.module_name}' because the conformance tests for the previous functionality ({frid}) haven't been completed yet.\n\n"
                    f"To fix this, please render the missing functionality ({frid}) first by running:\n"
                    f"  codeplain {self.module_name}{plain_file.PLAIN_SOURCE_FILE_EXTENSION} --render-from {frid}"
                )

    def ensure_previous_frid_commits_exist(self, render_range: list[str], render_conformance_tests: bool) -> None:
        """
        Ensure that all FRID commits before the render_range exist.

        This is a precondition check that must pass before rendering can proceed.
        Raises an exception if any previous FRID commits are missing.

        Args:
            render_range: List of FRIDs to render
            render_conformance_tests: Whether to check for conformance tests

        Raises:
            MissingPreviousFridCommitsError: If any previous FRID commits are missing
        """
        first_render_frid = render_range[0]

        # Get all FRIDs that should have been rendered before this one
        previous_frids = plain_spec.get_frids_before(self.plain_source, first_render_frid)
        if not previous_frids:
            return

        # Ensure the module folders exist
        self._ensure_module_folders_exist(first_render_frid)

        # Verify commits exist for all previous FRIDs
        for prev_frid in previous_frids:
            self._ensure_frid_commit_exists(prev_frid, first_render_frid, render_conformance_tests)
