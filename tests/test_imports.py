import pytest

import plain_file


def test_non_existent_import(load_test_data, get_test_data_path):
    plain_source_text = load_test_data("data/imports/non_existent_import.plain")
    with pytest.raises(Exception, match="Module does not exist"):
        plain_file.parse_plain_source(plain_source_text, {}, [], [], [])


def test_circular_imports(load_test_data, get_test_data_path):
    plain_source_text = load_test_data("data/imports/circular_imports_main.plain")
    template_dirs = [get_test_data_path("data/imports")]
    with pytest.raises(Exception, match="Circular import detected"):
        plain_file.parse_plain_source(plain_source_text, {}, template_dirs, [], [])


def test_diamond_imports(load_test_data, get_test_data_path):
    plain_source_text = load_test_data("data/imports/diamond_imports_main.plain")
    template_dirs = [get_test_data_path("data/imports")]
    plain_file_parse_result = plain_file.parse_plain_source(plain_source_text, {}, template_dirs, [], [])

    assert plain_file.marshall_plain_source(plain_file_parse_result.plain_source) == {
        "definitions": [
            {"markdown": "- :CommonImportDef: is a definition in diamond_import_common."},
            {"markdown": "- :Import1Def: is a definition in diamond_import_1."},
            {"markdown": "- :Import2Def: is a definition in diamond_import_2."},
        ],
        "implementation reqs": [
            {"markdown": "- :CommonImportDef: is used in diamond_import_common."},
            {"markdown": "- :Import1Def: is used in diamond_import_1."},
            {"markdown": "- :Import2Def: is used in diamond_import_2."},
            {"markdown": '- :MainExecutableFile: of :App: should be called "hello_world.py".'},
        ],
        "test reqs": [
            {"markdown": "- :CommonImportDef: is tested in diamond_import_common."},
            {"markdown": "- :Import1Def: is tested in diamond_import_1."},
            {"markdown": "- :Import2Def: is tested in diamond_import_2."},
        ],
        "functional specs": [{"markdown": '- Display "hello, world"'}],
    }
