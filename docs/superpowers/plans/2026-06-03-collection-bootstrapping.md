# Collection Bootstrapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `ensure_collection` MCP tool that installs missing Ansible collections to a temporary directory so the agent can query their docs without requiring the user to install them manually.

**Architecture:** New `collections.py` module owns all temp-dir management, version tracking, and `ansible-galaxy` subprocess calls. `parser.py` gains `ANSIBLE_COLLECTIONS_PATH` env injection so `ansible-doc` finds temp-installed collections. `server.py` exposes the new tool and adds error hints in existing tools when collections are missing.

**Tech Stack:** Python 3.10+, `tempfile.TemporaryDirectory`, `subprocess.run`, `threading.Lock`, `ansible-galaxy` CLI

**Spec:** `docs/superpowers/specs/2026-06-03-collection-bootstrapping-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/ansible_know/collections.py` | Create | Collection install state, locking, `ansible-galaxy` subprocess, version parsing, error sanitization |
| `tests/test_collections.py` | Create | Unit tests for the collections module |
| `src/ansible_know/parser.py` | Modify | Inject `ANSIBLE_COLLECTIONS_PATH` into `_run_ansible_doc` subprocess env |
| `tests/test_parser.py` | Modify | Add env injection tests |
| `src/ansible_know/server.py` | Modify | Add `ensure_collection` tool + error hints in existing tools |
| `tests/test_server.py` | Modify | Add tool + error hint tests |

---

### Task 1: Core collections module — install, version tracking, error handling

**Files:**
- Create: `tests/test_collections.py`
- Create: `src/ansible_know/collections.py`

This task builds the full `collections.py` module and its tests: `_find_ansible_galaxy`, `ensure_collection`, `get_collections_path`, `list_installed`, `CollectionInstallError`, version parsing, and thread-safe locking.

- [ ] **Step 1: Write test file with all collection module tests**

