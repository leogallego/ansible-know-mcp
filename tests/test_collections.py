"""Tests for ansible_know.collections."""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from ansible_know.collections import (
    ensure_collection,
    get_collections_path,
    list_installed,
)
from ansible_know.errors import CollectionInstallError


@pytest.fixture(autouse=True)
def reset_collections_state():
    """Reset module-level state between tests."""
    import ansible_know.collections as col
    col._installed = {}
    col._install_locks = {}
    old_tmp = col._tmp_dir
    col._tmp_dir = None
    yield
    test_tmp = col._tmp_dir
    col._tmp_dir = None
    for tmp in [old_tmp, test_tmp]:
        if tmp is not None:
            try:
                tmp.cleanup()
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

    def test_no_version_skips_after_first_install(self):
        galaxy_stdout = "Installing 'netbox.netbox:4.0.0' to '<path>'\nnetbox.netbox:4.0.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout)):
                ensure_collection("netbox.netbox")
            with patch("subprocess.run") as mock_run:
                result = ensure_collection("netbox.netbox")
        assert result["status"] == "already_installed"
        assert result["version"] == "4.0.0"
        mock_run.assert_not_called()

    def test_different_version_replaces(self):
        galaxy_stdout_1 = "Installing 'netbox.netbox:3.9.0' to '<path>'\nnetbox.netbox:3.9.0 was installed successfully"
        galaxy_stdout_2 = "Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout_1)):
                ensure_collection("netbox.netbox", version="3.9.0")
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout_2)):
                result = ensure_collection("netbox.netbox", version="4.1.0")
        assert result["status"] == "installed"
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

    def test_fallback_to_manifest(self):
        galaxy_stdout = "Some unexpected output format"
        manifest_data = {"collection_info": {"version": "3.5.0"}}
        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", return_value=_make_subprocess_result(stdout=galaxy_stdout)):
                import tempfile

                import ansible_know.collections as col
                col._tmp_dir = tempfile.TemporaryDirectory()
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
    def test_same_collection_installs_once(self):
        call_count = 0
        def slow_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_subprocess_result(
                stdout="Installing 'netbox.netbox:4.1.0' to '<path>'\nnetbox.netbox:4.1.0 was installed successfully"
            )

        with patch("ansible_know.collections._find_ansible_galaxy", return_value="/usr/bin/ansible-galaxy"):
            with patch("subprocess.run", side_effect=slow_run):
                t1 = threading.Thread(target=ensure_collection, args=("netbox.netbox",))
                t2 = threading.Thread(target=ensure_collection, args=("netbox.netbox",))
                t1.start()
                t2.start()
                t1.join()
                t2.join()
        assert call_count == 1  # second thread sees first's result and skips

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
