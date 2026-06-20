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
from ansible_know.types import EnsureCollectionResult
from ansible_know.validation import sanitize_error

__all__ = ["CollectionManager"]

logger = logging.getLogger("ansible_know")

_VERSION_PARSE_RE = re.compile(r"(\S+\.\S+):(\d+\.\d+\.\d+\S*)")


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


def _parse_version(stdout: str, collection_fqcn: str, tmpdir: str) -> str:
    for match in _VERSION_PARSE_RE.finditer(stdout):
        if match.group(1) == collection_fqcn:
            return match.group(2)

    parts = collection_fqcn.split(".")
    manifest_path = Path(tmpdir) / "ansible_collections" / parts[0] / parts[1] / "MANIFEST.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            version = manifest.get("collection_info", {}).get("version")
            if version:
                return version
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse installed version for %s", collection_fqcn)
    return "unknown"


class CollectionManager:
    """Manages temporary collection installation and tracking.

    Thread-safe manager for installing Ansible collections to a temporary
    directory and tracking their versions. Each instance maintains its own
    isolated state.
    """

    MAX_TRACKED_COLLECTIONS = 100

    def __init__(self) -> None:
        self._tmp_dir: tempfile.TemporaryDirectory | None = None
        self._installed: dict[str, str] = {}
        self._install_locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._install_gate = threading.Lock()  # serializes all ansible-galaxy subprocess calls

    def _get_or_create_tmpdir(self) -> str:
        with self._locks_lock:
            if self._tmp_dir is None:
                self._tmp_dir = tempfile.TemporaryDirectory(prefix="ansible_know_")
            return self._tmp_dir.name

    def ensure_collection(self, collection_fqcn: str, version: str | None = None) -> EnsureCollectionResult:
        """Install a collection to the temp directory (thread-safe).

        Args:
            collection_fqcn: Two-part collection identifier (e.g., "netbox.netbox").
            version: Optional version constraint (e.g., "4.1.0").

        Installs once and pins the resolved version. Subsequent calls skip
        unless a different version is explicitly requested.

        Returns dict with keys: namespace, version, status, message.
        """
        with self._locks_lock:
            if len(self._install_locks) >= self.MAX_TRACKED_COLLECTIONS and collection_fqcn not in self._install_locks:
                raise CollectionInstallError(
                    f"Too many collections tracked ({self.MAX_TRACKED_COLLECTIONS}). "
                    "Restart the server to reset."
                )
            if collection_fqcn not in self._install_locks:
                self._install_locks[collection_fqcn] = threading.Lock()
            lock = self._install_locks[collection_fqcn]

        with lock:
            current = self._installed.get(collection_fqcn)
            if current and (not version or current == version):
                return {
                    "namespace": collection_fqcn,
                    "version": current,
                    "status": "already_installed",
                    "message": f"Collection {collection_fqcn} v{current} is already available.",
                }

            previous_version = current
            tmpdir = self._get_or_create_tmpdir()
            galaxy = _find_ansible_galaxy()

            collection_spec = f"{collection_fqcn}:=={version}" if version else collection_fqcn
            cmd = [galaxy, "collection", "install", collection_spec, "-p", tmpdir, "--force"]

            with self._install_gate:
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise CollectionInstallError(
                        f"ansible-galaxy timed out installing {collection_fqcn}"
                    ) from exc

                if result.returncode != 0:
                    raise CollectionInstallError(
                        sanitize_error(result.stderr.strip())
                    )

            installed_version = _parse_version(result.stdout, collection_fqcn, tmpdir)
            self._installed[collection_fqcn] = installed_version

            if previous_version and previous_version != installed_version:
                message = (
                    f"Installed {collection_fqcn} v{installed_version}, "
                    f"replacing previously installed v{previous_version}."
                )
            elif version:
                message = f"Installed {collection_fqcn} v{installed_version}."
            else:
                message = f"Installed {collection_fqcn} v{installed_version} (latest)."

            return {
                "namespace": collection_fqcn,
                "version": installed_version,
                "status": "installed",
                "message": message,
            }

    def get_collections_path(self) -> str | None:
        with self._locks_lock:
            if self._tmp_dir is None:
                return None
            return self._tmp_dir.name

    def list_installed(self) -> dict[str, str]:
        with self._locks_lock:
            return dict(self._installed)
