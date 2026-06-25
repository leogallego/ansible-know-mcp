"""Tests for ansible_know.validation."""

from pathlib import Path

import pytest

from ansible_know.errors import ValidationError
from ansible_know.validation import (
    MAX_KEYWORD_LENGTH,
    MAX_NAMESPACE_LENGTH,
    MAX_QUERY_LENGTH,
    MAX_RESPONSE_SIZE,
    MAX_SKILL_NAME_LENGTH,
    MAX_TAGS_LENGTH,
    MAX_VERSION_LENGTH,
    sanitize_error,
    truncate_response,
    validate_doc_url,
    validate_fqcn,
    validate_install_path,
    validate_keyword,
    validate_namespace,
    validate_path_containment,
    validate_query,
    validate_skill_name,
    validate_tags,
    validate_version,
)


class TestValidateFqcn:
    def test_valid_three_segments(self):
        validate_fqcn("ansible.builtin.copy")

    def test_valid_with_underscores(self):
        validate_fqcn("my_namespace.my_collection.my_module")

    def test_valid_with_numbers(self):
        validate_fqcn("ns1.col2.mod3")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_fqcn("")

    def test_rejects_none_like(self):
        with pytest.raises(ValidationError):
            validate_fqcn("")

    def test_rejects_single_segment(self):
        with pytest.raises(ValidationError):
            validate_fqcn("copy")

    def test_rejects_two_segments(self):
        with pytest.raises(ValidationError):
            validate_fqcn("ansible.builtin")

    def test_rejects_four_segments(self):
        with pytest.raises(ValidationError):
            validate_fqcn("a.b.c.d")

    def test_rejects_dashes(self):
        with pytest.raises(ValidationError):
            validate_fqcn("my-ns.my-col.my-mod")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValidationError):
            validate_fqcn("../../etc/passwd")

    def test_rejects_spaces(self):
        with pytest.raises(ValidationError):
            validate_fqcn("a.b.c d")

    def test_rejects_shell_metacharacters(self):
        for char in [";", "|", "&", "$", "`", "(", ")"]:
            with pytest.raises(ValidationError):
                validate_fqcn(f"a.b.c{char}rm")

    def test_rejects_unicode(self):
        with pytest.raises(ValidationError):
            validate_fqcn("a.b.módule")

    def test_rejects_empty_segments(self):
        with pytest.raises(ValidationError):
            validate_fqcn("a..c")


class TestValidateNamespace:
    def test_valid_two_segments(self):
        validate_namespace("ansible.builtin")

    def test_valid_with_underscores(self):
        validate_namespace("my_ns.my_col")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_namespace("")

    def test_rejects_single_segment(self):
        with pytest.raises(ValidationError):
            validate_namespace("ansible")

    def test_rejects_three_segments(self):
        with pytest.raises(ValidationError):
            validate_namespace("a.b.c")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValidationError):
            validate_namespace("../etc")

    def test_rejects_dashes(self):
        with pytest.raises(ValidationError):
            validate_namespace("my-ns.my-col")

    def test_rejects_over_max_length(self):
        long_ns = "a" * 60 + "." + "b" * 60
        assert len(long_ns) < MAX_NAMESPACE_LENGTH
        validate_namespace(long_ns)

        too_long = "a" * 64 + "." + "b" * 64
        with pytest.raises(ValidationError):
            validate_namespace(too_long)


class TestValidateSkillName:
    def test_valid_two_segments(self):
        validate_skill_name("netbox.netbox")

    def test_valid_three_segments(self):
        validate_skill_name("netbox.netbox.netbox_device")

    def test_valid_with_underscores(self):
        validate_skill_name("my_ns.my_col.my_mod")

    def test_valid_with_numbers(self):
        validate_skill_name("ns1.col2.mod3")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_skill_name("")

    def test_rejects_single_segment(self):
        with pytest.raises(ValidationError):
            validate_skill_name("copy")

    def test_rejects_four_segments(self):
        with pytest.raises(ValidationError):
            validate_skill_name("a.b.c.d")

    def test_rejects_dashes(self):
        with pytest.raises(ValidationError):
            validate_skill_name("my-ns.my-col")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValidationError):
            validate_skill_name("../../etc/passwd")

    def test_rejects_shell_metacharacters(self):
        for char in [";", "|", "&", "$", "`"]:
            with pytest.raises(ValidationError):
                validate_skill_name(f"a.b{char}rm")

    def test_rejects_over_max_length(self):
        valid = "a" * 100 + "." + "b" * 100
        assert len(valid) < MAX_SKILL_NAME_LENGTH
        validate_skill_name(valid)

        too_long = "a" * 128 + "." + "b" * 128
        with pytest.raises(ValidationError):
            validate_skill_name(too_long)

    def test_rejects_slashes(self):
        with pytest.raises(ValidationError):
            validate_skill_name("a/b.c")


class TestValidateKeyword:
    def test_valid_keyword(self):
        validate_keyword("copy")

    def test_empty_keyword_allowed(self):
        validate_keyword("")

    def test_exact_max_length(self):
        validate_keyword("a" * MAX_KEYWORD_LENGTH)

    def test_over_max_length(self):
        with pytest.raises(ValidationError):
            validate_keyword("a" * (MAX_KEYWORD_LENGTH + 1))

    def test_unicode_allowed(self):
        validate_keyword("módule")

    def test_spaces_allowed(self):
        validate_keyword("copy file")


