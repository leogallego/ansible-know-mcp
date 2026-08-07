"""Tests for ansible_know.parser."""

import json
from unittest.mock import ANY, MagicMock, patch

import pytest

from ansible_know.errors import AnsibleDocError, CollectionNotFoundError, ValidationError
from ansible_know.parser import (
    _ANSIBLE_DOC_BATCH_SIZE,
    extract_examples,
    extract_module_metadata,
    extract_params,
    extract_plugin_metadata,
    extract_role_metadata,
    extract_short_description,
    get_module_doc,
    get_module_docs,
    get_plugin_doc,
    get_plugin_docs,
    get_role_doc,
    is_api_module,
    list_modules,
    list_plugins,
    list_roles,
    load_module_metadata_batch,
    load_plugin_metadata_batch,
    search_modules,
    search_plugins,
    transform_galaxy_to_ansible_doc_format,
)


class TestGetModuleDoc:
    def test_returns_parsed_json(self, sample_module_doc, sample_module_doc_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_module_doc_json):
            result = get_module_doc("ansible.builtin.package")
        assert result == sample_module_doc

    def test_raises_on_invalid_json(self):
        with patch("ansible_know.parser._run_ansible_doc", return_value="not json"):
            with pytest.raises(AnsibleDocError, match="Failed to parse"):
                get_module_doc("ansible.builtin.package")


class TestGetModuleDocs:
    def test_batches_names_in_one_call(self, sample_module_doc_json):
        names = ["ansible.builtin.package", "ansible.builtin.apt"]
        with patch(
            "ansible_know.parser._run_ansible_doc", return_value=sample_module_doc_json,
        ) as mock:
            get_module_docs(names)
        mock.assert_called_once_with(
            "ansible.builtin.package", "ansible.builtin.apt", "--json",
            collections_path=None, timeout=ANY,
        )

    def test_empty_names_skips_subprocess(self):
        with patch("ansible_know.parser._run_ansible_doc") as mock:
            assert get_module_docs([]) == {}
        mock.assert_not_called()

    def test_chunks_large_name_lists(self, sample_module_doc_json):
        names = [f"ns.col.mod{i}" for i in range(_ANSIBLE_DOC_BATCH_SIZE + 3)]
        with patch(
            "ansible_know.parser._run_ansible_doc", return_value=sample_module_doc_json,
        ) as mock:
            get_module_docs(names)
        assert mock.call_count == 2
        # args are (*chunk, "--json"); collections_path is a kwarg
        first_args = mock.call_args_list[0].args
        second_args = mock.call_args_list[1].args
        assert first_args[-1] == "--json"
        assert len(first_args) - 1 == _ANSIBLE_DOC_BATCH_SIZE
        assert second_args[-1] == "--json"
        assert len(second_args) - 1 == 3

    def test_deduplicates_names(self, sample_module_doc_json):
        with patch(
            "ansible_know.parser._run_ansible_doc", return_value=sample_module_doc_json,
        ) as mock:
            get_module_docs(["a.b.c", "a.b.c", "a.b.d"])
        mock.assert_called_once_with(
            "a.b.c", "a.b.d", "--json", collections_path=None, timeout=ANY,
        )

    def test_chunk_failure_falls_back_per_module(self, sample_module_doc):
        names = ["ns.col.a", "ns.col.b"]
        batch_error = AnsibleDocError("batch boom")
        per_name = {
            "ns.col.a": json.dumps({"ns.col.a": sample_module_doc["ansible.builtin.package"]}),
            "ns.col.b": json.dumps({"ns.col.b": sample_module_doc["ansible.builtin.package"]}),
        }

        def side_effect(*args, **kwargs):
            # Multi-name batch: (...names, "--json")
            name_args = [a for a in args if a != "--json"]
            if len(name_args) > 1:
                raise batch_error
            return per_name[name_args[0]]

        with patch("ansible_know.parser._run_ansible_doc", side_effect=side_effect) as mock:
            result = get_module_docs(names)
        assert set(result) == set(names)
        assert mock.call_count == 3  # 1 failed batch + 2 singles


