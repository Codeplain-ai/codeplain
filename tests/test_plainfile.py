from unittest.mock import MagicMock, patch

import pytest
from mistletoe.block_token import List, ListItem, Paragraph
from mistletoe.span_token import Emphasis, RawText, Strong

import plain_file
import plain_spec
from plain2code_exceptions import PlainSyntaxError
from plain_file import process_acceptance_tests


class MockParseBuffer(list):
    def __init__(self, *args, loose=False):
        super().__init__(*args)
        self.loose = loose


def _create_mock_fr_content(content="Implement the entry point for ...", line_number=32):
    """Helper to create a mock functional requirement content paragraph."""
    mock_fr_content_text = MagicMock(spec=RawText)
    mock_fr_content_text.content = content

    mock_fr_content = MagicMock(spec=Paragraph)
    mock_fr_content.children = [mock_fr_content_text]
    mock_fr_content.line_number = line_number

    return mock_fr_content


def _create_mock_at_heading_paragraph(line_number=34):
    """Helper to create a mock acceptance test heading paragraph with proper structure."""
    mock_at_heading_raw_text = MagicMock(spec=RawText)
    mock_at_heading_raw_text.content = plain_spec.ACCEPTANCE_TEST_HEADING

    mock_at_heading_strong = MagicMock(spec=Strong)
    mock_at_heading_strong.children = [mock_at_heading_raw_text]

    mock_at_heading_emphasis = MagicMock(spec=Emphasis)
    mock_at_heading_emphasis.children = [mock_at_heading_strong]

    mock_at_heading_paragraph = MagicMock(spec=Paragraph)
    mock_at_heading_paragraph.children = [mock_at_heading_emphasis]
    mock_at_heading_paragraph.line_number = line_number

    return mock_at_heading_paragraph


def _create_mock_at_list(content="The App shouldn't show logging...", line_number=36):
    """Helper to create a mock acceptance test list with a single item."""
    mock_at_item_raw_text = MagicMock(spec=RawText)
    mock_at_item_raw_text.content = content

    mock_at_item_paragraph = MagicMock(spec=Paragraph)
    mock_at_item_paragraph.children = [mock_at_item_raw_text]
    mock_at_item_paragraph.line_number = line_number

    mock_at_item = MagicMock(spec=ListItem)
    mock_at_item.children = [mock_at_item_paragraph]
    mock_at_item.line_number = line_number

    mock_at_list = MagicMock(spec=List)
    mock_at_list.children = [mock_at_item]
    mock_at_list.line_number = line_number

    return mock_at_list


@patch("plain_file._process_single_acceptance_test_requirement")
def test_process_acceptance_tests_no_functional_requirements_key(mock_process_single):
    """Tests early return when functional requirements key is missing."""
    plain_source_tree = {}

    process_acceptance_tests(plain_source_tree)

    mock_process_single.assert_not_called()


@patch("plain_file._process_single_acceptance_test_requirement")
def test_process_acceptance_tests_frs_lacks_children_attr(mock_process_single):
    """Tests early return when frs object lacks children attribute."""
    plain_source_tree = {plain_spec.FUNCTIONAL_REQUIREMENTS: None}

    process_acceptance_tests(plain_source_tree)

    mock_process_single.assert_not_called()


@patch("plain_file._process_single_acceptance_test_requirement")
def test_process_acceptance_tests_no_sections_direct_frs(mock_process_single):
    """Tests processing of top-level functional requirements when no sections exist."""
    mock_fr_item1_with_children = MagicMock(name="FRItem1_WithChildren")
    mock_fr_item1_with_children.children = MagicMock(name="ChildrenOfFRItem1")

    mock_fr_item2_no_children = MagicMock(spec=[], name="FRItem2_NoChildren")

    mock_fr_item3_with_children = MagicMock(name="FRItem3_WithChildren")
    mock_fr_item3_with_children.children = MagicMock(name="ChildrenOfFRItem3")

    mock_frs_top_level = MagicMock(name="TopLevelFRS")
    mock_frs_top_level.children = [
        mock_fr_item1_with_children,
        mock_fr_item2_no_children,
        mock_fr_item3_with_children,
    ]

    plain_source_tree = {
        plain_spec.FUNCTIONAL_REQUIREMENTS: mock_frs_top_level,
        "definitions": MagicMock(name="TopLevelDefs"),
        "implementation reqs": MagicMock(name="TopLevelNFRs"),
    }

    process_acceptance_tests(plain_source_tree)

    assert mock_process_single.call_count == 2
    mock_process_single.assert_any_call(mock_fr_item1_with_children)
    mock_process_single.assert_any_call(mock_fr_item3_with_children)


def test_psart_no_acceptance_tests_present():
    """Tests behavior when no 'Acceptance test:' heading is present."""
    functional_requirement_mock = MagicMock(spec=ListItem)
    functional_requirement_mock.line_number = 1

    mock_paragraph1 = MagicMock(spec=Paragraph)
    mock_paragraph1.children = []
    mock_paragraph2 = MagicMock(spec=Paragraph)
    mock_paragraph2.children = []

    original_children = [mock_paragraph1, mock_paragraph2]
    functional_requirement_mock.children = list(original_children)

    plain_file._process_single_acceptance_test_requirement(functional_requirement_mock)

    assert not hasattr(
        functional_requirement_mock, plain_spec.ACCEPTANCE_TESTS
    ), "acceptance_tests attribute should not be added if no ATs are found."
    assert list(functional_requirement_mock.children) == original_children, "FR children should remain unchanged."


