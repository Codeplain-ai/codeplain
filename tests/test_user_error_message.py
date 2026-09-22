from liquid2.exceptions import TemplateNotFoundError

from plain2code import user_error_message

SEARCH_ORDER_STEPS = [
    "1. The directory containing your .plain file",
    "2. The directory specified by --template-dir (if provided)",
    "3. The built-in 'standard_template_library' directory",
]


def test_template_not_found_keeps_parser_message_and_adds_search_order():
    message = user_error_message(TemplateNotFoundError("Template not found: 'nope.plain'"))

    # liquid2 repr-quotes a bare message when no token is attached; the CLI attaches one.
    assert "Template not found: 'nope.plain'" in message
    for step in SEARCH_ORDER_STEPS:
        assert step in message
    assert "missing template exists" in message


def test_resource_not_found_keeps_parser_message_and_adds_search_order():
    message = user_error_message(FileNotFoundError("Resource file 'x.md' referenced in module 'm' not found."))

    assert message.startswith("Resource file 'x.md' referenced in module 'm' not found.")
    for step in SEARCH_ORDER_STEPS:
        assert step in message
    assert "resource exists" in message


def test_other_errors_are_unchanged():
    assert user_error_message(ValueError("boom")) == "boom"


def test_empty_message_falls_back_to_repr():
    assert user_error_message(RuntimeError()) == "RuntimeError()"