class TestLoadModuleMetadataBatch:
    def test_extracts_metadata_for_present_modules(self, sample_module_doc):
        docs = {
            "ansible.builtin.package": sample_module_doc["ansible.builtin.package"],
            "ansible.builtin.apt": sample_module_doc["ansible.builtin.package"],
        }
        with patch(
            "ansible_know.parser.get_module_docs", return_value=docs,
        ) as mock_docs:
            result = load_module_metadata_batch(
                ["ansible.builtin.package", "ansible.builtin.apt", "missing.mod"],
            )
        mock_docs.assert_called_once()
        assert set(result) == {"ansible.builtin.package", "ansible.builtin.apt"}
        assert result["ansible.builtin.package"]["module_name"] == "ansible.builtin.package"


class TestListModules:
    def test_returns_all_modules(self, sample_module_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_module_list_json) as mock:
            result = list_modules()
        assert len(result) == 4
        mock.assert_called_once_with("--list", "--json", collections_path=None)

    def test_passes_collection_filter(self, sample_module_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_module_list_json) as mock:
            list_modules(collection_filter="community.general")
        mock.assert_called_once_with("--list", "--json", "community.general", collections_path=None)


class TestSearchModules:
    def test_filters_by_keyword_in_name(self, sample_module_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_module_list_json):
            result = search_modules("redis")
        assert "community.general.redis" in result
        assert len(result) == 1

    def test_filters_by_keyword_in_description(self, sample_module_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_module_list_json):
            result = search_modules("apt")
        assert "ansible.builtin.apt" in result
        assert "ansible.builtin.package" not in result

    def test_case_insensitive(self, sample_module_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_module_list_json):
            result = search_modules("REDIS")
        assert "community.general.redis" in result


class TestExtractParams:
    def test_extracts_all_params(self, sample_module_doc):
        params = extract_params(sample_module_doc)
        names = [p["name"] for p in params]
        assert "name" in names
        assert "state" in names
        assert "use" in names

    def test_required_params_first(self, sample_module_doc):
        params = extract_params(sample_module_doc)
        required_flags = [p["required"] for p in params]
        assert required_flags == [True, True, False]

    def test_param_fields(self, sample_module_doc):
        params = extract_params(sample_module_doc)
        state_param = next(p for p in params if p["name"] == "state")
        assert state_param["type"] == "str"
        assert state_param["required"] is True
        assert state_param["choices"] == ["present", "absent", "latest"]

    def test_default_values(self, sample_module_doc):
        params = extract_params(sample_module_doc)
        use_param = next(p for p in params if p["name"] == "use")
        assert use_param["default"] == "auto"
        assert use_param["required"] is False


class TestExtractExamples:
    def test_returns_example_yaml(self, sample_module_doc):
        examples = extract_examples(sample_module_doc)
        assert "Install ntpdate" in examples
        assert "state: present" in examples


class TestExtractShortDescription:
    def test_returns_description(self, sample_module_doc):
        desc = extract_short_description(sample_module_doc)
        assert desc == "Generic OS package manager"


class TestIsApiModule:
    def test_system_module_returns_false(self, sample_module_doc):
        assert is_api_module(sample_module_doc) is False

    def test_api_module_returns_true(self, sample_api_module_doc):
        assert is_api_module(sample_api_module_doc) is True


class TestExtractModuleMetadata:
    def test_returns_combined_metadata(self, sample_module_doc):
        meta = extract_module_metadata(sample_module_doc)
        assert meta["module_name"] == "ansible.builtin.package"
        assert meta["short_description"] == "Generic OS package manager"
        assert len(meta["params"]) == 3
        assert "ntpdate" in meta["examples"]
        assert meta["is_api_module"] is False

    def test_api_module_metadata(self, sample_api_module_doc):
        meta = extract_module_metadata(sample_api_module_doc)
        assert meta["is_api_module"] is True


