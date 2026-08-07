"""Text cleaning utilities for RTD / docs markdown content (Foundation layer)."""

from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

__all__ = [
    "clean_redhat_markdown",
    "clean_rtd_markdown",
    "html_to_markdown",
]

logger = logging.getLogger("ansible_know")

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


def _format_markdown_link(label: str, href: str) -> str:
    """Build a CommonMark link, escaping delimiters; drop unsafe schemes."""
    href = href.strip()
    label = label.strip() or href
    if not href:
        return label

    parsed = urlparse(href)
    scheme = (parsed.scheme or "").lower()
    if scheme and scheme not in {"http", "https"}:
        return label
    if href.startswith("//"):
        return label

    safe_label = (
        label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    )
    safe_href = href.replace("\\", "\\\\").replace(")", "\\)")
    if re.search(r"\s", safe_href):
        return f"[{safe_label}](<{safe_href}>)"
    return f"[{safe_label}]({safe_href})"


def _extract_tagged_region(raw_html: str, tag: str) -> str | None:
    """Extract first ``<tag>...</tag>`` region without catastrophic backtracking."""
    lower = raw_html.lower()
    start_token = f"<{tag}"
    close_token = f"</{tag}>"
    search_from = 0
    while True:
        start = lower.find(start_token, search_from)
        if start < 0:
            return None
        after = start + len(start_token)
        if after < len(raw_html) and raw_html[after] not in " \t\r\n/>":
            search_from = after
            continue
        end = lower.find(close_token, after)
        if end < 0:
            return None
        return raw_html[start:end + len(close_token)]


def _extract_role_main_div(raw_html: str) -> str | None:
    """Extract the closed ``<div role="main">...</div>`` region, if present."""
    match = re.search(
        r'(?is)<div\b[^>]*\brole\s*=\s*["\']main["\'][^>]*>',
        raw_html,
    )
    if not match:
        return None
    start = match.start()
    # Depth-count div tags from the opening role=main tag to its closer.
    lower = raw_html.lower()
    pos = match.end()
    depth = 1
    while pos < len(lower) and depth > 0:
        next_open = lower.find("<div", pos)
        next_close = lower.find("</div>", pos)
        if next_close < 0:
            return None
        if next_open >= 0 and next_open < next_close:
            after = next_open + 4
            if after < len(raw_html) and raw_html[after] in " \t\r\n/>":
                gt = lower.find(">", next_open)
                if gt < 0:
                    return None
                depth += 1
                pos = gt + 1
            else:
                pos = after
            continue
        depth -= 1
        pos = next_close + len("</div>")
    if depth != 0:
        return None
    return raw_html[start:pos]


def _extract_content_html(raw_html: str) -> str:
    """Prefer ``<article>`` / ``<main>`` / ``role=main``; else full document."""
    for tag in ("article", "main"):
        region = _extract_tagged_region(raw_html, tag)
        if region:
            return region
    role_main = _extract_role_main_div(raw_html)
    if role_main is not None:
        return role_main
    return raw_html


