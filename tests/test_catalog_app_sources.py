"""来源读取不创建身份，历史快照不能绕过当前授权/运行代际。"""

from types import SimpleNamespace

import pytest
from test_skill_content_workflow import content_app as _content_app

from app.services.catalog_app_sources import CatalogAppSources
from app.storage.runtime_identities import (
    current_endpoint_identity,
    endpoint_identity,
    retire_service_identity,
)

content_app = _content_app


@pytest.fixture
def sources(content_app):
    _, database = content_app
    config = {"expose_in_catalog": True, "require_auth": True}
    state = {"available": True, "authorized": True}
    manager = SimpleNamespace(
        server_info={"same": {"status": "running", "mcp": object()}}
    )
    registry = CatalogAppSources(
        database,
        manager,
        available=lambda: state["available"],
        authorize=lambda config: state["authorized"],
        config_loader=lambda name: config if name == "same" else None,
    )
    return registry, config, state, database


def test_lookup_does_not_register_unknown_or_retired_source(sources):
    registry, _, _, database = sources
    assert registry.source("same") is None
    assert current_endpoint_identity(database, "service", "same") is None
    endpoint_identity(database, "service", "same")
    snapshot = registry.source("same")
    assert snapshot and registry.current(snapshot)
    with database.session_scope() as session:
        retire_service_identity(session, "same")
    assert registry.source("same") is None
    assert not registry.current(snapshot)
    assert current_endpoint_identity(database, "service", "same") is None


@pytest.mark.parametrize(
    "change",
    [
        "disabled",
        "hidden",
        "oauth",
        "unauthorized",
        "unavailable",
        "stopped",
        "component",
    ],
)
def test_permission_and_lifecycle_changes_reject_old_snapshot(sources, change):
    registry, config, state, database = sources
    endpoint_identity(database, "service", "same")
    snapshot = registry.source("same")
    if change == "disabled":
        config["enabled"] = False
    elif change == "hidden":
        config["expose_in_catalog"] = False
    elif change == "oauth":
        config["oauth"] = {"enabled": True}
    elif change == "unauthorized":
        state["authorized"] = False
    elif change == "unavailable":
        state["available"] = False
    elif change == "stopped":
        registry.manager.server_info["same"]["status"] = "stopped"
    else:
        registry.manager.server_info["same"]["gateway_component"] = "apps"
    assert not registry.current(snapshot)
    assert registry.sources() == []


def test_restart_and_same_name_recreation_never_reuse_old_route(sources):
    registry, _, _, database = sources
    original_id = endpoint_identity(database, "service", "same")
    old = registry.source("same")
    assert registry.source("same") is old
    registry.manager.server_info["same"]["mcp"] = object()
    restarted = registry.source("same")
    assert not registry.current(old)
    assert restarted.identity.source == original_id
    assert restarted.identity.generation != old.identity.generation
    with database.session_scope() as session:
        retire_service_identity(session, "same")
    new_id = endpoint_identity(database, "service", "same")
    recreated = registry.source("same")
    assert new_id != original_id
    assert not registry.current(restarted)
    assert recreated.identity.source == new_id
    del registry.manager.server_info["same"]
    assert registry.sources() == [] and not registry._generations
