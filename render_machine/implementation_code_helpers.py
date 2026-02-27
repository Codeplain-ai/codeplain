import json

import file_utils
import git_utils
import plain_spec


class ImplementationCodeHelpers:
    @staticmethod
    def calculate_build_folder_hash(build_folder: str) -> str:
        _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(build_folder)
        return plain_spec.hash_text(f"folder={build_folder}|{json.dumps(existing_files_content)}")

    @staticmethod
    def fetch_existing_files(build_folder: str):
        existing_files = file_utils.list_all_text_files(build_folder)
        existing_files_content = file_utils.get_existing_files_content(build_folder, existing_files)
        return existing_files, existing_files_content

    @staticmethod
    def remove_system_folder_paths_from_code_diff(code_diff: dict):
        for file_name in list(code_diff.keys()):
            if file_utils.is_system_folder_path(file_name):
                del code_diff[file_name]

        return code_diff

    @staticmethod
    def get_code_diff(build_folder: str, plain_source_tree: dict, frid: str):
        previous_frid_code_diff = git_utils.diff(
            build_folder,
            plain_spec.get_previous_frid(plain_source_tree, frid),
        )

        return ImplementationCodeHelpers.remove_system_folder_paths_from_code_diff(previous_frid_code_diff)

    @staticmethod
    def get_fixed_implementation_code_diff(build_folder: str, frid: str):
        fixed_implementation_code_diff = git_utils.get_fixed_implementation_code_diff(build_folder, frid)
        if fixed_implementation_code_diff is None:
            return None

        return ImplementationCodeHelpers.remove_system_folder_paths_from_code_diff(fixed_implementation_code_diff)

    @staticmethod
    def get_implementation_code_diff(build_folder: str, frid: str, previous_frid: str):
        implementation_code_diff = git_utils.get_implementation_code_diff(build_folder, frid, previous_frid)

        return ImplementationCodeHelpers.remove_system_folder_paths_from_code_diff(implementation_code_diff)
