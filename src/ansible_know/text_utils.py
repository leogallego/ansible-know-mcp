"""Text cleaning utilities for RTD markdown content (Foundation layer)."""

from __future__ import annotations

import re

__all__ = [
    "clean_redhat_markdown",
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

    text = _PERMALINK_RE.sub("", text)
    text = _EXCESS_BLANKS_RE.sub("\n\n", text)
    return text.strip(), title


_RH_COPY_LINK_RE = re.compile(
    r"Copy\s*link\s*(?:Link\s*copied\s*(?:to\s*clipboard)?!?)?$",
    re.MULTILINE,
)
_RH_SKIP_SECTIONS = {"legal notice"}


def clean_redhat_markdown(raw: str) -> tuple[str, str]:
    """Clean Red Hat docs markdown and extract title.

    Returns (cleaned_content, title). Title is empty string if no H1 found.
    Strips Red Hat boilerplate (Legal Notice, Copy link artifacts) and
    collapses excess blank lines.
    """
    if not raw:
        return "", ""

    text = _RH_COPY_LINK_RE.sub("", raw)

    match = _H1_RE.search(text)
    if match:
        text = text[match.start():]
        title = match.group(1).strip()
    else:
        title = ""

    lines = text.split("\n")
    filtered: list[str] = []
    skip_until_next_h2 = False
    for line in lines:
        h2_match = re.match(r"^##\s+(.+)$", line)
        if h2_match:
            heading_lower = h2_match.group(1).strip().lower()
            if heading_lower in _RH_SKIP_SECTIONS:
                skip_until_next_h2 = True
                continue
            skip_until_next_h2 = False
        if skip_until_next_h2:
            continue
        filtered.append(line)

    text = "\n".join(filtered)
    text = _EXCESS_BLANKS_RE.sub("\n\n", text)
    return text.strip(), title
