"""Text cleaning utilities for RTD markdown content (Foundation layer)."""

from __future__ import annotations

import re

import httpx

__all__ = [
    "clean_rtd_markdown",
    "fetch_rtd_markdown",
    "RTDMarkdownResult",
]

_DOCTYPE_RE = re.compile(r"<!DOCTYPE\s+html>", re.IGNORECASE)
_H1_RE = re.compile(r"^# (.+?)(?:\s*\{#[\w-]+\})?\s*$", re.MULTILINE)
_EXCESS_BLANKS_RE = re.compile(r"\n{3,}")
_PERMALINK_RE = re.compile(r"\s*\[[¶]\]\(#[^)]*(?:\s*\"[^\"]*\")?\)")


class RTDMarkdownResult(tuple):
    """A 3-tuple representing (content, title, tokens) with an extra source_url attribute."""
    def __new__(cls, content: str, title: str, tokens: int, source_url: str = "") -> RTDMarkdownResult:
        obj = super().__new__(cls, (content, title, tokens))
        obj.source_url = source_url
        return obj


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


async def fetch_rtd_markdown(
    url: str,
    client: httpx.AsyncClient,
    max_size: int | None = None,
) -> RTDMarkdownResult:
    """Fetch markdown from ReadTheDocs API and clean it.

    Returns RTDMarkdownResult which acts as a 3-tuple of (content, title, tokens).
    """
    resp = await client.get(
        url,
        headers={"Accept": "text/markdown"},
        follow_redirects=True,
        timeout=30.0,
    )
    resp.raise_for_status()

    if max_size is not None and len(resp.content) > max_size:
        raise ValueError(
            f"Response too large: {len(resp.content)} bytes (max {max_size})"
        )

    content_type = resp.headers.get("content-type", "")
    if "text/markdown" not in content_type:
        raise ValueError(f"Expected text/markdown but got {content_type!r}")

    tokens_str = resp.headers.get("x-markdown-tokens", "0")
    try:
        tokens = int(tokens_str)
    except ValueError:
        tokens = 0

    content, title = clean_rtd_markdown(resp.text)
    return RTDMarkdownResult(content, title, tokens, source_url=str(resp.url))
