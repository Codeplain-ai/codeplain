"""Which scenario each conformance test implements, checked against the files (as codeplain-api's traceability
did for the one-shot test renders). The conformance tests writer reports its tests with submit_conformance_tests;
what does not add up goes back to it, so it corrects it in the same session."""

import os


def _test_exists(test: dict, files_content: dict[str, str], tests_folder: str) -> bool:
    """Whether the test's name appears in the file the writer says holds it (or, when that path names no file,
    in any file of the folder: a mismatch in the path alone does not make a test missing)."""
    name = str(test.get("test_name") or "")
    if not name:
        return False
    file_name = str(test.get("file_name") or "")
    if os.path.isabs(file_name):
        file_name = os.path.relpath(file_name, tests_folder)
    content = files_content.get(os.path.normpath(file_name)) if file_name else None
    if content is None:
        return any(name in file_content for file_content in files_content.values() if file_content)
    return name in content


def check_traceability(scenario_ids: list[str], tests: list[dict], files_content: dict[str, str], tests_folder: str):
    """`implemented` (scenario ID to its tests), `missing` (scenarios without a test), `extra` (tests of no
    scenario), `duplicated` (scenarios with more than one test) and `not_found` (reported tests no file holds)."""
    implemented: dict[str, list[str]] = {}
    extra: list[str] = []
    not_found: list[str] = []
    for test in tests or []:
        if not isinstance(test, dict):
            continue
        if not _test_exists(test, files_content, tests_folder):
            not_found.append(f"{test.get('test_name')} ({test.get('file_name')})")
            continue
        scenario_id = test.get("scenario_id")
        if scenario_id in scenario_ids:
            implemented.setdefault(scenario_id, []).append(str(test["test_name"]))
        else:
            extra.append(str(test["test_name"]))
    return {
        "implemented": implemented,
        "missing": [scenario_id for scenario_id in scenario_ids if scenario_id not in implemented],
        "extra": extra,
        "duplicated": [scenario_id for scenario_id, names in implemented.items() if len(names) > 1],
        "not_found": not_found,
    }


def describe_problems(traceability: dict) -> str:
    """What the writer has to correct; empty when the tests trace to the scenarios. Tests of no scenario are
    allowed when they are helpers the framework collects, so they are mentioned, not demanded away."""
    problems = []
    if traceability["not_found"]:
        problems.append(
            "These reported tests are in none of the files of the conformance tests folder (check the name and the "
            "file): " + ", ".join(traceability["not_found"])
        )
    if traceability["missing"]:
        problems.append("These scenarios have no test: " + ", ".join(traceability["missing"]))
    if traceability["duplicated"]:
        problems.append(
            "These scenarios have more than one test; keep exactly one per scenario: "
            + ", ".join(traceability["duplicated"])
        )
    if not problems:
        return ""
    if traceability["extra"]:
        problems.append(
            "These tests implement no scenario; remove them unless they are helpers the test framework collects: "
            + ", ".join(traceability["extra"])
        )
    return "\n".join(f"- {problem}" for problem in problems)
