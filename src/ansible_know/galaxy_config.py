"""Galaxy server configuration from ansible.cfg.

Reads [galaxy_server.*] sections to support multiple Galaxy-compatible
endpoints (public Galaxy, private Automation Hub, AAP Gateway) with
per-server authentication.
"""

from __future__ import annotations

import configparser
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ansible_know.config import GALAXY_BASE_URL

logger = logging.getLogger("ansible_know")

PUBLIC_GALAXY_URL = "https://galaxy.ansible.com"


@dataclass(frozen=True)
class GalaxyServerConfig:
    """Configuration for a single Galaxy-compatible server."""

    name: str
    url: str
    token: str | None = None
    username: str | None = None
    password: str | None = None
    auth_url: str | None = None
    client_id: str | None = None
    validate_certs: bool = True
    timeout: int = 60


def find_ansible_cfg() -> Path | None:
    """Resolve ansible.cfg using Ansible's standard lookup order."""
    env = os.environ.get("ANSIBLE_CONFIG")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        return None

    candidates = [
        Path.cwd() / "ansible.cfg",
        Path.home() / ".ansible.cfg",
        Path("/etc/ansible/ansible.cfg"),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _env_override(server_name: str, key: str) -> str | None:
    """Check for ANSIBLE_GALAXY_SERVER_{NAME}_{KEY} env var override."""
    env_key = f"ANSIBLE_GALAXY_SERVER_{server_name.upper()}_{key.upper()}"
    return os.environ.get(env_key)


def _sanitize_credential(value: str | None) -> str | None:
    """Strip control characters that could enable header injection."""
    if value is None:
        return None
    sanitized = value.replace("\r", "").replace("\n", "").strip()
    if sanitized != value.strip():
        logger.warning("Stripped control characters from credential value")
    return sanitized or None


def _read_server(
    cfg: configparser.ConfigParser, name: str,
) -> GalaxyServerConfig | None:
    """Read a single [galaxy_server.NAME] section with env var overrides."""
    section = f"galaxy_server.{name}"

    def _get(key: str, fallback: str | None = None) -> str | None:
        return _env_override(name, key) or cfg.get(section, key, fallback=fallback)

    url = _get("url")
    if not url:
        logger.warning("Galaxy server '%s' has no url, skipping", name)
        return None

    validate_raw = _get("validate_certs", "true")
    validate_certs = validate_raw.lower() not in ("false", "0", "no", "off")

    timeout_raw = _get("timeout", "60")
    try:
        timeout = int(timeout_raw)
    except (ValueError, TypeError):
        timeout = 60

    return GalaxyServerConfig(
        name=name,
        url=url.rstrip("/"),
        token=_sanitize_credential(_get("token")),
        username=_sanitize_credential(_get("username")),
        password=_sanitize_credential(_get("password")),
        auth_url=_sanitize_credential(_get("auth_url")),
        client_id=_sanitize_credential(_get("client_id")),
        validate_certs=validate_certs,
        timeout=timeout,
    )


def load_galaxy_servers() -> list[GalaxyServerConfig]:
    """Load Galaxy server configurations from ansible.cfg.

    Resolution order:
    1. Parse ansible.cfg [galaxy] server_list + [galaxy_server.*] sections
    2. Apply ANSIBLE_GALAXY_SERVER_{NAME}_{KEY} env var overrides
    3. Fall back to ANSIBLE_KNOW_GALAXY_URL or public Galaxy
    """
    cfg_path = find_ansible_cfg()
    servers: list[GalaxyServerConfig] = []

    if cfg_path:
        logger.info("Reading Galaxy config from %s", cfg_path)
        cfg = configparser.ConfigParser()
        cfg.read(str(cfg_path))

        server_list_raw = cfg.get("galaxy", "server_list", fallback="")
        server_names = [s.strip() for s in server_list_raw.split(",") if s.strip()]

        for name in server_names:
            server = _read_server(cfg, name)
            if server:
                servers.append(server)

    if not servers:
        fallback_url = GALAXY_BASE_URL
        servers.append(GalaxyServerConfig(
            name="galaxy",
            url=fallback_url.rstrip("/"),
        ))
        if fallback_url != PUBLIC_GALAXY_URL:
            logger.info("Using custom Galaxy URL: %s", fallback_url)

    no_public = os.environ.get(
        "ANSIBLE_KNOW_NO_PUBLIC_GALAXY", "",
    ).strip().lower() in ("1", "true", "yes")
    if not no_public:
        seen_urls = {s.url.rstrip("/") for s in servers}
        if PUBLIC_GALAXY_URL not in seen_urls:
            servers.append(GalaxyServerConfig(
                name="public_galaxy",
                url=PUBLIC_GALAXY_URL,
            ))

    return servers
