from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from functools import cached_property

from plain2code_exceptions import (
    GitNotInstalledError,
    InvalidModuleArchiveError,
    MissingPreviousFunctionalitiesError,
    ModuleDoesNotExistError,
)

try:
    from git import Repo
    from git.exc import NoSuchPathError
except ImportError:
    raise GitNotInstalledError("git is not installed. Please install git and try again.")

import file_utils
import git_utils
import metadata_utils
import plain_file
import plain_spec
from metadata_utils import (
    MODULE_FUNCTIONALITIES,
    MODULE_METADATA_FILENAME,
    REQUIRED_MODULES_FUNCTIONALITIES,
)
from plain2code_console import console
from render_machine.implementation_code_helpers import ImplementationCodeHelpers

CODEPLAIN_MEMORY_SUBFOLDER = ".memory"
CODEPLAIN_METADATA_FOLDER = ".codeplain"
MODULE_CODE_SUBFOLDER = "code"
MODULE_TESTS_SUBFOLDER = "tests"

# A module's build output may be shipped as a single zip archive named
# "<module>.module" instead of an unpacked "<module>/" folder. See PlainModule.materialize
# (read/consume) and PlainModule.ensure_module_unpacked (unpack-on-change).
MODULE_ARCHIVE_EXTENSION = ".module"


def get_module_code_folder(modules_base_folder: str, module_name: str) -> str:
    return os.path.join(modules_base_folder, module_name, MODULE_CODE_SUBFOLDER)


def get_module_tests_folder(modules_base_folder: str, module_name: str) -> str:
    return os.path.join(modules_base_folder, module_name, MODULE_TESTS_SUBFOLDER)


def _validate_module_repo(repo_path: str, subfolder: str, archive_path: str) -> None:
    """Validate one extracted git repo (code/ or tests/) inside a module archive."""
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        raise InvalidModuleArchiveError(
            f"Module archive '{archive_path}' has no git repository in '{subfolder}/' "
            "(the archive must include the '.git' directory)."
        )
    try:
        repo = Repo(repo_path)
        if repo.bare or repo.head.is_detached:
            raise InvalidModuleArchiveError(
                f"The git repository in '{subfolder}/' of module archive '{archive_path}' "
                "must be a working tree checked out on a branch."
            )
    except InvalidModuleArchiveError:
        raise
    except Exception as e:
        raise InvalidModuleArchiveError(
            f"The git repository in '{subfolder}/' of module archive '{archive_path}' is invalid: {e}"
        ) from e


def _validate_module_tree(root: str, archive_path: str) -> None:
    """Verify an extracted module tree. ``code/`` is required. ``tests/`` is optional: a module
    rendered without a conformance-tests script has no tests folder, so its archive has none either.
    When ``tests/`` is present it must be a valid git repo."""
    code_path = os.path.join(root, MODULE_CODE_SUBFOLDER)
    if not os.path.isdir(code_path):
        raise InvalidModuleArchiveError(
            f"Module archive '{archive_path}' is missing the '{MODULE_CODE_SUBFOLDER}/' folder at its root. "
            "The archive must contain the module folder's contents (code/, optionally tests/, ...) at its "
            "root, not nested under a top-level directory."
        )
    _validate_module_repo(code_path, MODULE_CODE_SUBFOLDER, archive_path)

    tests_path = os.path.join(root, MODULE_TESTS_SUBFOLDER)
    if os.path.isdir(tests_path):
        _validate_module_repo(tests_path, MODULE_TESTS_SUBFOLDER, archive_path)


def _extract_module_archive(archive_path: str, dest: str) -> None:
    """Extract a "<module>.module" zip into dest and validate the module layout.

    Guards against zip-slip, restores unix mode bits (zipfile drops them), and validates
    that dest contains valid code/ and tests/ git repositories. Raises
    InvalidModuleArchiveError on any problem.
    """
    if not zipfile.is_zipfile(archive_path):
        raise InvalidModuleArchiveError(f"Module archive '{archive_path}' is not a valid zip file.")

    os.makedirs(dest, exist_ok=True)
    dest_root = os.path.realpath(dest)

    try:
        with zipfile.ZipFile(archive_path) as zf:
            for member in zf.namelist():
                target = os.path.realpath(os.path.join(dest, member))
                if target != dest_root and not target.startswith(dest_root + os.sep):
                    raise InvalidModuleArchiveError(
                        f"Module archive '{archive_path}' contains an unsafe path: {member}"
                    )
            zf.extractall(dest)
            for info in zf.infolist():
                mode = (info.external_attr >> 16) & 0o777
                if not mode:
                    continue
                member_path = os.path.join(dest, info.filename)
                if os.path.exists(member_path) and not os.path.islink(member_path):
                    os.chmod(member_path, mode)
    except zipfile.BadZipFile as e:
        raise InvalidModuleArchiveError(f"Module archive '{archive_path}' is corrupt: {e}") from e

    _validate_module_tree(dest, archive_path)


