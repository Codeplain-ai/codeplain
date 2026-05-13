import logging
import re

from rich.console import Console
from rich.style import Style
from rich.tree import Tree

import plain2code_logger

CHARACTERS_TO_TOKENS_RULE_OF_THUMB_RATIO = 4

# Pattern to strip Rich markup tags (e.g. [#FF6B6B], [bold], [/bold])
_RICH_MARKUP_PATTERN = re.compile(r"\[/?[^\]]*\]")


def _sanitize_for_logger(text: str) -> str:
    """Strip Rich markup and replace non-ASCII chars for safe logging on all platforms."""
    return _RICH_MARKUP_PATTERN.sub("", text).encode("ascii", "replace").decode("ascii")


logger = logging.getLogger(plain2code_logger.LOGGER_NAME)


class Plain2CodeConsole(Console):
    INFO_STYLE = Style()
    WARNING_STYLE = Style(color="yellow", bold=True)
    ERROR_STYLE = Style(color="red", bold=True)
    INPUT_STYLE = Style(color="#4169E1")  # Royal Blue
    OUTPUT_STYLE = Style(color="green")
    DEBUG_STYLE = Style(color="purple")

    def __init__(self):
        super().__init__()
        try:
            import tiktoken

            self.llm_encoding = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.warning(
                "Failed to import optional library tiktoken. Using approximate instead of exact token count."
            )
            logger.debug(f"Exception: {e}")
            self.llm_encoding = None

    def info(self, *args, **kwargs):
        logger.info(_sanitize_for_logger(" ".join(map(str, args))))
        super().print(*args, **kwargs, style=self.INFO_STYLE)

    def warning(self, *args, **kwargs):
        logger.warning(_sanitize_for_logger(" ".join(map(str, args))))
        super().print(*args, **kwargs, style=self.WARNING_STYLE)

    def error(self, *args, **kwargs):
        logger.error(_sanitize_for_logger(" ".join(map(str, args))))
        super().print(*args, **kwargs, style=self.ERROR_STYLE)

    def input(self, *args, **kwargs):
        # We also log input as info so it shows in the toggled view
        logger.info(_sanitize_for_logger(" ".join(map(str, args))))
        super().print(*args, **kwargs, style=self.INPUT_STYLE)

    def output(self, *args, **kwargs):
        logger.info(_sanitize_for_logger(" ".join(map(str, args))))
        super().print(*args, **kwargs, style=self.OUTPUT_STYLE)

    def debug(self, *args, **kwargs):
        logger.debug(_sanitize_for_logger(" ".join(map(str, args))))
        super().print(*args, **kwargs, style=self.DEBUG_STYLE)

    def print_list(self, items, style=None):
        for item in items:
            super().print(f"{item}", style=style)

    def print_files(self, header, root_folder, files, style=None):
        if not files:
            return

        tree = self._create_tree_from_files(root_folder, files)
        super().print(f"\n{header}", style=style)

        super().print(tree, style=style)

        super().print()

    def _create_tree_from_files(self, root_folder, files):
        """
        Creates a Tree structure from a dictionary of files using the rich library.

        Args:
            files (dict): A dictionary where keys are file paths (strings)
                            and values are file content (strings).

        Returns:
            Tree: The root of the created tree structure.
        """
        tree = Tree(root_folder)
        for path, content in files.items():
            parts = path.split("/")
            current_level = tree
            for part in parts:
                existing_level = None
                for child in current_level.children:
                    if child.label == part:
                        existing_level = child
                        break

                if existing_level is None:
                    if part == parts[-1]:
                        if files[path] is None:
                            current_level = current_level.add(f"{part} [red]deleted[/red]")
                        else:
                            file_lines = len(content.splitlines())
                            file_tokens = self._count_tokens(content)
                            current_level = current_level.add(f"{part} ({file_lines} lines, {file_tokens} tokens)")
                    else:
                        current_level = current_level.add(part)
                else:
                    current_level = existing_level

        return tree

    def _count_tokens(self, text):
        """Count tokens using tiktoken if available, otherwise estimate from character count."""
        if self.llm_encoding is not None:
            try:
                return len(self.llm_encoding.encode(text))
            except Exception:
                pass
        return len(text) // CHARACTERS_TO_TOKENS_RULE_OF_THUMB_RATIO

    def print_resources(self, resources_list, linked_resources):
        if len(resources_list) == 0:
            self.debug("Linked resources: None")
            return

        self.debug("Linked resources:")
        for resource_name in resources_list:
            if resource_name["target"] in linked_resources:
                file_tokens = self._count_tokens(linked_resources[resource_name["target"]])
                self.debug(
                    f"- {resource_name['text']} [#4169E1]({resource_name['target']}, {file_tokens} tokens)[/#4169E1]"
                )

        self.input()


console = Plain2CodeConsole()
