"""Collection bootstrapping — temp-install collections for ansible-doc access.

Manages a process-lifetime temporary directory where collections are installed
via `ansible-galaxy collection install`. Thread-safe for concurrent installs.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger("ansible_know")

_VERSION_PARSE_RE = re.compile(r"(\S+\.\S+):(\d+\.\d+\.\d+\S*)")
_PATH_RE = re.compile(r"/(?:home|tmp|usr|etc|var|opt)/\S+")

_tmp_dir: tempfile.TemporaryDirectory | None = None
_installed: dict[str, str] = {}
_install_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


class CollectionInstallError(Exception):
    """Raised when ansible-galaxy collection install fails."""


def _sanitize_error(msg: str) -> str:
    return _PATH_RE.sub("<path>", str(msg))


def _find_ansible_galaxy() -> str:
    env_bin = Path(sys.executable).parent / "ansible-galaxy"
    if env_bin.exists():
        return str(env_bin)
    found = shutil.which("ansible-galaxy")
    if found:
        return found
    raise CollectionInstallError(
        "ansible-galaxy not found. Install ansible-core: pip install ansible-core"
    )


def _get_or_create_tmpdir() -> str:
    global _tmp_dir
    if _tmp_dir is None:
        _tmp_dir = tempfile.TemporaryDirectory(prefix="ansible_know_")
    return _tmp_dir.name


def _parse_version(stdout: str, namespace: str, tmpdir: str) -> str:
    for match in _VERSION_PARSE_RE.finditer(stdout):
        if match.group(1) == namespace:
            return match.group(2)

    parts = namespace.split(".")
    manifest_path = Path(tmpdir) / "ansible_collections" / parts[0] / parts[1] / "MANIFEST.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            version = manifest.get("collection_info", {}).get("version")
            if version:
                return version
        except (json.JSONDecodeError, KeyError):
            pass

    logger.warning("Could not parse installed version for %s", namespace)
    return "unknown"


def ensure_collection(namespace: str, version: str | None = None) -> dict:
    """Install a collection to the temp directory.

    Returns dict with keys: namespace, version, status, message.
    - No version: always installs latest (overwrites previous).
    - Version specified: skips if already installed with same version.
    """
    with _locks_lock:
        if namespace not in _install_locks:
            _install_locks[namespace] = threading.Lock()
        lock = _install_locks[namespace]

    with lock:
        if version and _installed.get(namespace) == version:
            return {
                "namespace": namespace,
                "version": version,
                "status": "already_installed",
                "message": f"Collection {namespace} v{version} is already available.",
            }

        previous_version = _installed.get(namespace)
        tmpdir = _get_or_create_tmpdir()
        galaxy = _find_ansible_galaxy()

        collection_spec = f"{namespace}:=={version}" if version else namespace
        cmd = [galaxy, "collection", "install", collection_spec, "-p", tmpdir, "--force"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            raise CollectionInstallError(
                f"ansible-galaxy timed out installing {namespace}"
            )

        if result.returncode != 0:
            raise CollectionInstallError(
                _sanitize_error(result.stderr.strip())
            )

        installed_version = _parse_version(result.stdout, namespace, tmpdir)
        _installed[namespace] = installed_version

        if previous_version and previous_version != installed_version:
            message = (
                f"Installed {namespace} v{installed_version} (latest), "
                f"replacing previously installed v{previous_version}."
            )
        elif version:
            message = f"Installed {namespace} v{installed_version}."
        else:
            message = f"Installed {namespace} v{installed_version} (latest)."

        return {
            "namespace": namespace,
            "version": installed_version,
            "status": "installed",
            "message": message,
        }


def get_collections_path() -> str | None:
    if _tmp_dir is None:
        return None
    return _tmp_dir.name


def list_installed() -> dict[str, str]:
    return dict(_installed)
