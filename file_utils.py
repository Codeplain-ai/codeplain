import os
import shutil
import stat
from pathlib import Path

from plain_parser.loaders import (  # noqa: F401
    TrackingFileSystemLoader,
    get_loaded_templates,
    load_linked_resources,
    open_from,
)

from plain2code_console import console
from plain_modules import CODEPLAIN_MEMORY_SUBFOLDER, CODEPLAIN_METADATA_FOLDER

BINARY_FILE_EXTENSIONS = [".pyc"]

# Dictionary mapping of file extensions to type names
FILE_EXTENSION_MAPPING = {
    "": "plaintext",
    ".py": "python",
    ".txt": "plaintext",
    ".md": "markdown",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SASS/SCSS",
    ".java": "java",
    ".cpp": "C++",
    ".c": "C",
    ".cs": "C#",
    ".php": "PHP",
    ".rb": "Ruby",
    ".go": "Go",
    ".rs": "Rust",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".sql": "SQL",
    ".json": "JSON",
    ".jsonl": "JSONL",
    ".xml": "XML",
    ".yaml": "YAML",
    ".yml": "YAML",  # YAML has two common extensions
    ".sh": "Shell Script",
    ".bat": "Batch File",
}

SYSTEM_FOLDERS = [".git", CODEPLAIN_METADATA_FOLDER, CODEPLAIN_MEMORY_SUBFOLDER]


def is_system_folder_path(file_path: str) -> bool:
    parts = Path(file_path).parts
    return bool(parts) and parts[0] in SYSTEM_FOLDERS


def list_all_text_files(directory):
    all_files = []
    for root, dirs, files in os.walk(directory, topdown=True):
        # Skip directories that should not be traversed
        for skip_dir in SYSTEM_FOLDERS:
            if skip_dir in dirs:
                dirs.remove(skip_dir)

        modified_root = os.path.relpath(root, directory)
        if modified_root == ".":
            modified_root = ""

        for filename in files:
            if not any(filename.endswith(ending) for ending in BINARY_FILE_EXTENSIONS):
                try:
                    with open(os.path.join(root, filename), "rb") as f:
                        f.read().decode("utf-8")
                except UnicodeDecodeError:
                    console.debug(f"WARNING! Not listing {filename} in {root}. File is not a text file. Skipping it.")
                    continue

                all_files.append(os.path.join(modified_root, filename))

    return all_files


def list_folders_in_directory(directory):
    # List all items in the directory
    items = os.listdir(directory)

    # Filter out the folders
    folders = [item for item in items if os.path.isdir(os.path.join(directory, item))]

    return folders


def _on_rm_error(func, path, _exc_info):
    """On Windows, clear read-only flag and retry the removal."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def delete_folder(folder_name):
    """Delete a folder and all its subfolders and files."""
    if os.path.exists(folder_name):
        shutil.rmtree(folder_name, onerror=_on_rm_error)


def delete_files_and_subfolders(directory):
    """Delete all contents of a directory but keep the directory itself."""
    for entry in os.scandir(directory):
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path, onerror=_on_rm_error)
        else:
            try:
                os.remove(entry.path)
            except PermissionError:
                os.chmod(entry.path, stat.S_IWRITE)
                os.remove(entry.path)


def add_current_path_if_no_path(filename):
    # Extract the base name of the file (ignoring any path information)
    basename = os.path.basename(filename)

    # Compare the basename to the original filename
    # If they are the same, there was no path information in the filename
    if basename == filename:
        # Prepend the current working directory
        return os.path.join(os.getcwd(), filename)
    # If the basename and the original filename differ, path information was present
    return filename


def get_existing_files_content(build_folder, existing_files):
    existing_files_content = {}
    for file_name in existing_files:
        with open(os.path.join(build_folder, file_name), "rb") as f:
            content = f.read()
            try:
                existing_files_content[file_name] = content.decode("utf-8")
            except UnicodeDecodeError:
                console.debug(f"WARNING! Error loading {file_name}. File is not a text file. Skipping it.")

    return existing_files_content


def store_response_files(target_folder, response_files, existing_files):
    for file_name in response_files:
        full_file_name = os.path.join(target_folder, file_name)

        if response_files[file_name] is None:
            # None content indicates that the file should be deleted.
            if os.path.exists(full_file_name):
                os.remove(full_file_name)
                existing_files.remove(file_name)
            else:
                console.debug(f"WARNING! Cannot delete file! File {full_file_name} does not exist.")

            continue

        os.makedirs(os.path.dirname(full_file_name), exist_ok=True)

        with open(full_file_name, "w", encoding="utf-8") as f:
            f.write(response_files[file_name])

        if file_name not in existing_files:
            existing_files.append(file_name)

    return existing_files


def update_build_folder_with_rendered_files(build_folder, existing_files, response_files):
    changed_files = set()
    changed_files.update(response_files.keys())

    existing_files = store_response_files(build_folder, response_files, existing_files)

    return existing_files, changed_files


def copy_folder_content(source_folder, destination_folder, ignore_folders=None):
    """
    Recursively copy all files and folders from source_folder to destination_folder.
    Uses shutil.copytree which handles all edge cases including permissions and symlinks.

    Args:
        source_folder: Source directory to copy from
        destination_folder: Destination directory to copy to
        ignore_folders: List of folder names to ignore during copy (default: empty list)
    """
    if ignore_folders is None:
        ignore_folders = []

    ignore_func = (
        (lambda dir, files: [f for f in files if f in ignore_folders]) if ignore_folders else None  # noqa: U100,U101
    )
    shutil.copytree(source_folder, destination_folder, dirs_exist_ok=True, ignore=ignore_func)


def get_template_directories(plain_file_path, custom_template_dir=None, default_template_dir=None) -> list[str]:
    """Set up template search directories with specific precedence order.

    The order of directories in the returned list determines template loading precedence.
    Earlier indices (lower numbers) have higher precedence - the first matching template found will be used.

    Precedence order (highest to lowest):
    1. Directory containing the plain file - for project-specific template overrides
    2. Custom template directory (if provided) - for shared custom templates
    3. Default template directory - for standard/fallback templates
    """
    template_dirs = [
        os.path.dirname(os.path.abspath(plain_file_path)),  # Highest precedence - directory containing plain file
    ]

    if custom_template_dir:
        template_dirs.append(os.path.abspath(custom_template_dir))  # Second highest - custom template dir

    if default_template_dir:
        # Add standard template directory last - lowest precedence
        template_dirs.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), default_template_dir))

    return template_dirs


def copy_folder_to_output(source_folder, output_folder):
    """Copy source folder contents directly to the specified output folder."""
    # Create output folder if it doesn't exist
    os.makedirs(output_folder, exist_ok=True)

    # If output folder exists, clean it first to ensure clean copy
    if os.path.exists(output_folder):
        delete_files_and_subfolders(output_folder)

    # Copy source folder contents directly to output folder (excluding SYSTEM_FOLDERS)
    copy_folder_content(source_folder, output_folder, ignore_folders=SYSTEM_FOLDERS)