def _strip_functional_requirements(plain_source_tree: dict) -> dict:
    stripped = {k: v for k, v in plain_source_tree.items() if k != plain_spec.FUNCTIONAL_REQUIREMENTS}
    if "sections" in stripped:
        stripped["sections"] = [_strip_functional_requirements(section) for section in stripped["sections"]]
    return stripped


class PlainModule:
    def __init__(self, filename: str, build_folder: str, template_dirs: list[str]):
        self.filename = filename
        self.build_folder = build_folder
        self.template_dirs = template_dirs
        # When the module exists only as a "<module>.module" archive, these hold the
        # scratch extraction used for read-only consumption. See materialize().
        self._resolved_module_folder: str | None = None
        self._scratch_dir: str | None = None
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
                    self.template_dirs,
                )
                for module_name in required_modules_names
            ]

    @cached_property
    def all_required_modules(self) -> list[PlainModule]:
        all_required_modules = []
        for required_module in self.required_modules:
            if len(required_module.required_modules) > 0:
                all_required_modules.extend(required_module.all_required_modules)

            all_required_modules.append(required_module)

        return all_required_modules

    @property
    def _default_module_folder(self) -> str:
        return os.path.join(self.build_folder, self.module_name)

    @property
    def module_folder(self):
        # When the module was materialized from a "<module>.module" archive, all subpaths
        # resolve against the scratch extraction; otherwise against the real folder.
        if self._resolved_module_folder is not None:
            return self._resolved_module_folder
        return self._default_module_folder

    @property
    def module_archive_path(self) -> str:
        return self._default_module_folder + MODULE_ARCHIVE_EXTENSION

    @property
    def module_conformance_tests_folder(self):
        return os.path.join(self.module_folder, MODULE_TESTS_SUBFOLDER)

    @property
    def module_build_folder(self):
        return os.path.join(self.module_folder, MODULE_CODE_SUBFOLDER)

    @property
    def module_memory_folder(self):
        return os.path.join(self.module_folder, CODEPLAIN_MEMORY_SUBFOLDER)

    def get_codeplain_folder(self):
        return os.path.join(self.module_folder, CODEPLAIN_METADATA_FOLDER)

    def has_module_archive(self) -> bool:
        return os.path.isfile(self.module_archive_path)

    def is_archived_only(self) -> bool:
        return not os.path.isdir(self._default_module_folder) and self.has_module_archive()

    def archive_has_conformance_tests(self) -> bool:
        """True if the "<module>.module" archive contains a tests/ folder. A module rendered without
        a conformance-tests script has no tests/, so its archive has none either."""
        if not self.has_module_archive() or not zipfile.is_zipfile(self.module_archive_path):
            return False
        prefix = MODULE_TESTS_SUBFOLDER + "/"
        with zipfile.ZipFile(self.module_archive_path) as zf:
            return any(name.startswith(prefix) for name in zf.namelist())

    def materialize(self) -> None:
        """Make an archive-only module readable without populating plain_modules/<module>/.

        If the real folder is absent but a "<module>.module" archive exists, extract it to a
        scratch directory and point this module's paths there. No-op if the real folder exists
        or the module is already materialized. The archive file is preserved.
        """
        if self._resolved_module_folder is not None:
            return
        if os.path.isdir(self._default_module_folder):
            return
        if not self.has_module_archive():
            return

        scratch_dir = tempfile.mkdtemp(prefix=f"codeplain-module-{self.module_name}-")
        try:
            _extract_module_archive(self.module_archive_path, scratch_dir)
        except BaseException:
            shutil.rmtree(scratch_dir, ignore_errors=True)
            raise
        self._scratch_dir = scratch_dir
        self._resolved_module_folder = scratch_dir

    def ensure_module_unpacked(self) -> None:
        """Unpack an archive-only module into the real plain_modules/<module>/ in place.

        Called before a module is (re)rendered. Idempotent. If the real folder already exists,
        only a stray archive is removed. Extraction is atomic (extract to a temp sibling under
        the build folder, then os.replace), and the archive is deleted only after success.
        """
        if os.path.isdir(self._default_module_folder):
            if self.has_module_archive():
                os.remove(self.module_archive_path)
            self._reset_scratch()
            self._resolved_module_folder = None
            return

        if not self.has_module_archive():
            return

        os.makedirs(self.build_folder, exist_ok=True)
        staging_dir = tempfile.mkdtemp(prefix=f".{self.module_name}-unpacking-", dir=self.build_folder)
        try:
            _extract_module_archive(self.module_archive_path, staging_dir)
            os.replace(staging_dir, self._default_module_folder)
        except BaseException:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        os.remove(self.module_archive_path)
        self._reset_scratch()
        self._resolved_module_folder = None

    def _reset_scratch(self) -> None:
        if self._scratch_dir is not None:
            shutil.rmtree(self._scratch_dir, ignore_errors=True)
            self._scratch_dir = None

    def cleanup_scratch(self) -> None:
        """Remove any scratch extraction created by materialize(). Safe to call multiple times."""
        self._reset_scratch()
        self._resolved_module_folder = None

    def get_module_render_status(self) -> tuple[str | None, str | None]:
        module_name, frid = git_utils.get_last_rendered_functionality(self.module_build_folder)
        if module_name is not None and module_name == self.module_name:
            return module_name, frid

        for module in reversed(self.all_required_modules):
            last_rendered_module_name, last_rendered_frid = git_utils.get_last_rendered_functionality(
                module.module_build_folder
            )
            if last_rendered_module_name is not None:
                return last_rendered_module_name, last_rendered_frid

        return None, None

    def get_repo(self):
        try:
            repo = git_utils.get_repo_info(self.module_build_folder)
        except NoSuchPathError:
            repo = None

        return repo

    def load_module_metadata(self) -> dict | None:
        return metadata_utils.load_metadata(self.module_metadata_path())

    def update_frid_in_module_metadata(self, frid: str) -> None:
        # Store the raw FR markdown (with any {{ code_variable }} placeholders intact), exactly
        # as save_module_metadata and the change-detection diff read it. Storing the rendered
        # text (code variables already substituted) would make the diff report a spurious edit
        # for every code-variable FR after a partial/interrupted render.
        metadata = self.load_module_metadata() or {}
        functionalities = metadata.get(MODULE_FUNCTIONALITIES, [])
        frid_index = int(frid) - 1
        frid_text = self._get_module_functional_requirements()[frid_index]
        if frid_index < len(functionalities):
            functionalities[frid_index] = frid_text
        else:
            functionalities.append(frid_text)
        metadata[MODULE_FUNCTIONALITIES] = functionalities

        metadata_utils.write_metadata(self.module_metadata_path(), metadata)

    def get_module_source_hash(self) -> str:
        return plain_spec.get_hash_value([self.plain_source] + self.resources_list)

    def get_module_non_functional_source_hash(self) -> str:
        stripped = _strip_functional_requirements(self.plain_source)
        return plain_spec.get_hash_value([stripped] + self.resources_list)

    def get_module_code_hash(self) -> str:
        # Content-only hash (see calculate_build_folder_hash): reading from the resolved (possibly
        # scratch) folder yields the same hash as the in-place folder and the same hash across
        # locations, so archived modules stay portable.
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
        functionalities = {}
        for required_module in self.required_modules:
            functionalities.update(required_module.get_functionalities())

        functionalities[self.module_name] = self._get_module_functional_requirements()

        return functionalities

    def module_metadata_path(self) -> str:
        return os.path.join(self.get_codeplain_folder(), MODULE_METADATA_FILENAME)

    def get_hashes(self) -> dict[str, str]:
        hashes = {
            "source_hash": self.get_module_source_hash(),
            "non_functional_source_hash": self.get_module_non_functional_source_hash(),
        }
        if len(self.required_modules) > 0:
            hashes["required_modules_code_hash"] = self.required_modules[-1].get_module_code_hash()
        return hashes

    def seed_module_metadata(self) -> None:
        """Write a fresh metadata file containing only the module hashes.

        Called at the start of a full render, before any functionality is
        rendered, so change detection has a clean baseline.
        """
        metadata_utils.write_metadata(self.module_metadata_path(), self.get_hashes())

    def truncate_metadata_functionalities(self, frid: str | None) -> None:
        """Trim the stored functionalities list to the first int(frid) entries.

        The metadata file is not tracked in the module's git repo, so when the
        code repo is reverted to an earlier functionality the stored list must
        be trimmed to match the reverted code. A frid of None means no
        functionality is implemented (empty list).
        """
        metadata = self.load_module_metadata()
        if metadata is None:
            return

        keep_count = int(frid) if frid is not None else 0
        if metadata_utils.truncate_functionalities(metadata, keep_count):
            metadata_utils.write_metadata(self.module_metadata_path(), metadata)

    def revert_code_to_frid(self, frid: str | None) -> None:
        """Revert the code repo to the commit for frid and keep metadata in sync."""
        git_utils.revert_to_commit_with_frid(self.module_build_folder, frid)
        self.truncate_metadata_functionalities(frid)

    def reconcile_metadata_with_git(self) -> None:
        """
        Trim the metadata functionalities list to what the code repo actually committed.
        """
        module_name, frid = git_utils.get_last_rendered_functionality(self.module_build_folder)
        own_frid = frid if module_name == self.module_name else None

        self.truncate_metadata_functionalities(own_frid)

    def save_module_metadata(self):
        module_metadata = self.get_hashes()
        module_metadata[MODULE_FUNCTIONALITIES] = self._get_module_functional_requirements()

        required_modules_functionalities = {}
        for required_module in self.required_modules:
            required_modules_functionalities.update(required_module.get_functionalities())

        if required_modules_functionalities:
            module_metadata[REQUIRED_MODULES_FUNCTIONALITIES] = required_modules_functionalities

        metadata_utils.write_metadata(self.module_metadata_path(), module_metadata)

    def _ensure_module_folders_exist(self, first_render_frid: str, render_conformance_tests: bool):
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

    def _raise_for_missing_frid_commits(
        self,
        previous_frids: list[str],
        first_render_frid: str,
        render_conformance_tests: bool,
    ) -> None:
        """
        Ensure commits exist for every previous FRID in both repositories.

        Each repository is asked once for the whole list rather than once per FRID, and the
        first FRID that is missing anywhere decides the error, in the order given.

        Args:
            previous_frids: The FRIDs that should already have been rendered
            first_render_frid: The first FRID in the render range (for error messages)
            render_conformance_tests: Whether to check for conformance tests

        Raises:
            MissingPreviousFunctionalitiesError: If any commit is missing
        """
        missing_in_build = set(
            git_utils.frids_missing_commits(self.module_build_folder, previous_frids, self.module_name)
        )
        missing_in_tests = set()
        if render_conformance_tests:
            try:
                missing_in_tests = set(
                    git_utils.frids_missing_commits(
                        self.module_conformance_tests_folder, previous_frids, self.module_name
                    )
                )
            except Exception:
                # A broken tests repo must not mask the actionable build-repo error below;
                # with nothing missing in the build repo it is a real failure and propagates.
                if not missing_in_build:
                    raise

        for frid in previous_frids:
            if frid in missing_in_build:
                raise MissingPreviousFunctionalitiesError(
                    f"Cannot start rendering from functionality {first_render_frid} for module '{self.module_name}' because the implementation of the previous functionality ({frid}) hasn't been completed yet.\n\n"
                    f"To fix this, please render the missing functionality ({frid}) first by running:\n"
                    f"  codeplain {self.module_name}{plain_file.PLAIN_SOURCE_FILE_EXTENSION} --render-from {frid}"
                )

            if frid in missing_in_tests:
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
        self._ensure_module_folders_exist(first_render_frid, render_conformance_tests)

        # Verify commits exist for all previous FRIDs
        self._raise_for_missing_frid_commits(previous_frids, first_render_frid, render_conformance_tests)

    def get_required_module_by_name(self, module_name: str) -> PlainModule:
        for module in self.all_required_modules:
            if module.module_name == module_name:
                return module

        raise ModuleDoesNotExistError(f"Module {module_name} does not exist")

    def get_next_module(self, module_name: str) -> PlainModule:
        all_modules = self.all_required_modules + [self]
        for idx, module in enumerate(all_modules):
            if module.module_name == module_name and idx < len(all_modules) - 1:
                return all_modules[idx + 1]

        if module_name == self.module_name:
            return None

        raise ModuleDoesNotExistError(f"Module {module_name} does not exist")

    def get_next_frid(self, frid: str, module_name: str) -> tuple[str, PlainModule]:
        if module_name != self.module_name:
            module = self.get_required_module_by_name(module_name)
        else:
            module = self

        next_frid = plain_spec.get_next_frid(module.plain_source, frid)

        if next_frid is None:
            next_module = self.get_next_module(module_name)
            if next_module is None:
                next_module = self
            return plain_spec.get_first_frid(next_module.plain_source), next_module

        return next_frid, module

    def is_module_fully_rendered(self) -> bool:
        frids = list(plain_spec.get_frids(self.plain_source))
        last_rendered_module_name, last_rendered_frid = git_utils.get_last_rendered_functionality(
            self.module_build_folder
        )
        if (
            last_rendered_module_name is not None
            and last_rendered_module_name == self.module_name
            and last_rendered_frid is not None
            and int(last_rendered_frid) >= int(frids[-1])
        ):
            return True

        return False

    def has_no_rendered_functionality(self) -> bool:
        last_rendered_module_name, last_rendered_frid = git_utils.get_last_rendered_functionality(
            self.module_build_folder
        )
        if (
            last_rendered_module_name is not None
            and last_rendered_module_name == self.module_name
            and last_rendered_frid is None
        ):
            return True

        return False

    def wipe_module(self) -> None:
        if os.path.isdir(self._default_module_folder):
            console.warning(f"Wiping module {self._default_module_folder}...")
            file_utils.delete_folder(self._default_module_folder)
        if self.has_module_archive():
            os.remove(self.module_archive_path)
        self._reset_scratch()
        self._resolved_module_folder = None
