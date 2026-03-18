import os
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest
from git import Repo

from git_utils import (
    BASE_FOLDER_COMMIT_MESSAGE,
    FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE,
    REFACTORED_CODE_COMMIT_MESSAGE,
    add_all_files_and_commit,
    diff,
    init_git_repo,
    revert_changes,
    revert_to_commit_with_frid,
)


@pytest.fixture
def temp_repo():
    """Create a temporary git repository for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        init_git_repo(temp_dir)

        # Create and commit initial file
        file_path = Path(temp_dir) / "test.txt"
        file_path.write_text("initial content\nline2\nline3\n")

        repo = Repo(temp_dir)
        repo.index.add(["test.txt"])
        add_all_files_and_commit(temp_dir, FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE.format("1.1"))

        yield temp_dir


@pytest.fixture
def empty_repo():
    """Create an empty git repository for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        init_git_repo(temp_dir)
        yield temp_dir


def test_empty_diff(temp_repo):
    """Test diff when there are no changes."""
    result = diff(temp_repo, "1.1")
    assert result == {}


def test_single_file_change(temp_repo):
    """Test diff with a single file change."""
    repo = Repo(temp_repo)

    # Modify the file
    file_path = Path(temp_repo) / "test.txt"
    file_path.write_text("modified content\nline2\nline3\n")
    repo.index.add(["test.txt"])
    repo.index.commit("Modified test.txt")

    # Get diff
    result = diff(temp_repo, "1.1")

    assert "test.txt" in result
    expected_diff = dedent("""
        --- a/test.txt
        +++ b/test.txt
        @@ -1,3 +1,3 @@
        -initial content
        +modified content
         line2
         line3
    """).strip()
    assert result["test.txt"] == expected_diff


def test_multiple_file_changes(temp_repo):
    """Test diff with multiple file changes."""
    repo = Repo(temp_repo)

    # Create and commit second file
    file2_path = Path(temp_repo) / "file2.txt"
    file2_path.write_text("file2 initial\nline2\n")

    add_all_files_and_commit(temp_repo, "Added file2.txt", None, "1.2")

    # Modify both files
    file1_path = Path(temp_repo) / "test.txt"
    file1_path.write_text("file1 modified\nline2\n")

    file2_path.write_text("file2 modified\nline2")

    # Get diff
    result = diff(temp_repo, "1.1")

    # Check first file
    assert "test.txt" in result
    expected_diff1 = dedent("""
        --- a/test.txt
        +++ b/test.txt
        @@ -1,3 +1,2 @@
        -initial content
        +file1 modified
         line2
        -line3
    """).strip()
    assert result["test.txt"] == expected_diff1

    # Check second file
    assert "file2.txt" in result
    expected_diff2 = dedent("""
        --- /dev/null
        +++ b/file2.txt
        @@ -0,0 +1,2 @@
        +file2 modified
        +line2
        \\ No newline at end of file
    """).strip()
    assert result["file2.txt"] == expected_diff2


def test_multiple_commits_diff(temp_repo):
    """Test diff with multiple commits."""
    repo = Repo(temp_repo)

    file1_path = Path(temp_repo) / "test.txt"

    # Create and commit second file
    file2_path = Path(temp_repo) / "file2.txt"
    file2_path.write_text("file2 frid1.1 refactored version\nline2\n")

    add_all_files_and_commit(temp_repo, REFACTORED_CODE_COMMIT_MESSAGE.format("1.1"), None, "1.1")
    add_all_files_and_commit(temp_repo, FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE.format("1.1"), None, "1.1")

    file1_path.write_text("file1 frid1.2 version\nline2\n")
    file2_path.write_text("file2 frid1.2 version\nline2\n")

    add_all_files_and_commit(temp_repo, "implemented frid 1.2", None, "1.2")

    file1_path.write_text("file1 frid1.2 refactored version\nline2\n")

    add_all_files_and_commit(temp_repo, REFACTORED_CODE_COMMIT_MESSAGE.format("1.2"), None, "1.2")
    add_all_files_and_commit(temp_repo, FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE.format("1.2"), None, "1.2")

    file3_path = Path(temp_repo) / "file3.txt"
    file3_path.write_text("file3 frid1.2 new file\nline2\n")

    # Get diff
    result = diff(temp_repo, "1.1")

    # Check first file
    assert "test.txt" in result
    expected_diff1 = dedent("""
        --- a/test.txt
        +++ b/test.txt
        @@ -1,3 +1,2 @@
        -initial content
        +file1 frid1.2 refactored version
         line2
        -line3
    """).strip()
    assert result["test.txt"] == expected_diff1

    # Check second file
    assert "file2.txt" in result
    expected_diff2 = dedent("""
        --- a/file2.txt
        +++ b/file2.txt
        @@ -1,2 +1,2 @@
        -file2 frid1.1 refactored version
        +file2 frid1.2 version
         line2
    """).strip()
    assert result["file2.txt"] == expected_diff2

    # Check third file
    assert "file3.txt" in result
    expected_diff3 = dedent("""
        --- /dev/null
        +++ b/file3.txt
        @@ -0,0 +1,2 @@
        +file3 frid1.2 new file
        +line2
    """).strip()
    assert result["file3.txt"] == expected_diff3


