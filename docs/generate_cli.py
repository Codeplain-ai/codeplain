import os
import sys

# Add the parent directory to the path so we can import plain2code_arguments
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from plain2code_arguments import create_parser

    # Get the parser and generate help text
    parser = create_parser(color=False)  # Disable color for markdown output
    help_text = parser.format_help()

    # Create markdown
    md = "# Plain2Code CLI Reference\n\n```text\n" + help_text + "\n```"

    # Run generate_cli.py in the docs folder

    with open("plain2code_cli.md", "w", encoding="utf-8") as f:
        f.write(md)


if __name__ == "__main__":
    main()
