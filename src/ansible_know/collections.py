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

from ansible_know.errors import CollectionInstallError
from ansible_know.validation import sanitize_error

logger = logging.getLogger("ansible_know")

_VERSION_PARSE_RE = re.compile(r"(\S+\.\S+):(\d+\.\d+\.\d+\S*)")

_tmp_dir: tempfile.TemporaryDirectory | None = None
_installed: dict[str, str] = {}
_install_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()
_install_gate = threading.Lock()  # serializes all ansible-galaxy subprocess calls


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
    with _locks_lock:
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
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse installed version for %s", namespace)
    return "unknown"


MAX_TRACKED_COLLECTIONS = 100


def ensure_collection(namespace: str, version: str | None = None) -> dict:
    """Install a collection to the temp directory (thread-safe).

    Installs once and pins the resolved version. Subsequent calls skip
    unless a different version is explicitly requested.

    Returns dict with keys: namespace, version, status, message.
    """
    with _locks_lock:
        if len(_install_locks) >= MAX_TRACKED_COLLECTIONS and namespace not in _install_locks:
            raise CollectionInstallError(
                f"Too many collections tracked ({MAX_TRACKED_COLLECTIONS}). "
                "Restart the server to reset."
            )
        if namespace not in _install_locks:
            _install_locks[namespace] = threading.Lock()
        lock = _install_locks[namespace]

    with lock:
        current = _installed.get(namespace)
        if current and (not version or current == version):
            return {
                "namespace": namespace,
                "version": current,
                "status": "already_installed",
                "message": f"Collection {namespace} v{current} is already available.",
            }

        previous_version = current
        tmpdir = _get_or_create_tmpdir()
        galaxy = _find_ansible_galaxy()

        collection_spec = f"{namespace}:=={version}" if version else namespace
        cmd = [galaxy, "collection", "install", collection_spec, "-p", tmpdir, "--force"]

        with _install_gate:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired as exc:
                raise CollectionInstallError(
                    f"ansible-galaxy timed out installing {namespace}"
                ) from exc

            if result.returncode != 0:
                raise CollectionInstallError(
                    sanitize_error(result.stderr.strip())
                )

        installed_version = _parse_version(result.stdout, namespace, tmpdir)
        _installed[namespace] = installed_version

        if previous_version and previous_version != installed_version:
            message = (
                f"Installed {namespace} v{installed_version}, "
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
    with _locks_lock:
        if _tmp_dir is None:
            return None
        return _tmp_dir.name


def list_installed() -> dict[str, str]:
    with _locks_lock:
        return dict(_installed)