def test_diff_without_previous_frid_and_no_base_folder(empty_repo):
    """Test diff without previous frid and no base folder."""
    # Create a new file without committing
    file_path = Path(empty_repo) / "new.txt"
    file_path.write_text("new file content\nline2\n")
    add_all_files_and_commit(empty_repo, "First commit")

    # create one more file
    file_path = Path(empty_repo) / "new2.txt"
    file_path.write_text("new file content\nline2\n")

    # Get diff
    result = diff(empty_repo)

    assert "new.txt" in result
    expected_diff = dedent("""
        --- /dev/null
        +++ b/new.txt
        @@ -0,0 +1,2 @@
        +new file content
        +line2
    """).strip()
    assert result["new.txt"] == expected_diff

    assert "new2.txt" in result
    expected_diff2 = dedent("""
        --- /dev/null
        +++ b/new2.txt
        @@ -0,0 +1,2 @@
        +new file content
        +line2
    """).strip()
    assert result["new2.txt"] == expected_diff2


def test_diff_without_previous_frid_and_base_folder(temp_repo):
    """Test diff without previous frid and base folder."""
    # Create a commit for the base folder
    file_path = Path(temp_repo) / "new.txt"
    file_path.write_text("base folder content\nline2\n")
    add_all_files_and_commit(temp_repo, BASE_FOLDER_COMMIT_MESSAGE)

    # update the file
    file_path.write_text("updated base folder content\nline2\n")

    # Get diff
    result = diff(temp_repo)

    assert "new.txt" in result
    expected_diff = dedent("""
        --- a/new.txt
        +++ b/new.txt
        @@ -1,2 +1,2 @@
        -base folder content
        +updated base folder content
         line2
    """).strip()
    assert result["new.txt"] == expected_diff


def test_new_file(temp_repo):
    """Test diff with a new untracked file."""
    repo = Repo(temp_repo)

    # Create a new file without committing
    file_path = Path(temp_repo) / "new.txt"
    file_path.write_text("new file content\nline2\n")

    # Get diff
    result = diff(temp_repo, "1.1")

    assert "new.txt" in result
    expected_diff = dedent("""
        --- /dev/null
        +++ b/new.txt
        @@ -0,0 +1,2 @@
        +new file content
        +line2
    """).strip()
    assert result["new.txt"] == expected_diff


def test_deleted_file(temp_repo):
    """Test diff with a deleted file."""
    repo = Repo(temp_repo)

    # Delete the file
    file_path = Path(temp_repo) / "test.txt"
    file_path.unlink()

    # Get diff
    result = diff(temp_repo, "1.1")

    assert "test.txt" in result
    expected_diff = dedent("""
        --- a/test.txt
        +++ /dev/null
        @@ -1,3 +0,0 @@
        -initial content
        -line2
        -line3
    """).strip()
    assert result["test.txt"] == expected_diff


def test_init_clean_repo(empty_repo):
    """Test initializing a clean git repository."""
    repo = Repo(empty_repo)

    # Verify it's a git repository
    assert repo.git_dir is not None
    assert repo.git_dir.endswith(".git")

    # Verify there is only one commit
    assert len(list(repo.iter_commits())) == 1
    assert "[Codeplain] Initial module commit" in list(repo.iter_commits())[0].message