```python
"""Tests for ansible_know.collections."""

import json
import threading
from unittest.mock import patch, MagicMock

import pytest

from ansible_know.collections import (
    CollectionInstallError,
    ensure_collection,
    get_collections_path,
    list_installed,
)


@pytest.fixture(autouse=True)
def reset_collections_state():
    """Reset module-level state between tests."""
    import ansible_know.collections as col
    col._installed = {}
    col._install_locks = {}
    old_tmp = col._tmp_dir
    col._tmp_dir = None
    yield
    col._tmp_dir = None
    if old_tmp is not None:
        try:
            old_tmp.cleanup()
        except Exception:
            pass


def _make_subprocess_result(stdout="", stderr="", returncode=0):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


class TestGetCollectionsPath:
    def test_returns_none_before_install(self):
        assert get_collections_path() is None

    def test_returns_path_after_install(self):
        galaxy_stdout = "Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout)):
                ensure_collection("netbox.netbox")
        path = get_collections_path()
        assert path is not None
        assert "ansible_collections" not in path


class TestListInstalled:
    def test_returns_empty_before_install(self):
        assert list_installed() == {}

    def test_returns_installed_after_install(self):
        galaxy_stdout = "Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout)):
                ensure_collection("netbox.netbox")
        installed = list_installed()
        assert "netbox.netbox" in installed


class TestEnsureCollectionInstalls:
    def test_installs_collection(self):
        galaxy_stdout = "Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout)) as mock_run:
                result = ensure_collection("netbox.netbox")
        assert result["status"] == "installed"
        assert result["namespace"] == "netbox.netbox"
        assert result["version"] == "4.1.0"
        args = mock_run.call_args[0][0]
        assert "collection" in args
        assert "install" in args
        assert "netbox.netbox" in args

    def test_installs_with_version_pin(self):
        galaxy_stdout = "Installing 'netbox.netbox:3.9.0' to '<path>'\nnetbox.netbox:3.9.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout)) as mock_run:
                result = ensure_collection("netbox.netbox", version="3.9.0")
        assert result["version"] == "3.9.0"
        args = mock_run.call_args[0][0]
        assert "netbox.netbox:==3.9.0" in args

    def test_no_version_always_installs(self):
        galaxy_stdout_1 = "Installing 'netbox.netbox:4.0.0' to '<path>'\nnetbox.netbox:4.0.0 was installed successfully"
        galaxy_stdout_2 = "Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout_1)):
                ensure_collection("netbox.netbox")
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout_2)) as mock_run:
                result = ensure_collection("netbox.netbox")
        assert result["status"] == "installed"
        assert result["version"] == "4.1.0"
        mock_run.assert_called_once()

    def test_no_version_reports_replacement(self):
        galaxy_stdout_1 = "Installing 'netbox.netbox:3.9.0' to '<path>'\nnetbox.netbox:3.9.0 was installed successfully"
        galaxy_stdout_2 = "Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout_1)):
                ensure_collection("netbox.netbox", version="3.9.0")
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout_2)):
                result = ensure_collection("netbox.netbox")
        assert "replacing" in result["message"].lower()
        assert "3.9.0" in result["message"]

    def test_skips_matching_pin(self):
        galaxy_stdout = "Installing 'netbox.netbox:3.9.0' to '<path>'\nnetbox.netbox:3.9.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout)):
                ensure_collection("netbox.netbox", version="3.9.0")
            with patch("subprocess.run") as mock_run:
                result = ensure_collection("netbox.netbox", version="3.9.0")
        assert result["status"] == "already_installed"
        mock_run.assert_not_called()

    def test_reinstalls_different_version(self):
        galaxy_stdout_1 = "Installing 'netbox.netbox:3.9.0' to '<path>'\nnetbox.netbox:3.9.0 was installed successfully"
        galaxy_stdout_2 = "Installing 'netbox.netbox:4.0.0' to '<path>'\nnetbox.netbox:4.0.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout_1)):
                ensure_collection("netbox.netbox", version="3.9.0")
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout_2)):
                result = ensure_collection("netbox.netbox", version="4.0.0")
        assert result["status"] == "installed"
        assert result["version"] == "4.0.0"
        assert list_installed()["netbox.netbox"] == "4.0.0"


class TestEnsureCollectionErrors:
    def test_galaxy_failure_raises(self):
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(
                stderr="ERROR! Failed to resolve collection netbox.netbox at /home/user/.ansible/tmp",
                returncode=1,
            )):
                with pytest.raises(CollectionInstallError, match="Failed to resolve"):
                    ensure_collection("netbox.netbox")

    def test_galaxy_failure_sanitizes_paths(self):
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(
                stderr="ERROR! at /home/user/.ansible/collections/path: denied",
                returncode=1,
            )):
                with pytest.raises(CollectionInstallError) as exc_info:
                    ensure_collection("netbox.netbox")
                assert "/home/user" not in str(exc_info.value)

    def test_timeout_raises(self):
        import subprocess as sp
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="ansible-galaxy", timeout=120)):
                with pytest.raises(CollectionInstallError, match="timed out"):
                    ensure_collection("netbox.netbox")


class TestVersionParsing:
    def test_parses_version_from_stdout(self):
        galaxy_stdout = "Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout)):
                result = ensure_collection("netbox.netbox")
        assert result["version"] == "4.1.0"

    def test_fallback_to_manifest(self, tmp_path):
        galaxy_stdout = "Some unexpected output format"
        manifest_data = {"collection_info": {"version": "3.5.0"}}
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout)):
                import ansible_know.collections as col
                import tempfile
                col._tmp_dir = tempfile.TemporaryDirectory()
                manifest_dir = (
                    col._tmp_dir.name
                    / "ansible_collections" / "netbox" / "netbox"
                ) if False else None
                # Create manifest path within the temp dir
                from pathlib import Path
                manifest_dir = Path(col._tmp_dir.name) / "ansible_collections" / "netbox" / "netbox"
                manifest_dir.mkdir(parents=True, exist_ok=True)
                (manifest_dir / "MANIFEST.json").write_text(json.dumps(manifest_data))
                result = ensure_collection("netbox.netbox")
        assert result["version"] == "3.5.0"

    def test_fallback_to_unknown(self):
        galaxy_stdout = "Some unexpected output format"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout)):
                result = ensure_collection("netbox.netbox")
        assert result["version"] == "unknown"


class TestConcurrentInstall:
    def test_same_collection_serialized(self):
        call_count = 0
        def slow_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_subprocess_result(
                stdout=f"Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
            )

        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", side_effect=slow_run):
                t1 = threading.Thread(target=ensure_collection, args=("netbox.netbox",))
                t2 = threading.Thread(target=ensure_collection, args=("netbox.netbox",))
                t1.start()
                t2.start()
                t1.join()
                t2.join()
        assert call_count == 2  # both run (no version pin = always install latest)

    def test_different_collections_parallel(self):
        call_order = []
        def tracking_run(*args, **kwargs):
            cmd = args[0]
            ns = [a for a in cmd if "." in a and "ansible-galaxy" not in a][0]
            call_order.append(ns)
            return _make_subprocess_result(
                stdout=f"Installing '{ns}:1.0.0' to '<path>'\n{ns}:1.0.0 was installed successfully"
            )

        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", side_effect=tracking_run):
                t1 = threading.Thread(target=ensure_collection, args=("netbox.netbox",))
                t2 = threading.Thread(target=ensure_collection, args=("community.general",))
                t1.start()
                t2.start()
                t1.join()
                t2.join()
        assert len(call_order) == 2
        assert set(call_order) == {"netbox.netbox", "community.general"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_collections.py -v`
