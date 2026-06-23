"""Async utilities (Foundation layer — no internal dependencies)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, ParamSpec, TypeVar

import httpx

__all__ = ["run_in_executor", "_optional_client"]

P = ParamSpec("P")
R = TypeVar("R")


def run_in_executor(
    func: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> Coroutine[Any, Any, R]:
    """Run a blocking function in the default executor."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))


@asynccontextmanager
async def _optional_client(
    http_client: httpx.AsyncClient | None = None,
    **kwargs: Any,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a shared or newly-created HTTP client, closing only if newly-created."""
    if http_client is not None:
        yield http_client
    else:
        async with httpx.AsyncClient(**kwargs) as client:
            yield client
