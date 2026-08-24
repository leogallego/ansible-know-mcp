"""Tests for ansible_know.text_utils AsciiDoc conversion."""

from ansible_know.text_utils import clean_asciidoc

_SYNTHETIC_ADOC = """= Sample CoP Page

NOTE: Read this first.

[%collapsible]
====
Hidden details
====

Explanations:: Why this matters.

[source,yaml]
----
- name: Example
  ansible.builtin.debug:
    msg: hello
----

See <<_anchor,Be descriptive>> and <<_nolabel>>.
"""


def test_clean_asciidoc_empty():
    assert clean_asciidoc("") == ("", "")


def test_clean_asciidoc_synthetic_fixture():
    content, title = clean_asciidoc(_SYNTHETIC_ADOC)
    assert title == "Sample CoP Page"
    assert content.startswith("# Sample CoP Page")
    assert "[%collapsible]" not in content
    assert "====" not in content
    assert "**Explanations**" in content
    assert "```yaml" in content
    assert "```" in content
    assert "Be descriptive" in content
    assert "<<_anchor" not in content
    assert "<<_nolabel>>" not in content
    assert "> **Note:**" in content