Expected: `ModuleNotFoundError: No module named 'ansible_know.collections'`

- [ ] **Step 3: Create the collections module**

```python
"""Collection bootstrapping — temp-install collections for ansible-doc access.

Manages a process-lifetime temporary directory where collections are installed
via `ansible-galaxy collection install`. Thread-safe for concurrent installs.
"""

from __future__ import annotations

import json
import logging
import os
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_collections.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/collections.py tests/test_collections.py
git commit -m "Add collections module for temp-installing Ansible collections

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Parser env injection — make ansible-doc find temp-installed collections

**Files:**
- Modify: `src/ansible_know/parser.py:34-56`
- Modify: `tests/test_parser.py`

This task modifies `_run_ansible_doc` to inject `ANSIBLE_COLLECTIONS_PATH` into the subprocess environment when collections have been installed, and adds tests for the three scenarios: no collections path, with path, and preserving existing env var.

- [ ] **Step 1: Write the new parser tests**

Add these tests to `tests/test_parser.py`:

```python
class TestRunAnsibleDocEnvInjection:
    def test_injects_collections_path(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("ansible_know.collections.get_collections_path", return_value="/tmp/ansible_know_abc123"):
                with patch("subprocess.run", return_value=MagicMock(
                    returncode=0, stdout='{}', stderr='',
                )) as mock_run:
                    from ansible_know.parser import _run_ansible_doc
                    _run_ansible_doc("--list", "--json")
                    env = mock_run.call_args[1]["env"]
                    assert "/tmp/ansible_know_abc123" in env["ANSIBLE_COLLECTIONS_PATH"]

    def test_preserves_existing_collections_path(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("ansible_know.collections.get_collections_path", return_value="/tmp/ansible_know_abc123"):
                with patch.dict("os.environ", {"ANSIBLE_COLLECTIONS_PATH": "/existing/path"}):
                    with patch("subprocess.run", return_value=MagicMock(
                        returncode=0, stdout='{}', stderr='',
                    )) as mock_run:
                        from ansible_know.parser import _run_ansible_doc
                        _run_ansible_doc("--list", "--json")
                        env = mock_run.call_args[1]["env"]
                        assert env["ANSIBLE_COLLECTIONS_PATH"].startswith("/tmp/ansible_know_abc123")
                        assert "/existing/path" in env["ANSIBLE_COLLECTIONS_PATH"]

    def test_no_injection_when_no_collections(self):
        with patch("ansible_know.parser._find_ansible_doc", return_value="/usr/bin/ansible-doc"):
            with patch("ansible_know.collections.get_collections_path", return_value=None):
                with patch("subprocess.run", return_value=MagicMock(
                    returncode=0, stdout='{}', stderr='',
                )) as mock_run:
                    from ansible_know.parser import _run_ansible_doc
                    _run_ansible_doc("--list", "--json")
                    if "env" in mock_run.call_args[1]:
                        assert mock_run.call_args[1]["env"] is None or \
                            "ANSIBLE_COLLECTIONS_PATH" not in mock_run.call_args[1].get("env", {})
```

Add these imports at the top of `tests/test_parser.py`:

```python
from unittest.mock import patch, MagicMock
import os
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_parser.py::TestRunAnsibleDocEnvInjection -v`
Expected: FAIL — `_run_ansible_doc` doesn't inject env yet, and doesn't import `collections`

- [ ] **Step 3: Modify `_run_ansible_doc` in parser.py**

Replace the `_run_ansible_doc` function in `src/ansible_know/parser.py` (lines 34-56) with:

```python
def _run_ansible_doc(*args: str) -> str:
    """Execute ansible-doc with the given arguments and return stdout."""
    ansible_doc = _find_ansible_doc()
    cmd = [ansible_doc, *args]

    from ansible_know import collections
    collections_path = collections.get_collections_path()
    env = None
    if collections_path:
        env = os.environ.copy()
        existing = env.get("ANSIBLE_COLLECTIONS_PATH", "")
        env["ANSIBLE_COLLECTIONS_PATH"] = (
            f"{collections_path}{os.pathsep}{existing}" if existing else collections_path
        )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    except FileNotFoundError:
        raise AnsibleDocError(
            "ansible-doc not found. Install ansible-core: pip install ansible-core"
        )
    except subprocess.TimeoutExpired:
        raise AnsibleDocError(f"ansible-doc timed out: {' '.join(cmd)}")

    if result.returncode != 0:
        raise AnsibleDocError(
            f"ansible-doc failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout
```

Also add `import os` at the top of `parser.py` (after the existing imports).

- [ ] **Step 4: Run all parser tests**

Run: `pytest tests/test_parser.py -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add src/ansible_know/parser.py tests/test_parser.py
git commit -m "Inject ANSIBLE_COLLECTIONS_PATH in ansible-doc subprocess

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Server tool and error hints — expose ensure_collection, add missing-collection hints

**Files:**
- Modify: `src/ansible_know/server.py`
- Modify: `tests/test_server.py`

This task adds the `ensure_collection` tool to `server.py`, adds version validation, and injects error hints into `get_module_doc`, `search_modules`, `generate_skill`, and `generate_collection_skills` when a collection is not found.

- [ ] **Step 1: Write the server tests**

Add these tests to `tests/test_server.py`:

```python
class TestEnsureCollectionTool:
    @pytest.mark.asyncio
    async def test_installs_collection(self):
        galaxy_stdout = "Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
        mock_result = MagicMock()
        mock_result.stdout = galaxy_stdout
        mock_result.stderr = ""
        mock_result.returncode = 0
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=mock_result):
                import ansible_know.collections as col
                col._installed = {}
                col._tmp_dir = None
                from ansible_know.server import ensure_collection
                result = await ensure_collection("netbox.netbox")
        assert result["status"] == "installed"
        assert result["namespace"] == "netbox.netbox"

    @pytest.mark.asyncio
    async def test_invalid_namespace(self):
        from ansible_know.server import ensure_collection
        result = await ensure_collection("../etc")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_version(self):
        from ansible_know.server import ensure_collection
        result = await ensure_collection("netbox.netbox", version="; rm -rf /")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_valid_version_format(self):
        galaxy_stdout = "Installing 'netbox.netbox:3.9.0' to '<path>'\nnetbox.netbox:3.9.0 was installed successfully"
        mock_result = MagicMock()
        mock_result.stdout = galaxy_stdout
        mock_result.stderr = ""
        mock_result.returncode = 0
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=mock_result):
                import ansible_know.collections as col
                col._installed = {}
                col._tmp_dir = None
                from ansible_know.server import ensure_collection
                result = await ensure_collection("netbox.netbox", version="3.9.0")
        assert result["status"] == "installed"
        assert result["version"] == "3.9.0"


class TestMissingCollectionHints:
    @pytest.mark.asyncio
    async def test_get_module_doc_hint(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device has no attribute"
        )
        from ansible_know.server import get_module_doc
        result = await get_module_doc("netbox.netbox.netbox_device")
        assert "ensure_collection" in result["error"]
        assert "netbox.netbox" in result["error"]

    @pytest.mark.asyncio
    async def test_search_modules_hint(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError(
            "ansible-doc failed (exit 1): netbox.netbox was not found"
        )
        from ansible_know.server import search_modules
        result = await search_modules("device", namespace="netbox.netbox")
        assert "ensure_collection" in result["error"]

    @pytest.mark.asyncio
    async def test_generate_skill_hint(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError(
            "ansible-doc failed (exit 1): netbox.netbox.netbox_device could not be found"
        )
        from ansible_know.server import generate_skill
        result = await generate_skill("netbox.netbox.netbox_device")
        assert "ensure_collection" in result

    @pytest.mark.asyncio
    async def test_no_hint_for_unrelated_errors(self, mock_ansible_doc):
        from ansible_know.parser import AnsibleDocError
        mock_ansible_doc.side_effect = AnsibleDocError("Some unrelated error")
        from ansible_know.server import get_module_doc
        result = await get_module_doc("ansible.builtin.copy")
        assert "ensure_collection" not in result.get("error", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_server.py::TestEnsureCollectionTool tests/test_server.py::TestMissingCollectionHints -v`
Expected: FAIL — `ensure_collection` tool doesn't exist yet

- [ ] **Step 3: Add version validation and ensure_collection tool to server.py**

Add the version regex constant after the existing `_NAMESPACE_RE` line (around line 27):

```python
_VERSION_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
```

Add the version validation function after `_validate_keyword`:

```python
def _validate_version(version: str) -> None:
    if not version or not _VERSION_RE.match(version):
        raise ValidationError(
            f"Invalid version format: use alphanumeric characters, dots, dashes only."
        )
```

Add the `_MISSING_COLLECTION_PATTERNS` list before the tool definitions (around line 108):

```python
_MISSING_COLLECTION_PATTERNS = ("has no attribute", "was not found", "could not be found")
```

Add a helper to build the hint message:

```python
def _collection_hint(namespace: str) -> str:
    return (
        f" Collection '{namespace}' not found. Use ensure_collection('{namespace}') "
        f"to install it temporarily (latest version, or specify version='X.Y.Z')."
    )


def _maybe_add_hint(error_msg: str, namespace: str | None) -> str:
    if namespace and any(p in error_msg.lower() for p in _MISSING_COLLECTION_PATTERNS):
        return error_msg + _collection_hint(namespace)
    return error_msg
```

Add the `ensure_collection` tool after the discovery tools section (before skill management tools):

```python
@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
async def ensure_collection(
    collection_namespace: Annotated[str, "Collection namespace (e.g. 'netbox.netbox')"],
    version: Annotated[str | None, "Optional version pin (e.g. '4.1.0'). If omitted, installs latest."] = None,
) -> dict[str, Any]:
    """Install a collection to a temporary directory for this session.

    Returns dict with keys: namespace, version, status, message.
    - status: 'installed' (freshly installed) or 'already_installed' (version pin matched).
    - message: human-readable summary including the active version.
    """
    logger.info("ensure_collection namespace=%r version=%r", collection_namespace, version)
    try:
        _validate_namespace(collection_namespace)
        if version:
            _validate_version(version)
    except ValidationError as exc:
        return {"error": str(exc)}

    try:
        from ansible_know import collections

        result = await _run_in_executor(collections.ensure_collection, collection_namespace, version)
        logger.info(
            "ensure_collection result: namespace=%s version=%s status=%s",
            result["namespace"], result["version"], result["status"],
        )
        return result
    except Exception as exc:
        logger.warning("ensure_collection failed: %s", exc)
        return {"error": _sanitize_error(str(exc))}
```

- [ ] **Step 4: Add error hints to existing tools**

Modify `search_modules` — replace the generic except block (lines 134-136):

```python
    except Exception as exc:
        logger.warning("search_modules failed: %s", exc)
        return {"error": _maybe_add_hint(_sanitize_error(str(exc)), namespace)}
```

Modify `get_module_doc` — replace the generic except block (lines 161-162):

```python
    except Exception as exc:
        logger.warning("get_module_doc failed: %s", exc)
        ns = ".".join(module_name.split(".")[:2]) if "." in module_name else None
        return {"error": _maybe_add_hint(_sanitize_error(str(exc)), ns)}
```

Modify `generate_skill` — replace the generic except block (lines 342-344):

```python
    except Exception as exc:
        logger.warning("generate_skill failed: %s", exc)
        ns = ".".join(module_name.split(".")[:2]) if "." in module_name else None
        return _maybe_add_hint(_sanitize_error(str(exc)), ns)
```

Modify `get_collection_manifest` — replace the no-modules-found error (line 218):

```python
            return {"error": (
                f"No modules found in collection '{collection_namespace}'."
                + _collection_hint(collection_namespace)
            )}
```

And its generic except block (lines 232-233):

```python
    except Exception as exc:
        logger.warning("get_collection_manifest failed: %s", exc)
        return {"error": _maybe_add_hint(_sanitize_error(str(exc)), collection_namespace)}
```

Modify `generate_collection_skills` — replace the no-modules-found error (line 372):

```python
            return {"error": (
                f"No modules found in collection '{collection_namespace}'."
                + _collection_hint(collection_namespace)
            )}
```

And its generic except block (lines 413-414):

```python
    except Exception as exc:
        logger.warning("generate_collection_skills failed: %s", exc)
        return {"error": _maybe_add_hint(_sanitize_error(str(exc)), collection_namespace)}
```

- [ ] **Step 5: Run all server tests**

Run: `pytest tests/test_server.py -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 6: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS across all test files

- [ ] **Step 7: Commit**

```bash
git add src/ansible_know/server.py tests/test_server.py
git commit -m "Add ensure_collection tool and missing-collection error hints

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Integration verification and cleanup

**Files:**
- Review: all modified files
- Modify: `tests/test_collections.py` (if fixture isolation needs fixing)

This task runs the full test suite, checks for import issues, and verifies the end-to-end flow works correctly with all modules wired together.

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Check for circular import issues**

Run: `python -c "from ansible_know import server; print('Import OK')"`
Expected: `Import OK`

Run: `python -c "from ansible_know import collections; print('Import OK')"`
Expected: `Import OK`

Run: `python -c "from ansible_know import parser; print('Import OK')"`
Expected: `Import OK`

- [ ] **Step 3: Verify the tool shows up in MCP registration**

Run: `python -c "from ansible_know.server import mcp; tools = mcp._tool_manager._tools; print([t for t in tools]); assert 'ensure_collection' in tools, 'Tool not registered'"`
Expected: `ensure_collection` appears in the tool list

- [ ] **Step 4: Run type checks if available**

Run: `python -m py_compile src/ansible_know/collections.py && python -m py_compile src/ansible_know/parser.py && python -m py_compile src/ansible_know/server.py && echo "All files compile OK"`
Expected: `All files compile OK`

- [ ] **Step 5: Final commit (if any fixups were needed)**

Only if previous steps required changes:

```bash
git add -A
git commit -m "Fix integration issues from collection bootstrapping

Assisted-by: Claude Opus 4.6 <noreply@anthropic.com>"
```
