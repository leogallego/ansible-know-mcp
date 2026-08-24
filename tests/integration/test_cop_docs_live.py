"""Live CoP fetch — skipped unless --run-integration."""

from __future__ import annotations

import httpx
import pytest

from ansible_know.docs import fetch_cop_content

pytestmark = pytest.mark.integration

_COP_NAMING = (
    "https://raw.githubusercontent.com/redhat-cop/automation-good-practices"
    "/main/naming_conventions/README.adoc"
)


@pytest.mark.asyncio
async def test_live_fetch_naming_conventions():
    try:
        result = await fetch_cop_content(_COP_NAMING)
    except (httpx.HTTPError, OSError) as exc:
        pytest.skip(f"network error: {exc}")
    assert result["title"] == "Naming conventions"
    assert "Be descriptive" in result["content"]
    assert result["content"]