class TestValidateVersion:
    def test_valid_semver(self):
        validate_version("1.2.3")

    def test_valid_prerelease(self):
        validate_version("1.0.0-alpha")

    def test_valid_with_dots(self):
        validate_version("1.0.0.post1")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_version("")

    def test_rejects_shell_injection(self):
        with pytest.raises(ValidationError):
            validate_version("; rm -rf /")

    def test_rejects_spaces(self):
        with pytest.raises(ValidationError):
            validate_version("1.0 2")

    def test_rejects_over_max_length(self):
        with pytest.raises(ValidationError):
            validate_version("a" * (MAX_VERSION_LENGTH + 1))

    def test_exact_max_length(self):
        validate_version("a" * MAX_VERSION_LENGTH)


class TestValidateQuery:
    def test_valid_query(self):
        validate_query("playbook")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_query("")

    def test_rejects_whitespace_only(self):
        with pytest.raises(ValidationError):
            validate_query("   ")

    def test_exact_max_length(self):
        validate_query("a" * MAX_QUERY_LENGTH)

    def test_over_max_length(self):
        with pytest.raises(ValidationError):
            validate_query("a" * (MAX_QUERY_LENGTH + 1))

    def test_unicode_allowed(self):
        validate_query("módule guide")


class TestValidateTags:
    def test_valid_single_tag(self):
        validate_tags("networking")

    def test_valid_comma_separated(self):
        validate_tags("networking,cloud,security")

    def test_valid_with_underscores_dashes(self):
        validate_tags("my_tag,another-tag")

    def test_rejects_special_chars(self):
        with pytest.raises(ValidationError):
            validate_tags("valid,tags&inject=bad")

    def test_rejects_spaces(self):
        with pytest.raises(ValidationError):
            validate_tags("tag one, tag two")

    def test_over_max_length(self):
        with pytest.raises(ValidationError):
            validate_tags("a" * (MAX_TAGS_LENGTH + 1))

    def test_exact_max_length(self):
        validate_tags("a" * MAX_TAGS_LENGTH)


class TestValidateInstallPath:
    def test_valid_home_path(self, tmp_path):
        result = validate_install_path(str(tmp_path / "skills"))
        assert isinstance(result, Path)

    def test_rejects_etc(self):
        with pytest.raises(ValidationError, match="not allowed"):
            validate_install_path("/etc/evil")

    def test_rejects_usr(self):
        with pytest.raises(ValidationError, match="not allowed"):
            validate_install_path("/usr/local/evil")

    def test_rejects_bin(self):
        with pytest.raises(ValidationError, match="not allowed"):
            validate_install_path("/bin/evil")

    def test_rejects_sbin(self):
        with pytest.raises(ValidationError, match="not allowed"):
            validate_install_path("/sbin/evil")

    def test_returns_resolved_path(self, tmp_path):
        result = validate_install_path(str(tmp_path / "a" / ".." / "b"))
        assert ".." not in str(result)


class TestValidatePathContainment:
    def test_valid_child(self, tmp_path):
        child = tmp_path / "sub" / "file.txt"
        validate_path_containment(child, tmp_path)

    def test_rejects_escape(self, tmp_path):
        child = tmp_path / ".." / "etc" / "passwd"
        with pytest.raises(ValidationError, match="escapes"):
            validate_path_containment(child, tmp_path)

    def test_same_dir_is_valid(self, tmp_path):
        validate_path_containment(tmp_path, tmp_path)


class TestSanitizeError:
    def test_strips_home_path(self):
        msg = "Failed at /home/user/.ansible/tmp/foo: error"
        result = sanitize_error(msg)
        assert "/home/user" not in result
        assert "<path>" in result

    def test_strips_tmp_path(self):
        msg = "Error in /tmp/ansible_know_abc123/stuff"
        result = sanitize_error(msg)
        assert "/tmp/" not in result

    def test_strips_multiple_paths(self):
        msg = "Copy /home/a/src to /tmp/b/dst failed"
        result = sanitize_error(msg)
        assert "/home/a" not in result
        assert "/tmp/b" not in result

    def test_preserves_non_path_message(self):
        msg = "Module not found"
        assert sanitize_error(msg) == msg

    def test_preserves_non_sensitive_paths(self):
        msg = "Error at /something/else"
        assert sanitize_error(msg) == msg


class TestTruncateResponse:
    def test_preserves_small_response(self):
        text = "hello world"
        assert truncate_response(text) == text

    def test_truncates_large_response(self):
        text = "x" * (MAX_RESPONSE_SIZE + 100)
        result = truncate_response(text)
        assert len(result) < len(text)
        assert "Truncated" in result

    def test_exact_max_size_not_truncated(self):
        text = "x" * MAX_RESPONSE_SIZE
        assert truncate_response(text) == text

    def test_one_over_max_is_truncated(self):
        text = "x" * (MAX_RESPONSE_SIZE + 1)
        result = truncate_response(text)
        assert "Truncated" in result


class TestValidateDocUrl:
    def test_valid_ansible_core_url(self):
        validate_doc_url("https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_intro.html")

    def test_valid_ecosystem_url(self):
        validate_doc_url("https://docs.ansible.com/projects/lint/rules/")

    def test_valid_old_format_url(self):
        validate_doc_url("https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_intro.html")

    def test_rejects_non_ansible_domain(self):
        with pytest.raises(ValidationError):
            validate_doc_url("https://example.com/docs/page.html")

    def test_rejects_http(self):
        with pytest.raises(ValidationError):
            validate_doc_url("http://docs.ansible.com/page.html")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_doc_url("")

    def test_rejects_no_scheme(self):
        with pytest.raises(ValidationError):
            validate_doc_url("docs.ansible.com/page.html")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            validate_doc_url("https://docs.ansible.com/" + "a" * 2024)