def test_psart_empty_children_list_in_fr():
    """Tests behavior when the functional requirement itself has no children."""
    functional_requirement_mock = MagicMock(spec=ListItem)
    functional_requirement_mock.line_number = 1
    functional_requirement_mock.children = []

    plain_file._process_single_acceptance_test_requirement(functional_requirement_mock)

    assert not hasattr(functional_requirement_mock, plain_spec.ACCEPTANCE_TESTS)
    assert list(functional_requirement_mock.children) == []


def test_psart_heading_not_followed_by_list():
    """Tests behavior if AT heading is present but not followed by a list token."""
    functional_requirement_mock = MagicMock(spec=ListItem)
    functional_requirement_mock.line_number = 1

    mock_heading_raw_text = MagicMock(spec=RawText)
    mock_heading_raw_text.content = plain_spec.ACCEPTANCE_TEST_HEADING
    mock_heading_paragraph = MagicMock(spec=Paragraph)
    mock_heading_paragraph.children = [mock_heading_raw_text]
    mock_heading_paragraph.line_number = 2

    mock_not_a_list_token = MagicMock(spec=Paragraph)
    mock_not_a_list_token.children = []

    original_children = [mock_heading_paragraph, mock_not_a_list_token]
    functional_requirement_mock.children = list(original_children)

    plain_file._process_single_acceptance_test_requirement(functional_requirement_mock)

    assert not hasattr(
        functional_requirement_mock, plain_spec.ACCEPTANCE_TESTS
    ), "acceptance_tests attribute should not be added."
    assert len(functional_requirement_mock.children) == 2, "Children count should remain the same as original."
    assert (
        list(functional_requirement_mock.children) == original_children
    ), "Children should be unchanged as AT block was malformed."


def test_psart_with_valid_acceptance_tests():
    # Main item to test - OuterListItem
    functional_requirement_mock = MagicMock(spec=ListItem)
    functional_requirement_mock.line_number = 1

    # Create the three children using helper methods
    mock_fr_content = _create_mock_fr_content()
    mock_at_heading_paragraph = _create_mock_at_heading_paragraph()
    mock_at_list = _create_mock_at_list()

    # Set up children of the functional requirement
    functional_requirement_mock.children = [mock_fr_content, mock_at_heading_paragraph, mock_at_list]

    is_acceptance_test_heading, acceptance_test_heading_problem = plain_file._is_acceptance_test_heading(
        mock_at_heading_paragraph
    )
    assert is_acceptance_test_heading
    assert acceptance_test_heading_problem is None

    # Call the function under test
    plain_file._process_single_acceptance_test_requirement(functional_requirement_mock)

    # Verify acceptance_tests were extracted correctly
    assert hasattr(functional_requirement_mock, plain_spec.ACCEPTANCE_TESTS)
    assert len(functional_requirement_mock.acceptance_tests) == 1
    assert mock_at_list.children[0] in functional_requirement_mock.acceptance_tests

    # Verify only the FR content paragraph remains in children
    assert len(functional_requirement_mock.children) == 1
    assert mock_fr_content in functional_requirement_mock.children


def test_psart_with_invalid_acceptance_test_heading():
    functional_requirement_mock = MagicMock(spec=ListItem)
    functional_requirement_mock.line_number = 1

    # Create a heading paragraph with wrong content
    mock_at_heading_paragraph = _create_mock_at_heading_paragraph()

    # Change the content of the raw text to be invalid
    mock_at_heading_paragraph.children[0].children[0].children[0].content = "Acceptance test"  # missing colon

    # Set up children of the functional requirement with just the invalid heading
    functional_requirement_mock.children = [mock_at_heading_paragraph]

    # Verify that invalid heading is detected
    is_acceptance_test_heading, acceptance_test_heading_problem = plain_file._is_acceptance_test_heading(
        mock_at_heading_paragraph
    )
    assert not is_acceptance_test_heading
    assert acceptance_test_heading_problem is not None

    # Call the function under test and expect a PlainSyntaxError
    with pytest.raises(PlainSyntaxError):
        plain_file._process_single_acceptance_test_requirement(functional_requirement_mock)


def test_psart_with_duplicate_acceptance_test_heading():
    functional_requirement_mock = MagicMock(spec=ListItem)
    functional_requirement_mock.line_number = 1

    # Create content and list mocks
    mock_fr_content = _create_mock_fr_content()
    mock_at_list1 = _create_mock_at_list("First acceptance test")
    mock_at_list2 = _create_mock_at_list("Second acceptance test", line_number=40)

    # Create two heading paragraphs with different line numbers
    mock_at_heading_paragraph1 = _create_mock_at_heading_paragraph(line_number=34)
    mock_at_heading_paragraph2 = _create_mock_at_heading_paragraph(line_number=38)

    # Set up children of the functional requirement with duplicate AT headings
    functional_requirement_mock.children = [
        mock_fr_content,
        mock_at_heading_paragraph1,
        mock_at_list1,
        mock_at_heading_paragraph2,
        mock_at_list2,
    ]

    # Call the function under test and expect a PlainSyntaxError about duplicate headings
    expected_error_message = f"Syntax error at line {mock_at_heading_paragraph2.line_number}: Duplicate 'acceptance tests' heading found within the same functional requirement. Only one block of acceptance tests is allowed per functional requirement."

    with pytest.raises(PlainSyntaxError) as exc_info:
        plain_file._process_single_acceptance_test_requirement(functional_requirement_mock)

    assert str(exc_info.value) == expected_error_message


def test_invalid_plain_file_extension():
    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser("test.txt", [])
