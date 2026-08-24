import os

import file_utils
from conformance_fix_journal import FixAttemptJournal
from plain2code_console import console

CONFORMANCE_TEST_MEMORY_SUBFOLDER = "conformance_test_memory"


class MemoryManager:

    @staticmethod
    def fetch_memory_files(memory_folder: str) -> tuple[list[str], dict[str, str]]:
        """Fetch memory files from memory_folder/conformance_test_memory."""
        memory_path = os.path.join(memory_folder, CONFORMANCE_TEST_MEMORY_SUBFOLDER)
        if not os.path.exists(memory_path):
            return [], {}
        memory_files = file_utils.list_all_text_files(memory_path)
        memory_files_content = file_utils.get_existing_files_content(memory_path, memory_files)
        console.debug(f"Loaded {len(memory_files_content)} memory files.")
        return memory_files, memory_files_content

    def __init__(self, codeplain_api, memory_folder: str):
        self.codeplain_api = codeplain_api
        self.memory_folder = memory_folder
        self.journal = FixAttemptJournal(memory_folder)

    def store_memory_files(self, response_files: dict[str, str]):
        """Stores distilled memory files, applying deletions (None content) to existing memories."""
        memory_path = os.path.join(self.memory_folder, CONFORMANCE_TEST_MEMORY_SUBFOLDER)
        memory_files, _ = MemoryManager.fetch_memory_files(self.memory_folder)
        file_utils.store_response_files(memory_path, response_files, memory_files)
