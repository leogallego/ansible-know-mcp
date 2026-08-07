"""Tests for scripts/build_aap_manifests.py URL canonicalization helpers."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_aap_manifests.py"
_SPEC = importlib.util.spec_from_file_location("build_aap_manifests", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
build_aap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_aap)

AAP26_HTML = (
    "https://docs.redhat.com/en/documentation/"
    "red_hat_ansible_automation_platform/2.6/html/"
    "get_started-assembly_gs_platform_admin"
)
AAP26_BARE = (
    "https://docs.redhat.com/en/documentation/"
    "red_hat_ansible_automation_platform/2.6/"
    "get_started-assembly_gs_platform_admin"
)
AAP27_HTML = (
    "https://docs.redhat.com/en/documentation/"
    "red_hat_ansible_automation_platform/2.7/html/"
    "whats_new-overview_of_redhat_ansible_intro"
)
AAP27_BARE = (
    "https://docs.redhat.com/en/documentation/"
    "red_hat_ansible_automation_platform/2.7/"
    "whats_new-overview_of_redhat_ansible_intro"
)
AAP25_HTML = (
    "https://docs.redhat.com/en/documentation/"
    "red_hat_ansible_automation_platform/2.5/html/release_notes"
)


class TestCandidateUrls:
    def test_26_prefers_bare_then_original(self):
        assert build_aap._candidate_urls(AAP26_HTML, "2.6") == [AAP26_BARE, AAP26_HTML]

    def test_27_prefers_bare_then_original(self):
        assert build_aap._candidate_urls(AAP27_HTML, "2.7") == [AAP27_BARE, AAP27_HTML]

    def test_25_keeps_html_only(self):
        assert build_aap._candidate_urls(AAP25_HTML, "2.5") == [AAP25_HTML]

    def test_html_single_stripped_for_bare_versions(self):
        html_single = AAP26_HTML.replace("/html/", "/html-single/")
        bare = AAP26_BARE
        assert build_aap._candidate_urls(html_single, "2.6") == [bare, html_single]

    def test_already_bare_returns_single_candidate(self):
        assert build_aap._candidate_urls(AAP26_BARE, "2.6") == [AAP26_BARE]


class TestParseLandingJson:
    def test_plain_object(self):
        assert build_aap._parse_landing_json('{"product": "aap"}') == {"product": "aap"}

    def test_wrapped_result_string(self):
        inner = '{"product": "aap", "version": "2.7"}'
        raw = json.dumps({"result": inner})
        assert build_aap._parse_landing_json(raw)["version"] == "2.7"

    def test_invalid_nested_result_raises_value_error(self):
        raw = json.dumps({"result": "not-json{"})
        with pytest.raises(ValueError, match="not valid JSON"):
            build_aap._parse_landing_json(raw)


class TestTitleMatchesName:
    def test_matching_title(self):
        title = (
            "Red Hat Ansible Automation Platform | 2.6 | "
            "Get started as a platform administrator | Red Hat Documentation"
        )
        assert build_aap._title_matches_name(title, "Get started as an administrator")

    def test_rejects_misredirect_title(self):
        title = (
            "Red Hat Ansible Automation Platform | 2.6 | "
            "New features and enhancements | Red Hat Documentation"
        )
        assert not build_aap._title_matches_name(title, "Get started as an administrator")

    def test_empty_name_tokens_vacuously_true(self):
        assert build_aap._title_matches_name("Anything", "AAP")


def _mock_response(
    *,
    url: str,
    status_code: int = 200,
    title: str = "Guide title | Red Hat Documentation",
    soft_404: bool = False,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.url = httpx.URL(url)
    if soft_404:
        resp.text = (
            "<html><head><title>Page not found | Red Hat Documentation</title></head>"
            "<body><h1>404: Page not found</h1></body></html>"
        )
    else:
        resp.text = f"<html><head><title>{title}</title></head><body><h1>Hi</h1></body></html>"
    return resp


class TestResolveHttpCanonicalUrl:
    @pytest.mark.asyncio
    async def test_prefers_verified_bare_slug(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _mock_response(
                    url=AAP27_BARE,
                    title=(
                        "Red Hat Ansible Automation Platform | 2.7 | "
                        "Ansible Automation Platform release overview | Red Hat Documentation"
                    ),
                ),
            ]
        )
        result = await build_aap._resolve_http_canonical_url(
            client,
            AAP27_HTML,
            "2.7",
            "Ansible Automation Platform release overview",
            asyncio.Semaphore(1),
        )
        assert result == AAP27_BARE
        assert client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_falls_back_when_bare_is_soft_404(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _mock_response(url=AAP26_BARE, soft_404=True),
                _mock_response(
                    url=AAP26_HTML,
                    title=(
                        "Red Hat Ansible Automation Platform | 2.6 | "
                        "Get started as a platform administrator | Red Hat Documentation"
                    ),
                ),
            ]
        )
        result = await build_aap._resolve_http_canonical_url(
            client,
            AAP26_HTML,
            "2.6",
            "Get started as an administrator",
            asyncio.Semaphore(1),
        )
        assert result == AAP26_HTML
        assert client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_rejects_bare_misredirect_then_accepts_original(self):
        """If bare mis-redirects (wrong title), keep looking at original."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _mock_response(
                    url="https://docs.redhat.com/en/documentation/"
                    "red_hat_ansible_automation_platform/2.6/whats_new-aap_26",
                    title=(
                        "Red Hat Ansible Automation Platform | 2.6 | "
                        "New features and enhancements | Red Hat Documentation"
                    ),
                ),
                _mock_response(
                    url=AAP26_HTML,
                    title=(
                        "Red Hat Ansible Automation Platform | 2.6 | "
                        "Get started as a platform administrator | Red Hat Documentation"
                    ),
                ),
            ]
        )
        result = await build_aap._resolve_http_canonical_url(
            client,
            AAP26_HTML,
            "2.6",
            "Get started as an administrator",
            asyncio.Semaphore(1),
        )
        assert result == AAP26_HTML

    @pytest.mark.asyncio
    async def test_title_mismatched_bare_keeps_original_when_html_unusable(self):
        """Bare wrong-title must not rewrite when original is soft-404."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _mock_response(
                    url="https://docs.redhat.com/en/documentation/"
                    "red_hat_ansible_automation_platform/2.6/whats_new-aap_26",
                    title=(
                        "Red Hat Ansible Automation Platform | 2.6 | "
                        "New features and enhancements | Red Hat Documentation"
                    ),
                ),
                _mock_response(url=AAP26_HTML, soft_404=True),
            ]
        )
        result = await build_aap._resolve_http_canonical_url(
            client,
            AAP26_HTML,
            "2.6",
            "Get started as an administrator",
            asyncio.Semaphore(1),
        )
        assert result == AAP26_HTML
        assert client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_25_skips_network_via_canonicalize_entries(self):
        entries = [{
            "url": AAP25_HTML,
            "title": "Release notes",
            "topic": "whats_new",
        }]
        out = await build_aap._canonicalize_entries(entries, "2.5")
        assert out is entries
