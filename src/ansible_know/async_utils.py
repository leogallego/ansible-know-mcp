"""Async utilities (Foundation layer — no internal dependencies)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any, ParamSpec, TypeVar

__all__ = ["run_in_executor"]

P = ParamSpec("P")
R = TypeVar("R")


def run_in_executor(
    func: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> Coroutine[Any, Any, R]:
    """Run a blocking function in the default executor."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))
