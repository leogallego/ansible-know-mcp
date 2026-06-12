"""Tests for ansible_know.readme_parser."""

from __future__ import annotations

from tests.conftest import (
    SAMPLE_ROLE_README_HTML,
    SAMPLE_ROLE_README_HTML_CODEBLOCK_VARS,
    SAMPLE_ROLE_README_HTML_HEADING_VARS,
)


class TestParseRoleReadmeDescription:
    def test_extracts_first_paragraph(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme(SAMPLE_ROLE_README_HTML)
        assert "Configure time synchronization" in result["description"]

    def test_empty_html_returns_empty_description(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme("")
        assert result["description"] == ""

    def test_minimal_html(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme("<p>Just a paragraph.</p>")
        assert result["description"] == "Just a paragraph."


class TestParseRoleReadmeTableVariables:
    def test_extracts_table_variables(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme(SAMPLE_ROLE_README_HTML)
        variables = result["variables"]
        assert len(variables) == 2
        names = [v["name"] for v in variables]
        assert "timesync_ntp_servers" in names
        assert "timesync_ptp_domains" in names

    def test_table_variable_fields(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme(SAMPLE_ROLE_README_HTML)
        ntp_var = next(v for v in result["variables"] if v["name"] == "timesync_ntp_servers")
        assert ntp_var["default"] == "[]"
        assert ntp_var["description"] == "List of NTP servers"

    def test_no_tables_returns_empty_variables(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme("<h1>Role</h1><p>No tables here.</p>")
        assert result["variables"] == []


class TestParseRoleReadmeHeadingVariables:
    def test_extracts_heading_per_variable(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme(SAMPLE_ROLE_README_HTML_HEADING_VARS)
        variables = result["variables"]
        names = [v["name"] for v in variables]
        assert "sap_state" in names
        assert "sap_instance_number" in names

    def test_heading_variable_metadata(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme(SAMPLE_ROLE_README_HTML_HEADING_VARS)
        sap_state = next(v for v in result["variables"] if v["name"] == "sap_state")
        assert sap_state["type"] == "str"
        assert sap_state["default"] == "present"


class TestParseRoleReadmeCodeBlockVariables:
    def test_extracts_codeblock_per_variable(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme(SAMPLE_ROLE_README_HTML_CODEBLOCK_VARS)
        variables = result["variables"]
        names = [v["name"] for v in variables]
        assert "docker_edition" in names
        assert "docker_packages_state" in names

    def test_codeblock_variable_default(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme(SAMPLE_ROLE_README_HTML_CODEBLOCK_VARS)
        edition = next(v for v in result["variables"] if v["name"] == "docker_edition")
        assert edition["default"] == "ce"


class TestParseRoleReadmeExamples:
    def test_extracts_yaml_examples(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme(SAMPLE_ROLE_README_HTML)
        assert "hosts: all" in result["examples"]
        assert "roles:" in result["examples"]

    def test_no_examples_returns_empty(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme("<h1>Role</h1><p>No code blocks.</p>")
        assert result["examples"] == ""


class TestParseRoleReadmeDependencies:
    def test_none_dependency(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme(SAMPLE_ROLE_README_HTML)
        assert result["dependencies"] == []

    def test_extracts_fqcn_dependencies(self):
        from ansible_know.readme_parser import parse_role_readme
        html = """
        <h1>Role</h1>
        <p>A role.</p>
        <h2>Dependencies</h2>
        <ul>
        <li>some.collection.other_role</li>
        <li>another.collection.role_name</li>
        </ul>
        """
        result = parse_role_readme(html)
        assert "some.collection.other_role" in result["dependencies"]
        assert "another.collection.role_name" in result["dependencies"]


class TestParseRoleReadmeMalformed:
    def test_never_raises_on_malformed_html(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme("<h1>Unclosed<p>Bad <table><tr><td>html")
        assert isinstance(result, dict)
        assert "description" in result
        assert "variables" in result

    def test_handles_none_like_empty(self):
        from ansible_know.readme_parser import parse_role_readme
        result = parse_role_readme("")
        assert result == {"description": "", "variables": [], "examples": "", "dependencies": []}


class TestParseRoleReadmeSizeLimit:
    def test_truncates_at_1mb(self):
        from ansible_know.readme_parser import parse_role_readme
        huge_html = "<h1>Role</h1><p>Desc.</p>" + "<p>x</p>" * 200_000
        assert len(huge_html) > 1_000_000
        result = parse_role_readme(huge_html)
        assert isinstance(result, dict)
