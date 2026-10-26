import os
import sys

# Put the checkout first on the path so we read its plain2code_arguments, not an
# installed copy of codeplain that would reflect whatever was last released.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from plain2code_arguments import create_parser

    # Get the parser and generate help text
    parser = create_parser()  # Disable color for markdown output
    help_text = parser.format_help()

    # Create markdown
    md = "# Plain2Code CLI Reference\n\n```text\n" + help_text + "\n```"

    # Run generate_cli.py in the docs folder

    with open("plain2code_cli.md", "w", encoding="utf-8") as f:
        f.write(md)


if __name__ == "__main__":
    main()
