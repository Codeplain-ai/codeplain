"""Tests for the context preload builders (preload.py)."""

import os

import preload


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class TestBuildMemoryPreload:
    def test_returns_contents_and_all_names(self, tmp_path):
        memory_folder = str(tmp_path / ".memory")
        _write(os.path.join(memory_folder, "agent_memory", "note-a.md"), "learning A")
        _write(os.path.join(memory_folder, "agent_memory", "note-b.md"), "learning B")

        contents, names = preload.build_memory_preload(memory_folder)

        assert contents == {
            os.path.join("agent_memory", "note-a.md"): "learning A",
            os.path.join("agent_memory", "note-b.md"): "learning B",
        }
        assert sorted(names) == sorted(contents.keys())

    def test_oversized_note_stays_index_only(self, tmp_path):
        memory_folder = str(tmp_path / ".memory")
        _write(os.path.join(memory_folder, "agent_memory", "small.md"), "small")
        _write(os.path.join(memory_folder, "agent_memory", "huge.md"), "x" * (preload.MEMORY_NOTE_MAX_CHARS + 1))

        contents, names = preload.build_memory_preload(memory_folder)

        assert list(contents.keys()) == [os.path.join("agent_memory", "small.md")]
        assert os.path.join("agent_memory", "huge.md") in names

    def test_missing_folder_is_empty(self, tmp_path):
        contents, names = preload.build_memory_preload(str(tmp_path / "nope"))
        assert contents == {}
        assert names == []


class TestBuildLinkedResourcesPreload:
    def test_small_resources_inlined_large_left_as_paths(self):
        resources = {
            "resources/small.json": '{"a": 1}',
            "resources/huge.yaml": "y" * (preload.LINKED_RESOURCE_MAX_CHARS + 1),
        }
        inline, leftover = preload.build_linked_resources_preload(resources)
        assert inline == {"resources/small.json": '{"a": 1}'}
        assert leftover == ["resources/huge.yaml"]

    def test_total_budget_enforced_smallest_first(self):
        # Five files under the per-file cap whose combined size exceeds the total
        # budget: the smallest ones are inlined, the one that no longer fits is not.
        chunk = preload.LINKED_RESOURCE_MAX_CHARS - 1
        count = preload.LINKED_RESOURCES_TOTAL_MAX_CHARS // chunk + 2
        resources = {f"{i}.json": "x" * chunk for i in range(count)}
        resources["tiny.json"] = "t" * 10

        inline, leftover = preload.build_linked_resources_preload(resources)

        assert "tiny.json" in inline
        assert leftover  # at least one file no longer fit the total budget
        inlined_chars = sum(len(v) for v in inline.values())
        assert inlined_chars <= preload.LINKED_RESOURCES_TOTAL_MAX_CHARS

    def test_empty_input(self):
        assert preload.build_linked_resources_preload(None) == ({}, [])
        assert preload.build_linked_resources_preload({}) == ({}, [])


