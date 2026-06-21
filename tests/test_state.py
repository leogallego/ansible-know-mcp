"""Tests for ansible_know.state."""

from unittest.mock import MagicMock

import pytest

from ansible_know.collections import CollectionManager
from ansible_know.state import LifespanContext, ServerState, SessionManager, SharedState


class TestServerState:
    def test_create_with_required_fields(self):
        mgr = CollectionManager()
        state = ServerState(collection_manager=mgr)
        assert state.collection_manager is mgr
        assert state.missing_collections == set()
        assert state.version_info is None
        assert state.galaxy_servers == []
        assert state.upgrade_warned is False

    def test_clear_missing_namespace(self):
        mgr = CollectionManager()
        state = ServerState(collection_manager=mgr)
        state.missing_collections.add("netbox.netbox")
        state.clear_missing_namespace("netbox.netbox")
        assert "netbox.netbox" not in state.missing_collections

    def test_clear_missing_namespace_absent_is_noop(self):
        mgr = CollectionManager()
        state = ServerState(collection_manager=mgr)
        state.clear_missing_namespace("nonexistent.ns")
        assert state.missing_collections == set()

    def test_independent_instances(self):
        mgr1 = CollectionManager()
        mgr2 = CollectionManager()
        state1 = ServerState(collection_manager=mgr1)
        state2 = ServerState(collection_manager=mgr2)
        state1.missing_collections.add("netbox.netbox")
        assert "netbox.netbox" not in state2.missing_collections


class TestLifespanContext:
    def test_is_typed_dict(self):
        assert "http_client" in LifespanContext.__annotations__
        assert "shared" in LifespanContext.__annotations__
        assert "sessions" in LifespanContext.__annotations__

    def test_required_keys(self):
        assert "http_client" in LifespanContext.__required_keys__
        assert "shared" in LifespanContext.__required_keys__
        assert "sessions" in LifespanContext.__required_keys__


class TestSharedState:
    def test_default_fields(self):
        shared = SharedState()
        assert shared.galaxy_servers == []
        assert shared.version_info is None

    def test_with_values(self):
        servers = [MagicMock()]
        info = {"installed": "0.4.0", "latest": "0.5.0", "outdated": True}
        shared = SharedState(galaxy_servers=servers, version_info=info)
        assert shared.galaxy_servers is servers
        assert shared.version_info is info


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_get_or_create_returns_same_for_same_id(self):
        mgr = SessionManager(SharedState(), collection_factory=CollectionManager)
        state1 = await mgr.get_or_create("session-1")
        state2 = await mgr.get_or_create("session-1")
        assert state1 is state2

    @pytest.mark.asyncio
    async def test_get_or_create_returns_different_for_different_ids(self):
        mgr = SessionManager(SharedState(), collection_factory=CollectionManager)
        state_a = await mgr.get_or_create("session-a")
        state_b = await mgr.get_or_create("session-b")
        assert state_a is not state_b

    @pytest.mark.asyncio
    async def test_session_state_has_shared_galaxy_servers(self):
        servers = [MagicMock()]
        shared = SharedState(galaxy_servers=servers)
        mgr = SessionManager(shared, collection_factory=CollectionManager)
        state = await mgr.get_or_create("session-1")
        assert state.galaxy_servers is servers

    @pytest.mark.asyncio
    async def test_on_version_update_propagates_to_sessions(self):
        shared = SharedState()
        mgr = SessionManager(shared, collection_factory=CollectionManager)
        state_a = await mgr.get_or_create("a")
        state_b = await mgr.get_or_create("b")

        new_info = {"installed": "0.4.0", "latest": "0.4.0", "outdated": False}
        await mgr.on_version_update(new_info)

        assert shared.version_info is new_info
        assert state_a.version_info is new_info
        assert state_b.version_info is new_info

    @pytest.mark.asyncio
    async def test_on_version_update_resets_upgrade_warned(self):
        shared = SharedState()
        mgr = SessionManager(shared, collection_factory=CollectionManager)
        state = await mgr.get_or_create("s1")
        state.upgrade_warned = True

        outdated_info = {"installed": "0.3.0", "latest": "0.4.0", "outdated": True}
        await mgr.on_version_update(outdated_info)

        assert state.upgrade_warned is False

    @pytest.mark.asyncio
    async def test_on_version_update_no_reset_when_not_outdated(self):
        shared = SharedState()
        mgr = SessionManager(shared, collection_factory=CollectionManager)
        state = await mgr.get_or_create("s1")
        state.upgrade_warned = True

        up_to_date = {"installed": "0.4.0", "latest": "0.4.0", "outdated": False}
        await mgr.on_version_update(up_to_date)

        assert state.upgrade_warned is True

    def test_all_installed_collections_empty(self):
        mgr = SessionManager(SharedState(), collection_factory=CollectionManager)
        assert mgr.all_installed_collections == {}

    @pytest.mark.asyncio
    async def test_all_installed_collections_union(self):
        shared = SharedState()
        mgr = SessionManager(shared, collection_factory=CollectionManager)
        state_a = await mgr.get_or_create("a")
        state_b = await mgr.get_or_create("b")

        state_a.collection_manager.list_installed = MagicMock(
            return_value={"community.general": "9.0.0"},
        )
        state_b.collection_manager.list_installed = MagicMock(
            return_value={"ansible.posix": "1.6.0", "community.general": "9.1.0"},
        )

        result = mgr.all_installed_collections
        assert "community.general" in result
        assert "ansible.posix" in result
        assert result["ansible.posix"] == "1.6.0"

    @pytest.mark.asyncio
    async def test_remove_session_calls_cleanup(self):
        shared = SharedState()
        mgr = SessionManager(shared, collection_factory=CollectionManager)
        state = await mgr.get_or_create("s1")
        state.collection_manager.cleanup = MagicMock()

        await mgr.remove_session("s1")

        state.collection_manager.cleanup.assert_called_once()
        # Second get_or_create should produce a new instance
        state2 = await mgr.get_or_create("s1")
        assert state2 is not state

    @pytest.mark.asyncio
    async def test_remove_session_nonexistent_is_noop(self):
        mgr = SessionManager(SharedState(), collection_factory=CollectionManager)
        await mgr.remove_session("does-not-exist")

    @pytest.mark.asyncio
    async def test_on_version_update_with_none(self):
        shared = SharedState(version_info={"installed": "0.3.0", "latest": "0.4.0", "outdated": True})
        mgr = SessionManager(shared, collection_factory=CollectionManager)
        state = await mgr.get_or_create("s1")
        state.upgrade_warned = True

        await mgr.on_version_update(None)

        assert shared.version_info is None
        assert state.version_info is None
        assert state.upgrade_warned is True