def test_add_all_files_and_commit(temp_repo):
    """Test adding all files and committing them."""
    # Create some test files
    file1_path = Path(temp_repo) / "file1.txt"
    file1_path.write_text("content1")
    file2_path = Path(temp_repo) / "file2.txt"
    file2_path.write_text("content2")

    # Add and commit files
    repo = add_all_files_and_commit(temp_repo, "Test commit", None, "FR123", "render-id")

    # Verify commit was created
    commits = list(repo.iter_commits())
    assert len(commits) == 3  # initial commit + first FRID commit + test commit

    # Verify commit message
    commit = commits[0]
    assert "Test commit" in commit.message
    assert "Changes related to functionality ID (FRID): FR123" in commit.message
    assert "Render ID: render-id" in commit.message

    # Verify files were committed
    tree = commit.tree
    assert "file1.txt" in tree
    assert "file2.txt" in tree

    file2_path.write_text("content2 modified")
    repo = add_all_files_and_commit(temp_repo, "Commit changes on existing file", None, "FR4")
    commits = list(repo.iter_commits())
    assert len(commits) == 4
    assert "Changes related to functionality ID (FRID): FR4" in commits[0].message

    repo = add_all_files_and_commit(temp_repo, "Empty commit", None, "FR5")
    commits = list(repo.iter_commits())
    assert len(commits) == 5
    assert "Changes related to functionality ID (FRID): FR5" in commits[0].message


def test_revert_changes(temp_repo):
    """Test reverting changes in the repository."""
    # Create and commit initial file
    file_path = Path(temp_repo) / "test.txt"
    file_path.write_text("initial content")
    repo = add_all_files_and_commit(temp_repo, "Initial commit", None, "FR123")

    # Modify the file
    file_path.write_text("modified content")

    # Verify the file was modified
    assert file_path.read_text() == "modified content"

    # Revert changes
    repo = revert_changes(temp_repo)

    # Verify the file was reverted
    assert file_path.read_text() == "initial content"

    # Verify working directory is clean
    assert not repo.is_dirty()


def test_revert_to_commit_with_frid(temp_repo):
    """Test reverting to a specific commit with FRID."""
    # Create and commit first version
    file_path = Path(temp_repo) / "test.txt"
    file_path.write_text("version 1")
    repo = add_all_files_and_commit(
        temp_repo, FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE.format("FR123"), None, "FR123"
    )

    # Create and commit second version
    file_path.write_text("version 2")
    repo = add_all_files_and_commit(
        temp_repo, FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE.format("FR456"), None, "FR456"
    )

    # Create and commit third version
    file_path.write_text("version 3")
    repo = add_all_files_and_commit(
        temp_repo, FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE.format("FR789"), None, "FR789"
    )

    # Revert to FR123
    repo = revert_to_commit_with_frid(temp_repo, "FR123")

    # Verify we're back at version 1
    assert file_path.read_text() == "version 1"

    # Verify working directory is clean
    assert not repo.is_dirty()

    # Test error case - non-existent FRID
    with pytest.raises(Exception) as exc_info:
        revert_to_commit_with_frid(temp_repo, "NONEXISTENT")
    assert "No commit with frid NONEXISTENT found" in str(exc_info.value)


def test_revert_to_commit_with_frid_and_base_folder(temp_repo):
    """Test reverting to base folder."""
    # Create a commit for the base folder
    file_path = Path(temp_repo) / "new.txt"
    file_path.write_text("base folder content\nline1\n")
    add_all_files_and_commit(temp_repo, BASE_FOLDER_COMMIT_MESSAGE)

    # create another commit
    file_path.write_text("changed file content\nline2\n")
    add_all_files_and_commit(temp_repo, "Another commit")

    # revert to base folder
    repo = revert_to_commit_with_frid(temp_repo, None)

    # verify the file was reverted
    assert file_path.read_text() == "base folder content\nline1\n"


def test_revert_to_base_folder_no_commit(temp_repo):
    """Test reverting to base folder."""
    # Create a commit for the base folder
    file_path = Path(temp_repo) / "new.txt"
    file_path.write_text("some content\n")
    add_all_files_and_commit(temp_repo, "FRID", 123)

    # revert initial commit
    repo = revert_to_commit_with_frid(temp_repo, None)

    # verify the repo doesn't have any commits and that the file doesn't exist
    assert not repo.is_dirty()
    assert repo.active_branch.name == "main"
    assert len(list(repo.iter_commits())) == 1  # initial commit
    assert not file_path.exists()