class TestBuildSourceFilesPreload:
    def test_own_files_and_relevant_inherited_files_included(self, tmp_path):
        build_folder = str(tmp_path / "build")
        # The build folder is cumulative: it also contains inherited required-module code.
        _write(os.path.join(build_folder, "src", "OwnService.java"), "class OwnService {}")
        _write(os.path.join(build_folder, "src", "HttpClient.java"), "class HttpClient {}")  # inherited, in spec
        _write(os.path.join(build_folder, "src", "InheritedNoise.java"), "class InheritedNoise {}")

        contents = preload.build_source_files_preload(
            build_folder,
            relevance_text="Implement :OwnService: using the :HttpClient:",
            module_files={os.path.join("src", "OwnService.java")},
        )

        assert os.path.join("src", "OwnService.java") in contents
        assert os.path.join("src", "HttpClient.java") in contents  # inherited but spec-relevant
        assert os.path.join("src", "InheritedNoise.java") not in contents  # inherited and irrelevant

    def test_own_files_included_even_when_not_in_spec_text(self, tmp_path):
        build_folder = str(tmp_path / "build")
        _write(os.path.join(build_folder, "src", "helper_util.java"), "class HelperUtil {}")

        contents = preload.build_source_files_preload(
            build_folder,
            relevance_text="nothing matching",
            module_files={os.path.join("src", "helper_util.java")},
        )

        assert os.path.join("src", "helper_util.java") in contents

    def test_without_module_files_only_relevant_files_included(self, tmp_path):
        build_folder = str(tmp_path / "build")
        _write(os.path.join(build_folder, "src", "BatchParser.java"), "class BatchParser {}")
        _write(os.path.join(build_folder, "src", "Unrelated.java"), "class Unrelated {}")

        contents = preload.build_source_files_preload(
            build_folder, relevance_text="Implement the BatchParser", module_files=None
        )

        assert os.path.join("src", "BatchParser.java") in contents
        assert os.path.join("src", "Unrelated.java") not in contents

    def test_oversized_file_skipped(self, tmp_path):
        build_folder = str(tmp_path / "build")
        _write(os.path.join(build_folder, "big.py"), "#" * (preload.SOURCE_FILE_MAX_CHARS + 1))
        _write(os.path.join(build_folder, "ok.py"), "print('hi')")

        contents = preload.build_source_files_preload(build_folder, module_files={"big.py", "ok.py"})

        assert "ok.py" in contents
        assert "big.py" not in contents

    def test_own_files_win_over_inherited_when_budget_is_tight(self, tmp_path, monkeypatch):
        build_folder = str(tmp_path / "build")
        _write(os.path.join(build_folder, "own.java"), "o" * 100)
        _write(os.path.join(build_folder, "spec_match.java"), "i" * 100)  # inherited, relevance-matched

        monkeypatch.setattr(preload, "SOURCE_FILES_TOTAL_MAX_CHARS", 100)
        contents = preload.build_source_files_preload(
            build_folder, relevance_text="uses spec_match", module_files={"own.java"}
        )

        assert list(contents.keys()) == ["own.java"]

    def test_missing_folder_is_empty(self, tmp_path):
        assert preload.build_source_files_preload(str(tmp_path / "nope")) == {}


class TestModuleChangedFiles:
    def test_returns_none_when_not_a_git_repo(self, tmp_path):
        assert preload.module_changed_files(str(tmp_path)) is None


class TestBuildConformanceTestExamplePreload:
    def test_picks_suite_and_selects_manifest_and_tests(self, tmp_path):
        tests_folder = str(tmp_path / "modules" / "mod_a" / "tests")
        suite = os.path.join(tests_folder, "feature_one")
        _write(os.path.join(suite, "pom.xml"), "<project/>")
        _write(os.path.join(suite, "src", "FeatureTest.java"), "class FeatureTest {}")
        _write(os.path.join(suite, "src", "helper.java"), "class Helper {}")

        example = preload.build_conformance_test_example_preload([tests_folder])

        joined = "|".join(example.keys())
        assert "pom.xml" in joined
        assert "FeatureTest.java" in joined
        assert "helper.java" not in joined  # neither a manifest nor a test file

    def test_excludes_current_suite(self, tmp_path):
        tests_folder = str(tmp_path / "modules" / "mod_a" / "tests")
        only_suite = os.path.join(tests_folder, "current")
        _write(os.path.join(only_suite, "pom.xml"), "<project/>")

        example = preload.build_conformance_test_example_preload([tests_folder], exclude_folder=only_suite)

        assert example == {}

    def test_no_suites_is_empty(self, tmp_path):
        assert preload.build_conformance_test_example_preload([str(tmp_path)]) == {}
        assert preload.build_conformance_test_example_preload([]) == {}


class TestBuildEnvironmentBrief:
    def test_no_manifests_no_brief(self, tmp_path):
        build_folder = str(tmp_path / "build")
        _write(os.path.join(build_folder, "readme.txt"), "nothing to detect")
        assert preload.build_environment_brief(build_folder) == ""

    def test_python_ecosystem_probed(self, tmp_path):
        build_folder = str(tmp_path / "build")
        _write(os.path.join(build_folder, "requirements.txt"), "requests\n")

        brief = preload.build_environment_brief(build_folder)

        # python3 exists wherever these tests run, so the probe line must be present.
        assert "python" in brief
        assert len(brief) <= preload.ENVIRONMENT_BRIEF_MAX_CHARS
