"""CLI argument parsing for transport configuration.

Foundation-layer module: stdlib only, no imports from Domain,
External Access, or Orchestration.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Literal

__all__ = ["ServerConfig", "parse_args"]

_VALID_TRANSPORTS = ("stdio", "http")
_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8080


@dataclass(frozen=True)
class ServerConfig:
    """Resolved server transport configuration."""

    transport: Literal["stdio", "http"]
    host: str
    port: int


def _port_in_range(value: str) -> int:
    """Validate port is an integer in 1-65535."""
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'") from exc
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError(
            f"port must be between 1 and 65535, got {port}"
        )
    return port


def parse_args(argv: list[str] | None = None) -> ServerConfig:
    """Parse CLI arguments with environment variable fallbacks.

    Precedence: CLI flag > environment variable > default.
    """
    parser = argparse.ArgumentParser(
        prog="ansible-know-mcp",
        description="Ansible Know MCP Server",
    )
    parser.add_argument(
        "--transport",
        choices=_VALID_TRANSPORTS,
        default=os.environ.get("ANSIBLE_KNOW_TRANSPORT", "stdio"),
        help="Transport mode (default: stdio, env: ANSIBLE_KNOW_TRANSPORT)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("ANSIBLE_KNOW_HOST", _DEFAULT_HOST),
        help=f"Bind address for HTTP transport (default: {_DEFAULT_HOST}, env: ANSIBLE_KNOW_HOST)",
    )
    env_port = os.environ.get("ANSIBLE_KNOW_PORT")
    parser.add_argument(
        "--port",
        type=_port_in_range,
        default=env_port if env_port is not None else str(_DEFAULT_PORT),
        help=f"Listen port for HTTP transport (default: {_DEFAULT_PORT}, env: ANSIBLE_KNOW_PORT)",
    )

    args = parser.parse_args(argv)
    return ServerConfig(
        transport=args.transport,
        host=args.host,
        port=args.port,
    )
