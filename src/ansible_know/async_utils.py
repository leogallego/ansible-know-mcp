"""Async utilities (Foundation layer — no internal dependencies)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, ParamSpec, TypeVar

import httpx

__all__ = ["optional_http_client", "run_in_executor"]

P = ParamSpec("P")
R = TypeVar("R")


def run_in_executor(
    func: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> Coroutine[Any, Any, R]:
    """Run a blocking function in the default executor."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))


@asynccontextmanager
async def optional_http_client(
    http_client: httpx.AsyncClient | None,
    *,
    timeout: float | httpx.Timeout = 30.0,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield *http_client* or a short-lived owned client.

    When *http_client* is provided it is yielded as-is and never closed.
    Otherwise a new ``httpx.AsyncClient`` is created with *timeout* and
    closed when the context exits.
    """
    if http_client is not None:
        yield http_client
        return
    client = httpx.AsyncClient(timeout=timeout)
    try:
        yield client
    finally:
        await client.aclose()