class TestRunAnsibleDocEnvInjection:
    def test_injects_collections_path(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("subprocess.run", return_value=MagicMock(
                returncode=0, stdout='{}', stderr='',
            )) as mock_run:
                from ansible_know.parser import _run_ansible_doc
                _run_ansible_doc("--list", "--json", collections_path="/tmp/ansible_know_abc123")
                env = mock_run.call_args[1]["env"]
                assert "/tmp/ansible_know_abc123" in env["ANSIBLE_COLLECTIONS_PATH"]

    def test_prepends_to_existing_collections_path(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch.dict("os.environ", {"ANSIBLE_COLLECTIONS_PATH": "/existing/path"}):
                with patch("subprocess.run", return_value=MagicMock(
                    returncode=0, stdout='{}', stderr='',
                )) as mock_run:
                    from ansible_know.parser import _run_ansible_doc
                    _run_ansible_doc("--list", "--json", collections_path="/tmp/ansible_know_abc123")
                    env = mock_run.call_args[1]["env"]
                    assert env["ANSIBLE_COLLECTIONS_PATH"].startswith("/tmp/ansible_know_abc123")
                    assert "/existing/path" in env["ANSIBLE_COLLECTIONS_PATH"]

    def test_no_collections_path_still_sets_ansible_local_tmp(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("subprocess.run", return_value=MagicMock(
                returncode=0, stdout='{}', stderr='',
            )) as mock_run:
                from ansible_know.parser import _run_ansible_doc
                _run_ansible_doc("--list", "--json")
                call_kwargs = mock_run.call_args[1]
                env = call_kwargs.get("env")
                assert env is not None
                assert "ANSIBLE_LOCAL_TMP" in env
                assert "ANSIBLE_COLLECTIONS_PATH" not in env


class TestCollectionNotFoundDetection:
    def test_raises_collection_not_found_on_has_no_attribute(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("subprocess.run", return_value=MagicMock(
                returncode=1, stdout='', stderr='netbox.netbox has no attribute',
            )):
                from ansible_know.parser import _run_ansible_doc
                with pytest.raises(CollectionNotFoundError, match="has no attribute"):
                    _run_ansible_doc("netbox.netbox.netbox_device", "--json")

    def test_raises_collection_not_found_on_was_not_found(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("subprocess.run", return_value=MagicMock(
                returncode=1, stdout='', stderr='netbox.netbox was not found',
            )):
                from ansible_know.parser import _run_ansible_doc
                with pytest.raises(CollectionNotFoundError, match="was not found"):
                    _run_ansible_doc("netbox.netbox.netbox_device", "--json")

    def test_raises_collection_not_found_on_could_not_be_found(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("subprocess.run", return_value=MagicMock(
                returncode=1, stdout='', stderr='module could not be found',
            )):
                from ansible_know.parser import _run_ansible_doc
                with pytest.raises(CollectionNotFoundError, match="could not be found"):
                    _run_ansible_doc("netbox.netbox.netbox_device", "--json")

    def test_raises_ansible_doc_error_on_other_errors(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("subprocess.run", return_value=MagicMock(
                returncode=1, stdout='', stderr='ansible-doc timed out',
            )):
                from ansible_know.parser import _run_ansible_doc
                with pytest.raises(AnsibleDocError, match="timed out"):
                    _run_ansible_doc("ansible.builtin.copy", "--json")

    def test_raises_collection_not_found_on_exit_0_with_empty_json(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("subprocess.run", return_value=MagicMock(
                returncode=0, stdout='{}', stderr='[WARNING]: kubernetes.core.k8s was not found',
            )):
                from ansible_know.parser import _run_ansible_doc
                with pytest.raises(CollectionNotFoundError, match="was not found"):
                    _run_ansible_doc("kubernetes.core.k8s", "--json")

    def test_raises_collection_not_found_on_exit_0_with_empty_stdout(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("subprocess.run", return_value=MagicMock(
                returncode=0, stdout='', stderr='[WARNING]: some.col.mod was not found',
            )):
                from ansible_know.parser import _run_ansible_doc
                with pytest.raises(CollectionNotFoundError, match="was not found"):
                    _run_ansible_doc("some.col.mod", "--json")

    def test_returns_normally_on_exit_0_with_valid_json(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("subprocess.run", return_value=MagicMock(
                returncode=0, stdout='{"ansible.builtin.copy": {}}', stderr='',
            )):
                from ansible_know.parser import _run_ansible_doc
                result = _run_ansible_doc("ansible.builtin.copy", "--json")
                assert "ansible.builtin.copy" in result

    def test_collection_not_found_is_subclass_of_ansible_doc_error(self):
        assert issubclass(CollectionNotFoundError, AnsibleDocError)


class TestListRoles:
    def test_returns_role_dict(self, sample_role_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_role_list_json) as mock:
            result = list_roles()
        assert "fedora.linux_system_roles.timesync" in result
        assert "fedora.linux_system_roles.gfs2" in result
        mock.assert_called_once_with("--list", "-t", "role", "--json", collections_path=None)

    def test_passes_collection_filter(self, sample_role_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_role_list_json) as mock:
            list_roles(collection_filter="fedora.linux_system_roles")
        mock.assert_called_once_with(
            "--list", "-t", "role", "--json", "fedora.linux_system_roles",
            collections_path=None,
        )


class TestGetRoleDoc:
    def test_returns_parsed_json(self, sample_role_doc, sample_role_doc_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_role_doc_json):
            result = get_role_doc("fedora.linux_system_roles.gfs2")
        assert result == sample_role_doc

    def test_returns_empty_dict_for_undocumented_role(self):
        with patch("ansible_know.parser._run_ansible_doc", return_value="{}"):
            result = get_role_doc("fedora.linux_system_roles.timesync")
        assert result == {}

    def test_passes_role_type_flag(self, sample_role_doc_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_role_doc_json) as mock:
            get_role_doc("fedora.linux_system_roles.gfs2")
        mock.assert_called_once_with("-t", "role", "fedora.linux_system_roles.gfs2", "--json", collections_path=None)


class TestExtractRoleMetadata:
    def test_extracts_role_name(self, sample_role_doc):
        meta = extract_role_metadata(sample_role_doc)
        assert meta["role_name"] == "fedora.linux_system_roles.gfs2"

    def test_extracts_short_description(self, sample_role_doc):
        meta = extract_role_metadata(sample_role_doc)
        assert meta["short_description"] == "The gfs2 role."

    def test_extracts_entry_points(self, sample_role_doc):
        meta = extract_role_metadata(sample_role_doc)
        assert "main" in meta["entry_points"]
        main_ep = meta["entry_points"]["main"]
        assert main_ep["description"] == "The gfs2 role."
        assert len(main_ep["options"]) == 2

    def test_options_have_correct_fields(self, sample_role_doc):
        meta = extract_role_metadata(sample_role_doc)
        options = meta["entry_points"]["main"]["options"]
        cluster = next(o for o in options if o["name"] == "gfs2_cluster_name")
        assert cluster["type"] == "str"
        assert cluster["required"] is True

    def test_empty_doc_returns_empty(self):
        meta = extract_role_metadata({})
        assert meta["role_name"] == ""
        assert meta["short_description"] == ""
        assert meta["entry_points"] == {}

    def test_multiple_entry_points(self):
        doc = {
            "some.collection.role": {
                "collection": "some.collection",
                "entry_points": {
                    "main": {
                        "description": "Main entry.",
                        "options": {},
                    },
                    "configure": {
                        "description": "Configure only.",
                        "options": {
                            "config_path": {
                                "description": "Path to config.",
                                "type": "str",
                                "required": True,
                            },
                        },
                    },
                },
            },
        }
        meta = extract_role_metadata(doc)
        assert "main" in meta["entry_points"]
        assert "configure" in meta["entry_points"]
        assert len(meta["entry_points"]["configure"]["options"]) == 1


class TestTransformGalaxyToAnsibleDocFormat:
    def test_missing_doc_strings(self):
        entry = {"content_type": "module", "content_name": "test"}
        result = transform_galaxy_to_ansible_doc_format("ns.col.test", entry)
        doc = result["ns.col.test"]["doc"]
        assert doc["short_description"] == ""
        assert doc["options"] == {}

    def test_missing_short_description(self):
        entry = {
            "doc_strings": {
                "doc": {"description": ["Some desc"], "options": []},
                "examples": "",
                "return": [],
                "metadata": {},
            }
        }
        result = transform_galaxy_to_ansible_doc_format("ns.col.mod", entry)
        assert result["ns.col.mod"]["doc"]["short_description"] == ""

    def test_options_list_with_non_dict_items(self):
        entry = {
            "doc_strings": {
                "doc": {
                    "short_description": "Test",
                    "options": [
                        {"name": "valid", "type": "str"},
                        "not_a_dict",
                        42,
                    ],
                },
                "examples": "",
                "return": [],
                "metadata": {},
            }
        }
        result = transform_galaxy_to_ansible_doc_format("ns.col.mod", entry)
        opts = result["ns.col.mod"]["doc"]["options"]
        assert "valid" in opts
        assert len(opts) == 1

    def test_option_without_name_key(self):
        entry = {
            "doc_strings": {
                "doc": {
                    "short_description": "Test",
                    "options": [
                        {"type": "str", "required": True},
                    ],
                },
                "examples": "",
                "return": [],
                "metadata": {},
            }
        }
        result = transform_galaxy_to_ansible_doc_format("ns.col.mod", entry)
        assert result["ns.col.mod"]["doc"]["options"] == {}


class TestListPlugins:
    def test_returns_plugin_dict(self, sample_plugin_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_list_json) as mock:
            result = list_plugins("lookup")
        assert "netbox.netbox.nb_lookup" in result
        mock.assert_called_once_with("--list", "-t", "lookup", "--json", collections_path=None)

    def test_passes_collection_filter(self, sample_plugin_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_list_json) as mock:
            list_plugins("lookup", collection_filter="netbox.netbox")
        mock.assert_called_once_with(
            "--list", "-t", "lookup", "--json", "netbox.netbox",
            collections_path=None,
        )

    def test_rejects_invalid_plugin_type(self):
        with pytest.raises(ValidationError, match="Invalid plugin type"):
            list_plugins("bogus")


class TestGetPluginDoc:
    def test_returns_parsed_json(self, sample_plugin_doc, sample_plugin_doc_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_doc_json):
            result = get_plugin_doc("netbox.netbox.nb_lookup", "lookup")
        assert result == sample_plugin_doc

    def test_passes_type_flag(self, sample_plugin_doc_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_doc_json) as mock:
            get_plugin_doc("netbox.netbox.nb_lookup", "lookup")
        mock.assert_called_once_with(
            "-t", "lookup", "netbox.netbox.nb_lookup", "--json",
            collections_path=None, timeout=ANY,
        )

    def test_rejects_invalid_plugin_type(self):
        with pytest.raises(ValidationError, match="Invalid plugin type"):
            get_plugin_doc("foo.bar.baz", "bogus")


class TestGetPluginDocs:
    def test_batches_names_with_type_flag(self, sample_plugin_doc_json):
        names = ["ansible.builtin.env", "ansible.builtin.file"]
        with patch(
            "ansible_know.parser._run_ansible_doc", return_value=sample_plugin_doc_json,
        ) as mock:
            get_plugin_docs(names, "lookup")
        mock.assert_called_once_with(
            "-t", "lookup", "ansible.builtin.env", "ansible.builtin.file", "--json",
            collections_path=None, timeout=ANY,
        )

    def test_empty_names_skips_subprocess(self):
        with patch("ansible_know.parser._run_ansible_doc") as mock:
            assert get_plugin_docs([], "lookup") == {}
        mock.assert_not_called()


class TestLoadPluginMetadataBatch:
    def test_extracts_metadata_for_present_plugins(self, sample_plugin_doc):
        docs = {
            "netbox.netbox.nb_lookup": sample_plugin_doc["netbox.netbox.nb_lookup"],
        }
        with patch("ansible_know.parser.get_plugin_docs", return_value=docs):
            result = load_plugin_metadata_batch(
                ["netbox.netbox.nb_lookup", "missing.lookup"], "lookup",
            )
        assert set(result) == {"netbox.netbox.nb_lookup"}
        assert result["netbox.netbox.nb_lookup"]["plugin_type"] == "lookup"


class TestSearchPlugins:
    def test_filters_by_keyword(self, sample_plugin_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_list_json):
            result = search_plugins("netbox", plugin_type="lookup")
        assert "netbox.netbox.nb_lookup" in result
        assert len(result) == 1

    def test_search_all_types(self, sample_plugin_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_list_json):
            result = search_plugins("env")
        assert "ansible.builtin.env" in result

    def test_case_insensitive(self, sample_plugin_list_json):
        with patch("ansible_know.parser._run_ansible_doc", return_value=sample_plugin_list_json):
            result = search_plugins("NETBOX", plugin_type="lookup")
        assert "netbox.netbox.nb_lookup" in result


class TestExtractPluginMetadata:
    def test_extracts_name_and_type(self, sample_plugin_doc):
        meta = extract_plugin_metadata(sample_plugin_doc, "lookup")
        assert meta["plugin_name"] == "netbox.netbox.nb_lookup"
        assert meta["plugin_type"] == "lookup"

    def test_extracts_short_description(self, sample_plugin_doc):
        meta = extract_plugin_metadata(sample_plugin_doc, "lookup")
        assert meta["short_description"] == "Queries and returns elements from NetBox"

    def test_extracts_params(self, sample_plugin_doc):
        meta = extract_plugin_metadata(sample_plugin_doc, "lookup")
        names = [p["name"] for p in meta["params"]]
        assert "api_endpoint" in names
        assert "token" in names

    def test_extracts_examples(self, sample_plugin_doc):
        meta = extract_plugin_metadata(sample_plugin_doc, "lookup")
        assert "nb_lookup" in meta["examples"]
