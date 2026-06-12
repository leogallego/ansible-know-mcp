"""Parse Galaxy role README HTML into structured data.

Uses Python's built-in html.parser.HTMLParser — no external dependencies.
Best-effort parsing: never raises on malformed input.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_FQCN_RE = re.compile(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+")
_YAML_INDICATORS = ("---", "hosts:", "roles:", "tasks:", "- name:")
_VAR_SECTIONS = ("role variables", "variables", "defaults", "role defaults")
_DEP_SECTIONS = ("dependencies", "requirements")
_EXAMPLE_SECTIONS = ("example playbook", "example", "examples", "usage")
_MAX_HTML_SIZE = 1_000_000


def parse_role_readme(html: str) -> dict[str, Any]:
    """Parse role README HTML into structured data.

    Returns dict with keys:
    - description (str): first paragraph(s) before first heading
    - variables (list[dict]): [{name, type, required, default, description}]
    - examples (str): YAML code blocks concatenated
    - dependencies (list[str]): role FQCNs from Dependencies section
    """
    if not html:
        return {"description": "", "variables": [], "examples": "", "dependencies": []}

    html = html[:_MAX_HTML_SIZE]

    parser = _ReadmeParser()
    parser.feed(html)

    # Flush any remaining description parts (for minimal HTML with no headings)
    parser._flush_description()

    variables = parser.table_variables or parser.heading_variables or parser.codeblock_variables

    return {
        "description": parser.description.strip(),
        "variables": variables,
        "examples": "\n\n".join(parser.examples).strip(),
        "dependencies": parser.dependencies,
    }


class _ReadmeParser(HTMLParser):
    """State-machine parser for role README HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.description: str = ""
        self.table_variables: list[dict[str, Any]] = []
        self.heading_variables: list[dict[str, Any]] = []
        self.codeblock_variables: list[dict[str, Any]] = []
        self.examples: list[str] = []
        self.dependencies: list[str] = []

        self._tag_stack: list[str] = []
        self._current_section: str = ""
        self._seen_first_heading: bool = False
        self._desc_parts: list[str] = []

        self._in_table: bool = False
        self._table_headers: list[str] = []
        self._current_row: list[str] = []
        self._in_thead: bool = False
        self._in_th: bool = False
        self._in_td: bool = False

        self._in_pre: bool = False
        self._in_code: bool = False
        self._code_text: str = ""

        self._heading_level: int = 0
        self._heading_text: str = ""
        self._in_heading: bool = False

        self._in_p: bool = False
        self._p_text: str = ""

        self._in_li: bool = False
        self._li_text: str = ""

        self._current_heading_var: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tag_stack.append(tag)

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = True
            self._heading_level = int(tag[1])
            self._heading_text = ""
        elif tag == "table":
            self._in_table = True
            self._table_headers = []
        elif tag == "thead":
            self._in_thead = True
        elif tag == "th":
            self._in_th = True
            self._heading_text = ""
        elif tag == "td":
            self._in_td = True
            self._p_text = ""
        elif tag == "tr":
            self._current_row = []
        elif tag == "pre":
            self._in_pre = True
            self._code_text = ""
        elif tag == "code":
            self._in_code = True
            if not self._in_pre:
                self._code_text = ""
        elif tag == "p":
            self._in_p = True
            self._p_text = ""
        elif tag == "li":
            self._in_li = True
            self._li_text = ""

    def handle_endtag(self, tag: str) -> None:
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = False
            heading_lower = self._heading_text.strip().lower()

            # First heading (usually h1 title) — keep collecting description after it
            if not self._seen_first_heading:
                self._seen_first_heading = True
            # Subsequent headings (h2+) — flush description and start sections
            elif self._heading_level >= 2:
                self._flush_description()

            self._finalize_heading_var()

            if any(s in heading_lower for s in _VAR_SECTIONS):
                self._current_section = "variables"
            elif any(s in heading_lower for s in _DEP_SECTIONS):
                self._current_section = "dependencies"
            elif any(s in heading_lower for s in _EXAMPLE_SECTIONS):
                self._current_section = "examples"
            else:
                if _SNAKE_CASE_RE.match(self._heading_text.strip()) and self._current_section == "variables":
                    self._current_heading_var = {
                        "name": self._heading_text.strip(),
                        "type": None,
                        "required": None,
                        "default": None,
                        "description": "",
                    }
                else:
                    self._current_section = ""

        elif tag == "table":
            self._in_table = False
        elif tag == "thead":
            self._in_thead = False
        elif tag == "th":
            self._in_th = False
            self._table_headers.append(self._heading_text.strip())
        elif tag == "td":
            self._in_td = False
            self._current_row.append(self._p_text.strip())
        elif tag == "tr":
            if not self._in_thead and self._current_row and self._table_headers:
                self._process_table_row()
        elif tag == "pre":
            self._in_pre = False
            code = self._code_text.strip()
            if code:
                if self._current_section == "variables" or self._current_section == "":
                    self._try_parse_codeblock_variable(code)
                if any(ind in code for ind in _YAML_INDICATORS):
                    self.examples.append(code)
        elif tag == "code":
            self._in_code = False
        elif tag == "p":
            self._in_p = False
            text = self._p_text.strip()
            if text:
                # Collect description before first heading OR after h1 but before first section (h2+)
                if not self._seen_first_heading or (self._seen_first_heading and not self._current_section):
                    self._desc_parts.append(text)
                elif self._current_heading_var is not None:
                    self._process_heading_var_paragraph(text)
                elif self._current_section == "variables" and not self._in_table:
                    if self.codeblock_variables and not self.codeblock_variables[-1].get("description"):
                        self.codeblock_variables[-1]["description"] = text
                elif self._current_section == "dependencies":
                    self._extract_dependencies(text)
        elif tag == "li":
            self._in_li = False
            text = self._li_text.strip()
            if self._current_section == "dependencies" and text:
                self._extract_dependencies(text)

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._heading_text += data
        elif self._in_th:
            self._heading_text += data
        elif self._in_td:
            self._p_text += data
        elif self._in_pre or self._in_code:
            self._code_text += data
        elif self._in_p:
            self._p_text += data
        elif self._in_li:
            self._li_text += data
        # Collect loose text before first heading OR after h1 but before first section
        elif not self._seen_first_heading or (self._seen_first_heading and not self._current_section):
            stripped = data.strip()
            if stripped:
                self._desc_parts.append(stripped)

    def _flush_description(self) -> None:
        if self._desc_parts and not self.description:
            self.description = " ".join(self._desc_parts)
            self._desc_parts = []

    def _process_table_row(self) -> None:
        header_map: dict[str, int] = {}
        for i, h in enumerate(self._table_headers):
            h_lower = h.lower().strip()
            if "variable" in h_lower or "name" in h_lower:
                header_map["name"] = i
            elif "default" in h_lower:
                header_map["default"] = i
            elif "required" in h_lower:
                header_map["required"] = i
            elif "description" in h_lower:
                header_map["description"] = i
            elif "type" in h_lower:
                header_map["type"] = i

        if "name" not in header_map:
            return

        row = self._current_row
        name_idx = header_map["name"]
        if name_idx >= len(row):
            return

        name = row[name_idx].strip()
        if not name:
            return

        var: dict[str, Any] = {
            "name": name,
            "type": None,
            "required": None,
            "default": None,
            "description": "",
        }
        if "type" in header_map and header_map["type"] < len(row):
            var["type"] = row[header_map["type"]].strip() or None
        if "default" in header_map and header_map["default"] < len(row):
            var["default"] = row[header_map["default"]].strip() or None
        if "required" in header_map and header_map["required"] < len(row):
            req_text = row[header_map["required"]].strip().lower()
            var["required"] = req_text in ("true", "yes")
        if "description" in header_map and header_map["description"] < len(row):
            var["description"] = row[header_map["description"]].strip()

        self.table_variables.append(var)

    def _process_heading_var_paragraph(self, text: str) -> None:
        if self._current_heading_var is None:
            return

        text_lower = text.lower().strip()
        if text_lower.startswith("type:"):
            self._current_heading_var["type"] = text[5:].strip() or None
        elif text_lower.startswith("default:"):
            self._current_heading_var["default"] = text[8:].strip() or None
        elif text_lower.startswith("required:"):
            req = text[9:].strip().lower()
            self._current_heading_var["required"] = req in ("true", "yes")
        else:
            if self._current_heading_var["description"]:
                self._current_heading_var["description"] += " " + text
            else:
                self._current_heading_var["description"] = text

    def _finalize_heading_var(self) -> None:
        if self._current_heading_var is not None:
            self.heading_variables.append(self._current_heading_var)
            self._current_heading_var = None

    def _try_parse_codeblock_variable(self, code: str) -> None:
        if any(ind in code for ind in _YAML_INDICATORS):
            return

        for line in code.strip().splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if _SNAKE_CASE_RE.match(key):
                    self.codeblock_variables.append({
                        "name": key,
                        "type": None,
                        "required": None,
                        "default": value or None,
                        "description": "",
                    })

    def _extract_dependencies(self, text: str) -> None:
        if text.lower().strip() in ("none", "none.", "n/a", "n/a.", ""):
            return
        matches = _FQCN_RE.findall(text)
        for m in matches:
            if m not in self.dependencies:
                self.dependencies.append(m)
