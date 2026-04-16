from __future__ import annotations

import json
import os

from git.exc import NoSuchPathError

import git_utils
import plain_spec
from plain2code_exceptions import ModuleDoesNotExistError
from render_machine.implementation_code_helpers import ImplementationCodeHelpers

CODEPLAIN_MEMORY_SUBFOLDER = ".memory"
CODEPLAIN_METADATA_FOLDER = ".codeplain"
MODULE_METADATA_FILENAME = "module_metadata.json"
MODULE_FUNCTIONALITIES = "functionalities"
REQUIRED_MODULES_FUNCTIONALITIES = "required_modules_functionalities"


class PlainModule:
    def __init__(self, name: str, build_folder: str):
        self.name = name
        self.build_folder = build_folder

    def get_module_build_folder(self):
        return os.path.join(self.build_folder, self.name)

    def get_codeplain_folder(self):
        return os.path.join(self.get_module_build_folder(), CODEPLAIN_METADATA_FOLDER)

    def get_repo(self):
        try:
            repo = git_utils.get_repo_info(self.get_module_build_folder())
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

    def get_module_source_hash(self, plain_source: dict, resources_list: list[dict]) -> str:
        return plain_spec.get_hash_value([plain_source] + resources_list)

    def get_module_code_hash(self) -> str:
        return ImplementationCodeHelpers.calculate_build_folder_hash(self.get_module_build_folder())

    def has_required_modules_code_changed(
        self,
        required_modules: list[PlainModule] | None,
    ) -> bool:
        if required_modules is None or len(required_modules) == 0:
            return False

        module_metadata = self.load_module_metadata()

        if not module_metadata or "required_modules_code_hash" not in module_metadata:
            return True

        previous_module = required_modules[-1]
        return module_metadata["required_modules_code_hash"] != previous_module.get_module_code_hash()

    def has_plain_spec_changed(self, plain_source: dict, resources_list: list[dict]) -> bool:
        module_metadata = self.load_module_metadata()

        if not module_metadata:
            return True

        if "source_hash" not in module_metadata:
            return True

        return module_metadata["source_hash"] != self.get_module_source_hash(plain_source, resources_list)

    def _get_module_functional_requirements(self, plain_source: dict) -> list[str]:
        module_functional_requirements = []

        for functional_requirement in plain_source[plain_spec.FUNCTIONAL_REQUIREMENTS]:
            module_functional_requirements.append(functional_requirement["markdown"])

        return module_functional_requirements

    def get_functionalities(self) -> dict[str, list[str]]:
        module_metadata = self.load_module_metadata()
        if module_metadata is None:
            raise ModuleDoesNotExistError(f"Module {self.name} does not exist or has no metadata.\n")

        if REQUIRED_MODULES_FUNCTIONALITIES in module_metadata:
            functionalities = module_metadata[REQUIRED_MODULES_FUNCTIONALITIES]
        else:
            functionalities = {}

        functionalities[self.name] = module_metadata[MODULE_FUNCTIONALITIES]

        return functionalities

    def save_module_metadata(
        self,
        plain_source: dict,
        resources_list: list[dict],
        required_modules: list[PlainModule] | None = None,
    ):
        codeplain_folder = self.get_codeplain_folder()
        os.makedirs(codeplain_folder, exist_ok=True)

        module_metadata = {
            "source_hash": self.get_module_source_hash(plain_source, resources_list),
            MODULE_FUNCTIONALITIES: self._get_module_functional_requirements(plain_source),
        }

        if required_modules is not None and len(required_modules) > 0:
            previous_module = required_modules[-1]
            module_metadata["required_modules_code_hash"] = previous_module.get_module_code_hash()

        required_modules_functionalities = {}
        for required_module in required_modules:
            required_modules_functionalities.update(required_module.get_functionalities())

        if required_modules_functionalities:
            module_metadata[REQUIRED_MODULES_FUNCTIONALITIES] = required_modules_functionalities

        metadata_path = os.path.join(codeplain_folder, MODULE_METADATA_FILENAME)
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(module_metadata, f, indent=4)
