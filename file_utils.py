import os
import shutil
import stat
from pathlib import Path

from liquid2 import Environment, FileSystemLoader, StrictUndefined
from liquid2.exceptions import UndefinedError

import plain_spec
from plain2code_console import console
from plain2code_nodes import Plain2CodeIncludeTag, Plain2CodeLoaderMixin
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


def get_file_type(file_name):

    # Extract the file extension
    ext = Path(file_name).suffix.lower()  # Convert to lowercase to handle case-insensitive matching

    # Use the dictionary to get the file type, defaulting to 'unknown' if the extension is not found
    return FILE_EXTENSION_MAPPING.get(ext, "unknown")


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


# delete a folder and all its subfolders and files
def delete_folder(folder_name):
    if os.path.exists(folder_name):
        # Use writable-aware deletion so read-only files (e.g. .git objects on Windows) don't cause PermissionError
        delete_files_and_subfolders(folder_name)
        _make_writable(folder_name)
        os.rmdir(folder_name)


def _make_writable(path: str) -> None:
    """On Windows, clear read-only so deletion can succeed."""
    # TODO - Check if this can be done in a cleaner way.
    is_windows = os.name == "nt"
    if is_windows:
        try:
            mode = os.stat(path).st_mode
            os.chmod(path, mode | stat.S_IWRITE)
        except OSError:
            pass


def delete_files_and_subfolders(directory):
    total_files_deleted = 0
    total_folders_deleted = 0

    # Walk the directory in reverse order (bottom-up)
    for root, dirs, files in os.walk(directory, topdown=False):
        # Delete files
        for file in files:
            file_path = os.path.join(root, file)
            _make_writable(file_path)
            os.remove(file_path)
            total_files_deleted += 1

        # Delete directories
        for dir_ in dirs:
            dir_path = os.path.join(root, dir_)
            _make_writable(dir_path)
            os.rmdir(dir_path)
            total_folders_deleted += 1


def copy_file(source_path, destination_path):
    # Ensure the destination directory exists
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)

    # Open the source file in read-binary ('rb') mode
    with open(source_path, "rb") as source_file:
        # Open the destination file in write-binary ('wb') mode
        with open(destination_path, "wb") as destination_file:
            # Copy the content from source to destination
            while True:
                # Read a chunk of the source file
                chunk = source_file.read(4096)  # Reading in chunks of 4KB
                if not chunk:
                    break  # End of file reached
                # Write the chunk to the destination file
                destination_file.write(chunk)


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

        with open(full_file_name, "w") as f:
            f.write(response_files[file_name])

        if file_name not in existing_files:
            existing_files.append(file_name)

    return existing_files


def open_from(dirs, file_name):
    for dir in dirs:
        full_file_name = os.path.join(dir, file_name)
        if not os.path.isfile(full_file_name):
            continue

        with open(full_file_name, "rb") as f:
            content = f.read()
            try:
                content_text = content.decode("utf-8")
            except UnicodeDecodeError:
                console.debug(
                    f"WARNING! Error loading {file_name} ({file_name}). File is not a text file. Skipping it."
                )
            return content_text

    return None


def load_linked_resources(template_dirs: list[str], resources_list):
    linked_resources = {}

    for resource in resources_list:
        file_name = resource["target"]
        if file_name in linked_resources:
            continue

        content = open_from(template_dirs, file_name)

        if content is None:
            raise FileNotFoundError(f"""
                Resource file {file_name} not found. Resource files are searched in the following order (highest to lowest precedence):

                1. The directory containing your .plain file
                2. The directory specified by --template-dir (if provided)
                3. The built-in 'standard_template_library' directory

                Please ensure that the resource exists in one of these locations, or specify the correct --template-dir if using custom templates.
                """)

        linked_resources[file_name] = content

    return linked_resources


class TrackingFileSystemLoader(Plain2CodeLoaderMixin, FileSystemLoader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.loaded_templates = {}

    def get_source(self, environment, template_name, **kwargs):
        source = super().get_source(environment, template_name, **kwargs)
        self.loaded_templates[template_name] = source.source
        return source


def get_loaded_templates(source_path, plain_source):
    # Render the plain source with Liquid templating engine
    # to identify the templates that are being loaded

    liquid_loader = TrackingFileSystemLoader(source_path)
    liquid_env = Environment(loader=liquid_loader, undefined=StrictUndefined)
    liquid_env.tags["include"] = Plain2CodeIncludeTag(liquid_env)

    liquid_env.filters["code_variable"] = plain_spec.code_variable_liquid_filter
    liquid_env.filters["prohibited_chars"] = plain_spec.prohibited_chars_liquid_filter

    plain_source_template = liquid_env.from_string(plain_source)
    try:
        plain_source = plain_source_template.render()
    except UndefinedError as e:
        raise Exception(f"Undefined liquid variable: {str(e)}")

    return plain_source, liquid_loader.loaded_templates


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
        os.path.dirname(plain_file_path),  # Highest precedence - directory containing plain file
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
