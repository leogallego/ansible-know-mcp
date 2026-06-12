"""Integration tests for real ansible-doc invocation.

Run with: pytest --run-integration tests/integration/
Requires: ansible-core installed in the environment.
"""

import shutil

import pytest

from ansible_know.errors import AnsibleDocError, CollectionNotFoundError

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_ansible_doc():
    if not shutil.which("ansible-doc"):
        pytest.skip("ansible-doc not found")


class TestRealAnsibleDoc:
    def test_list_builtin_modules(self):
        from ansible_know.parser import list_modules

        modules = list_modules(namespace="ansible.builtin")
        assert len(modules) > 10
        assert "ansible.builtin.copy" in modules
        assert "ansible.builtin.file" in modules

    def test_get_builtin_module_doc(self):
        from ansible_know.parser import get_module_doc

        doc = get_module_doc("ansible.builtin.copy")
        assert "ansible.builtin.copy" in doc
        module_doc = doc["ansible.builtin.copy"]
        assert "doc" in module_doc
        assert module_doc["doc"]["short_description"]

    def test_search_builtin_modules(self):
        from ansible_know.parser import search_modules

        results = search_modules("copy", namespace="ansible.builtin")
        assert "ansible.builtin.copy" in results

    def test_extract_metadata_from_real_doc(self):
        from ansible_know.parser import extract_module_metadata, get_module_doc

        doc = get_module_doc("ansible.builtin.copy")
        metadata = extract_module_metadata(doc)
        assert metadata["module_name"] == "ansible.builtin.copy"
        assert metadata["short_description"]
        assert len(metadata["params"]) > 0
        assert isinstance(metadata["is_api_module"], bool)

    def test_missing_collection_returns_empty_or_raises(self):
        from ansible_know.parser import extract_module_metadata, get_module_doc

        try:
            doc = get_module_doc("nonexistent.collection.module_name")
        except (AnsibleDocError, CollectionNotFoundError):
            return
        with pytest.raises(AnsibleDocError, match="not found"):
            extract_module_metadata(doc)


class TestRealAnsibleDocRoles:
    def test_list_roles_returns_dict(self):
        from ansible_know.parser import list_roles

        roles = list_roles(namespace="ansible.builtin")
        assert isinstance(roles, dict)

    def test_get_role_doc_without_argument_specs_returns_empty(self):
        from ansible_know.parser import list_roles

        roles = list_roles()
        if not roles:
            pytest.skip("No roles available")

        from ansible_know.parser import get_role_doc

        role_name = next(iter(roles))
        doc = get_role_doc(role_name)
        assert isinstance(doc, dict)
