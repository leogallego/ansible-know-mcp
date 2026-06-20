"""Tests for ansible_know.state."""

from ansible_know.collections import CollectionManager
from ansible_know.state import LifespanContext, ServerState


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
        assert "state" in LifespanContext.__annotations__
