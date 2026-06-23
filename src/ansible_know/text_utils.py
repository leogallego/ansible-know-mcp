"""Text cleaning utilities for RTD markdown content (Foundation layer)."""

from __future__ import annotations

import re

__all__ = [
    "clean_rtd_markdown",
]

_DOCTYPE_RE = re.compile(r"<!DOCTYPE\s+html>", re.IGNORECASE)
_H1_RE = re.compile(r"^# (.+?)(?:\s*\{#[\w-]+\})?\s*$", re.MULTILINE)
_EXCESS_BLANKS_RE = re.compile(r"\n{3,}")
_PERMALINK_RE = re.compile(r"\s*\[[¶]\]\(#[^)]*(?:\s*\"[^\"]*\")?\)")


def clean_rtd_markdown(raw: str) -> tuple[str, str]:
    """Clean RTD markdown output and extract title.

    Returns (cleaned_content, title). Title is empty string if no H1 found.
    """
    if not raw:
        return "", ""

    lines = raw.split("\n")
    for i, line in enumerate(lines[:5]):
        if _DOCTYPE_RE.search(line):
            lines[i] = ""
            break

    text = "\n".join(lines)

    match = _H1_RE.search(text)
    if match:
        text = text[match.start():]
        title = _PERMALINK_RE.sub("", match.group(1)).strip()
    else:
        title = ""

    text = _EXCESS_BLANKS_RE.sub("\n\n", text)
    return text.strip(), title
