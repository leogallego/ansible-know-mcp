"""Tests for ansible_know.async_utils."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ansible_know.async_utils import optional_http_client


class TestOptionalHttpClient:
    @pytest.mark.asyncio
    async def test_yields_shared_client_without_closing(self):
        shared = AsyncMock(spec=httpx.AsyncClient)
        shared.aclose = AsyncMock()

        async with optional_http_client(shared) as client:
            assert client is shared

        shared.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_and_closes_owned_client(self):
        owned = AsyncMock(spec=httpx.AsyncClient)
        owned.aclose = AsyncMock()
        factory = MagicMock(return_value=owned)

        with patch("ansible_know.async_utils.httpx.AsyncClient", factory):
            async with optional_http_client(None, timeout=10.0) as client:
                assert client is owned

        factory.assert_called_once_with(timeout=10.0)
        owned.aclose.assert_awaited_once()