class _HtmlToMarkdownParser(HTMLParser):
    """Minimal HTML→Markdown converter for Sphinx / RTD / docs HTML fragments."""

    _SKIP_TAGS = frozenset({
        "script", "style", "nav", "header", "footer", "aside",
        "button", "svg", "noscript", "form",
    })
    _BLOCK_TAGS = frozenset({
        "p", "div", "section", "li", "tr", "blockquote", "pre",
        "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table",
        "article", "main", "br", "hr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_pre = False
        self._list_stack: list[str] = []
        self._ol_index: list[int] = []
        self._href: str | None = None
        self._pending_href_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag in self._SKIP_TAGS:
                self._skip_depth += 1
            return
        if tag in self._SKIP_TAGS:
            self._skip_depth = 1
            return

        attr_map = {k.lower(): (v or "") for k, v in attrs}

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._ensure_blank_line()
            self._parts.append("#" * int(tag[1]) + " ")
        elif tag == "br":
            self._parts.append("\n")
        elif tag == "hr":
            self._ensure_blank_line()
            self._parts.append("---\n\n")
        elif tag == "p":
            self._ensure_blank_line()
        elif tag in {"ul", "ol"}:
            self._ensure_blank_line()
            self._list_stack.append(tag)
            self._ol_index.append(0)
        elif tag == "li":
            self._ensure_newline()
            depth = max(len(self._list_stack) - 1, 0)
            indent = "  " * depth
            if self._list_stack and self._list_stack[-1] == "ol":
                self._ol_index[-1] += 1
                self._parts.append(f"{indent}{self._ol_index[-1]}. ")
            else:
                self._parts.append(f"{indent}- ")
        elif tag == "pre":
            self._ensure_blank_line()
            self._emit("```\n")
            self._in_pre = True
        elif tag == "code" and not self._in_pre:
            self._emit("`")
        elif tag in {"strong", "b"}:
            self._emit("**")
        elif tag in {"em", "i"}:
            self._emit("*")
        elif tag == "a":
            href = attr_map.get("href", "")
            if href and not href.startswith("#"):
                self._href = href
                self._pending_href_text = []
        elif tag == "img":
            alt = attr_map.get("alt", "")
            if alt:
                self._emit(alt)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag in self._SKIP_TAGS:
                self._skip_depth -= 1
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p"}:
            self._emit("\n\n")
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            if self._ol_index:
                self._ol_index.pop()
            self._emit("\n")
        elif tag == "li":
            self._emit("\n")
        elif tag == "pre":
            if self._href is not None:
                if not "".join(self._pending_href_text).endswith("\n"):
                    self._emit("\n")
            elif not self._parts or not self._parts[-1].endswith("\n"):
                self._emit("\n")
            self._emit("```\n\n")
            self._in_pre = False
        elif tag == "code" and not self._in_pre:
            self._emit("`")
        elif tag in {"strong", "b"}:
            self._emit("**")
        elif tag in {"em", "i"}:
            self._emit("*")
        elif tag == "a" and self._href is not None:
            label = "".join(self._pending_href_text).strip() or self._href
            self._parts.append(_format_markdown_link(label, self._href))
            self._href = None
            self._pending_href_text = []
        elif tag in self._BLOCK_TAGS:
            self._ensure_newline()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_pre:
            self._emit(data)
            return
        if not data.strip():
            if self._href is not None:
                pending = "".join(self._pending_href_text)
                if data and pending and not pending.endswith(("\n", " ", "\t")):
                    self._emit(" ")
                return
            if data and self._parts and not self._parts[-1].endswith(("\n", " ", "\t")):
                self._emit(" ")
            return
        collapsed = re.sub(r"[ \t\r\n]+", " ", data)
        self._emit(collapsed)

    def _emit(self, text: str) -> None:
        if self._href is not None:
            self._pending_href_text.append(text)
        else:
            self._parts.append(text)

    def _ensure_newline(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def _ensure_blank_line(self) -> None:
        if not self._parts:
            return
        suffix = "".join(self._parts[-3:])
        if suffix.endswith("\n\n"):
            return
        if suffix.endswith("\n"):
            self._parts.append("\n")
        else:
            self._parts.append("\n\n")

    def get_markdown(self) -> str:
        return "".join(self._parts)


def html_to_markdown(raw_html: str) -> str:
    """Convert HTML documentation fragments to markdown (stdlib, no extra deps).

    Contract:
        Prefers ``<article>`` / ``<main>`` / closed ``role=main`` regions.
        On HTMLParser failure (malformed markup), falls back to tag-stripped
        plain text rather than raising — callers treat this as best-effort
        conversion for untrusted upstream HTML.
    """
    if not raw_html:
        return ""
    fragment = _extract_content_html(raw_html)
    parser = _HtmlToMarkdownParser()
    try:
        parser.feed(fragment)
        parser.close()
    except (ValueError, TypeError, AssertionError, RecursionError):
        # HTMLParser has no stable public error type for broken markup.
        logger.debug(
            "HTML→markdown parse failed; using stripped text fallback",
            exc_info=True,
        )
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", fragment)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return html.unescape(re.sub(r"\s+", " ", text)).strip()
    return parser.get_markdown()
