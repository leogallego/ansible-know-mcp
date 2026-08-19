"""Tests for ansible_know.galaxy_v1."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ansible_know.errors import GalaxyError
from tests.conftest import SAMPLE_ROLE_README_HTML

SAMPLE_ROLE = {
    "id": 42,
    "username": "ansible-lockdown",
    "name": "rhel9_cis",
    "description": "CIS Benchmark for RHEL 9",
    "github_user": "ansible-lockdown",
    "github_repo": "RHEL9-CIS",
    "github_branch": "devel",
    "download_count": 9000,
    "summary_fields": {
        "tags": ["system", "security"],
        "versions": [{"name": "1.2.3"}],
        "dependencies": [{"namespace": "geerlingguy", "name": "repo"}],
    },
}

SAMPLE_LIST = {"count": 1, "next": None, "previous": None, "results": [SAMPLE_ROLE]}


@pytest.fixture(autouse=True)
def _clear_v1_cache():
    from ansible_know.galaxy_v1 import clear_cache
    clear_cache()
    yield
    clear_cache()


def _json_response(json_body, status=200):
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_body
    mock_resp.content = b"{}"
    mock_resp.headers = {}
    if status >= 400:
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=MagicMock(status_code=status),
        )
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


def _mock_http(json_body, status=200):
    mock_client = AsyncMock()
    mock_client.get.return_value = _json_response(json_body, status=status)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _mock_http_sequence(json_bodies):
    mock_client = AsyncMock()
    mock_client.get.side_effect = [_json_response(body) for body in json_bodies]
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _skip_discovery(client):
    client._api_root = "https://galaxy.ansible.com/api"
    client._v1_path = "v1/"
    return client


class TestSearchRoles:
    @pytest.mark.asyncio
    async def test_uses_keywords_order_by_page_size(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            result = await gc.search_roles("rhel9_cis")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["keywords"] == ["rhel9", "cis"]
        assert params["order_by"] == "-download_count"
        assert params["page_size"] == "10"
        assert "search" not in params
        assert "keyword" not in params
        url = mock_client.get.call_args.args[0]
        assert url.endswith("/api/v1/roles/")
        assert result["roles"][0]["role_name"] == "ansible-lockdown.rhel9_cis"

    @pytest.mark.asyncio
    async def test_keywords_are_lowercased(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            result = await gc.search_roles("RHEL9_CIS")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["keywords"] == ["rhel9", "cis"]
        assert result["query"] == "RHEL9_CIS"

    @pytest.mark.asyncio
    async def test_multi_word_sends_repeated_keywords(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            await gc.search_roles("win openssh")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["keywords"] == ["win", "openssh"]

    @pytest.mark.asyncio
    async def test_stopwords_are_dropped(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            await gc.search_roles("I want some role to manage win openssh")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["keywords"] == ["win", "openssh"]

    @pytest.mark.asyncio
    async def test_splits_hyphen_and_underscore(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            await gc.search_roles("lockdown RHEL9-CIS")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["keywords"] == ["lockdown", "rhel9", "cis"]

    @pytest.mark.asyncio
    async def test_empty_and_retries_longest_token(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http_sequence([
            {"count": 0, "results": []},
            SAMPLE_LIST,
        ])
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            result = await gc.search_roles("win openssh")
        assert mock_client.get.call_count == 2
        first = mock_client.get.call_args_list[0].kwargs["params"]["keywords"]
        second = mock_client.get.call_args_list[1].kwargs["params"]["keywords"]
        assert first == ["win", "openssh"]
        assert second == "openssh"
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_tags_sends_first_segment_only(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            await gc.search_roles("cis", tags="system,security")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["tags"] == "system"

    @pytest.mark.asyncio
    async def test_does_not_call_content_during_search(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            await gc.search_roles("rhel9_cis")
        urls = [c.args[0] for c in mock_client.get.call_args_list]
        assert all("/content/" not in u for u in urls)


class TestFetchRoleByName:
    @pytest.mark.asyncio
    async def test_lookup_uses_namespace_and_name_not_owner(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http(SAMPLE_LIST)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            role = await gc.fetch_role_by_name("ansible-lockdown", "rhel9_cis")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["namespace"] == "ansible-lockdown"
        assert params["name"] == "rhel9_cis"
        assert "owner__username" not in params
        assert role["id"] == 42

    @pytest.mark.asyncio
    async def test_lookup_canonicalizes_case_and_role_hyphens(self):
        """GitHub-style RHEL9-CIS: try hyphen form first, then underscore."""
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http_sequence([
            {"count": 0, "results": []},
            SAMPLE_LIST,
        ])
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            await gc.fetch_role_by_name("Ansible-Lockdown", "RHEL9-CIS")
        first = mock_client.get.call_args_list[0].kwargs["params"]
        second = mock_client.get.call_args_list[1].kwargs["params"]
        assert first["namespace"] == "ansible-lockdown"
        assert first["name"] == "rhel9-cis"
        assert second["name"] == "rhel9_cis"

    @pytest.mark.asyncio
    async def test_lookup_keeps_hyphenated_role_name_on_hit(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        hyphen_role = {
            **SAMPLE_ROLE,
            "username": "dev-sec",
            "name": "ssh-hardening",
        }
        mock_client = _mock_http({"count": 1, "results": [hyphen_role]})
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            role = await gc.fetch_role_by_name("dev-sec", "ssh-hardening")
        assert mock_client.get.call_count == 1
        params = mock_client.get.call_args.kwargs["params"]
        assert params["namespace"] == "dev-sec"
        assert params["name"] == "ssh-hardening"
        assert role["name"] == "ssh-hardening"

    @pytest.mark.asyncio
    async def test_lookup_does_not_turn_namespace_underscores_into_hyphens(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http({"count": 0, "results": []})
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            with pytest.raises(GalaxyError, match="not found"):
                await gc.fetch_role_by_name("ansible_lockdown", "RHEL9_CIS")
        first = mock_client.get.call_args_list[0].kwargs["params"]
        assert first["namespace"] == "ansible_lockdown"
        assert first["name"] == "rhel9_cis"
        assert all(
            call.kwargs["params"]["namespace"] == "ansible_lockdown"
            for call in mock_client.get.call_args_list
        )

    @pytest.mark.asyncio
    async def test_empty_results_raises(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http({"count": 0, "results": []})
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            with pytest.raises(GalaxyError, match="not found"):
                await gc.fetch_role_by_name("missing", "role")


class TestFetchStandaloneRoleDoc:
    @pytest.mark.asyncio
    async def test_parses_readme_html(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        list_resp = MagicMock()
        list_resp.json.return_value = SAMPLE_LIST
        list_resp.content = b"{}"
        list_resp.headers = {}
        list_resp.raise_for_status.return_value = None
        content_resp = MagicMock()
        content_resp.json.return_value = {
            "readme": "README.md",
            "readme_html": SAMPLE_ROLE_README_HTML,
        }
        content_resp.content = b"{}"
        content_resp.headers = {}
        content_resp.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get.side_effect = [list_resp, content_resp]
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            meta, prov = await gc.fetch_standalone_role_doc(
                "ansible-lockdown.rhel9_cis",
            )
        content_url = mock_client.get.call_args_list[1].args[0]
        assert content_url.endswith("/api/v1/roles/42/content/")
        assert meta["content_type"] == "standalone_role"
        assert meta["role_name"] == "ansible-lockdown.rhel9_cis"
        assert prov["doc_source"] == "galaxy_v1_readme"
        assert "main" in meta["entry_points"]
        assert meta["github_branch"] == "devel"
        assert "geerlingguy.repo" in meta["dependencies"] or meta["dependencies"]

    @pytest.mark.asyncio
    async def test_github_style_identifier_looks_up_canonical_name(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        empty_list = MagicMock()
        empty_list.json.return_value = {"count": 0, "results": []}
        empty_list.content = b"{}"
        empty_list.headers = {}
        empty_list.raise_for_status.return_value = None
        list_resp = MagicMock()
        list_resp.json.return_value = SAMPLE_LIST
        list_resp.content = b"{}"
        list_resp.headers = {}
        list_resp.raise_for_status.return_value = None
        content_resp = MagicMock()
        content_resp.json.return_value = {"readme": "README.md", "readme_html": ""}
        content_resp.content = b"{}"
        content_resp.headers = {}
        content_resp.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get.side_effect = [empty_list, list_resp, content_resp]
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            meta, _prov = await gc.fetch_standalone_role_doc(
                "ansible-lockdown.RHEL9-CIS",
            )
        first = mock_client.get.call_args_list[0].kwargs["params"]
        second = mock_client.get.call_args_list[1].kwargs["params"]
        assert first["name"] == "rhel9-cis"
        assert second["name"] == "rhel9_cis"
        assert meta["role_name"] == "ansible-lockdown.rhel9_cis"

    @pytest.mark.asyncio
    async def test_empty_html_is_metadata_success(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        list_resp = MagicMock()
        list_resp.json.return_value = SAMPLE_LIST
        list_resp.content = b"{}"
        list_resp.headers = {}
        list_resp.raise_for_status.return_value = None
        content_resp = MagicMock()
        content_resp.json.return_value = {"readme": "README.md", "readme_html": ""}
        content_resp.content = b"{}"
        content_resp.headers = {}
        content_resp.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get.side_effect = [list_resp, content_resp]
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            meta, prov = await gc.fetch_standalone_role_doc(
                "ansible-lockdown.rhel9_cis",
            )
        assert prov["doc_source"] == "galaxy_v1_metadata"
        assert meta["short_description"] == "CIS Benchmark for RHEL 9"
        assert "doc_warning" in prov

    @pytest.mark.asyncio
    async def test_hyphenated_name_roundtrip(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        list_resp = MagicMock()
        list_resp.json.return_value = SAMPLE_LIST
        list_resp.content = b"{}"
        list_resp.headers = {}
        list_resp.raise_for_status.return_value = None
        content_resp = MagicMock()
        content_resp.json.return_value = {"readme": "README.md", "readme_html": "<p>x</p>"}
        content_resp.content = b"{}"
        content_resp.headers = {}
        content_resp.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get.side_effect = [list_resp, content_resp]
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            meta, _ = await gc.fetch_standalone_role_doc("ansible-lockdown.rhel9_cis")
        params = mock_client.get.call_args_list[0].kwargs["params"]
        assert params["namespace"] == "ansible-lockdown"
        assert params["name"] == "rhel9_cis"
        assert meta["role_name"] == "ansible-lockdown.rhel9_cis"


class TestV1Discovery:
    @pytest.mark.asyncio
    async def test_requires_v1_not_v3(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http({"available_versions": {"v3": "v3/"}})
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = GalaxyV1Client(base_url="https://hub.example")
            with pytest.raises(GalaxyError, match="v1"):
                await gc.search_roles("cis")

    @pytest.mark.asyncio
    async def test_v1_http_404_raises_galaxy_error(self):
        from ansible_know.galaxy_v1 import GalaxyV1Client
        mock_client = _mock_http({}, status=404)
        with patch("ansible_know.galaxy_v1.httpx.AsyncClient", return_value=mock_client):
            gc = _skip_discovery(GalaxyV1Client())
            with pytest.raises(GalaxyError):
                await gc.search_roles("cis")


class TestV1DoesNotPoisonV3:
    @pytest.mark.asyncio
    async def test_missing_v1_does_not_set_v3_discovery_failed(self):
        from ansible_know.galaxy import GalaxyClient
        from ansible_know.galaxy_config import GalaxyServerConfig
        from ansible_know.galaxy_v1 import GalaxyV1Client

        config = GalaxyServerConfig(
            name="hub", url="https://hub.example/api",
        )
        v3_only = {"available_versions": {"v3": "v3/"}}
        search_payload = {
            "data": [],
            "meta": {"count": 0},
        }

        def _route(url, **kwargs):
            resp = MagicMock()
            resp.content = b"{}"
            resp.headers = {}
            resp.raise_for_status.return_value = None
            if "collection-versions" in str(url) or "search" in str(url):
                resp.json.return_value = search_payload
            else:
                resp.json.return_value = v3_only
            return resp

        shared = AsyncMock()
        shared.get.side_effect = _route
        v3 = GalaxyClient.from_config(config, http_client=shared)
        v1 = GalaxyV1Client.from_config(config, http_client=shared)
        with pytest.raises(GalaxyError, match="v1"):
            await v1.search_roles("cis")
        assert v3._discovery_failed is False
        assert v3._v3_path is None
        result = await v3.search_collections("net")
        assert result["count"] == 0
        assert v3._discovery_failed is False
        assert v3._v3_path == "v3/"
