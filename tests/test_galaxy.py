"""Tests for ansible_know.galaxy."""

from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

from ansible_know.galaxy import GalaxyClient, GalaxyError, clear_cache


@pytest.fixture(autouse=True)
def reset_galaxy_cache():
    """Clear Galaxy caches between tests."""
    clear_cache()
    yield
    clear_cache()


SAMPLE_VERSIONS_RESPONSE = {
    "meta": {"count": 42},
    "links": {"first": None, "previous": None, "next": None, "last": None},
    "data": [
        {
            "version": "3.23.0",
            "href": "/api/v3/.../versions/3.23.0/",
            "created_at": "2026-05-07T13:31:02.008964Z",
            "updated_at": "2026-05-07T13:31:02.008964Z",
            "requires_ansible": ">=2.15.0",
            "marks": [],
        }
    ],
}


def _mock_client_get(response_json):
    """Create a mock httpx.AsyncClient whose .get() returns response_json."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_json
    mock_resp.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestLatestVersion:
    @pytest.mark.asyncio
    async def test_returns_latest_version(self):
        mock_client = _mock_client_get(SAMPLE_VERSIONS_RESPONSE)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            version = await client.latest_version("netbox", "netbox")
        assert version == "3.23.0"
        call_url = mock_client.get.call_args[0][0]
        assert "netbox/netbox/versions/" in call_url
        call_params = mock_client.get.call_args[1].get("params", {})
        assert call_params.get("ordering") == "-version"

    @pytest.mark.asyncio
    async def test_raises_on_empty_versions(self):
        empty_response = {"meta": {"count": 0}, "links": {}, "data": []}
        mock_client = _mock_client_get(empty_response)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="No versions found"):
                await client.latest_version("nonexistent", "collection")

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        import httpx as real_httpx
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = real_httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        mock_resp.content = b""
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("ansible_know.galaxy.httpx.AsyncClient", return_value=mock_client):
            client = GalaxyClient()
            with pytest.raises(GalaxyError, match="Galaxy API error"):
                await client.latest_version("bad", "collection")
