from types import SimpleNamespace

from render_machine.actions.render_conformance_tests import RenderConformanceTests


class FakeRunningContext:
    def __init__(self, tests_json):
        self.tests_json = tests_json

    def conformance_tests_json_has_module_populated(self, module_name):
        return bool(self.tests_json)

    def get_conformance_tests_json(self, module_name):
        return self.tests_json


def make_render_context(tmp_path, current_json, required_jsons):
    return SimpleNamespace(
        module_name="messages",
        conformance_tests_running_context=FakeRunningContext(current_json),
        plain_module=SimpleNamespace(all_required_modules=[SimpleNamespace(module_name=m) for m in required_jsons]),
        conformance_tests=SimpleNamespace(get_conformance_tests_json=lambda module: required_jsons[module]),
    )


def folder(tmp_path, name, files):
    path = tmp_path / name
    path.mkdir(parents=True)
    for file_name, content in files.items():
        (path / file_name).write_text(content)
    return str(path)


def test_reference_is_the_latest_earlier_functionality_of_the_current_module(tmp_path):
    first = folder(tmp_path, "messages/publish", {"pom.xml": "first"})
    second = folder(tmp_path, "messages/fetch", {"pom.xml": "second"})
    target = str(tmp_path / "messages/list")
    current = {"publish": {"folder_name": first}, "fetch": {"folder_name": second}, "list": {"folder_name": target}}
    context = make_render_context(tmp_path, current, {"accounts": {}})

    reference = RenderConformanceTests._reference_conformance_tests(context, target)

    assert reference == {f"{second}/pom.xml": "second"}


def test_first_functionality_of_a_module_uses_the_closest_required_module(tmp_path):
    healthcheck = folder(tmp_path, "healthcheck/health", {"pom.xml": "health"})
    accounts = folder(tmp_path, "accounts/delete", {"pom.xml": "accounts"})
    target = str(tmp_path / "messages/publish")
    required = {
        "healthcheck": {"health": {"folder_name": healthcheck}},
        "accounts": {"delete": {"folder_name": accounts}},
    }
    context = make_render_context(tmp_path, {"publish": {"folder_name": target}}, required)

    assert RenderConformanceTests._reference_conformance_tests(context, target) == {f"{accounts}/pom.xml": "accounts"}


def test_no_earlier_tests_give_no_reference(tmp_path):
    target = str(tmp_path / "healthcheck/health")
    context = make_render_context(tmp_path, {"health": {"folder_name": target}}, {})
    assert RenderConformanceTests._reference_conformance_tests(context, target) == {}


def test_tests_folder_is_named_after_the_functionality_id():
    from render_machine.actions.render_conformance_tests import tests_folder_name

    assert tests_folder_name("register-account", []) == "register_account"
    assert tests_folder_name("Fetch Conversation.Messages", ["other"]) == "fetch_conversation_messages"


def test_a_taken_tests_folder_name_gets_a_suffix():
    from render_machine.actions.render_conformance_tests import tests_folder_name

    assert tests_folder_name("login", ["login", "login_2"]) == "login_3"


def test_an_id_without_letters_is_left_to_the_server():
    from render_machine.actions.render_conformance_tests import tests_folder_name

    assert tests_folder_name("1.2.3", []) is None
