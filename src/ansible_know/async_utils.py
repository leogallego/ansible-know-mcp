"""Async utilities (Foundation layer — no internal dependencies)."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

__all__ = ["run_in_executor"]


def run_in_executor(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking function in the default executor."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))
