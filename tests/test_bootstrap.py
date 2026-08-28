"""应用 Bootstrap 顺序、失败阻断与重复创建测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.application import create_app
from app.core.config import settings
from app.services.config_service import ConfigService
from app.storage.database import Database


class FakeServerManager:
    def __init__(self) -> None:
        self.catalog_service = None
        self.loaded = False
        self.mounted = False

    def load_servers_from_config(self) -> None:
        assert ConfigService.load_raw_config()["app"]["version"] == "0.1.1"
        self.loaded = True

    def mount_all_servers(self, _app) -> None:
        assert self.loaded is True
        self.mounted = True

    @asynccontextmanager
    async def create_unified_lifespan(self, _app):
        yield


@pytest.fixture(autouse=True)
def storage_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "mcpcat_config_path", str(tmp_path / "config.json"))
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "mcpcat_skills_enabled", True)
    yield
    ConfigService.reset_database()


@pytest.mark.asyncio
async def test_create_app_bootstraps_storage_before_business_services(
    tmp_path: Path,
) -> None:
    manager = FakeServerManager()
    database = Database(tmp_path / "application.db")

    application = create_app(database=database, server_manager=manager)

    assert manager.loaded is True
    assert manager.mounted is True
    assert application.state.storage_ready is True
    assert application.state.schema_status.current == "0002_skills_registry"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"message": "OK", "ready": True}
    database.dispose()


def test_storage_failure_prevents_business_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import application as application_module

    manager = FakeServerManager()

    def fail_storage(_database):
        raise RuntimeError("schema upgrade failed")

    monkeypatch.setattr(application_module, "bootstrap_storage", fail_storage)
    with pytest.raises(RuntimeError, match="schema upgrade failed"):
        create_app(server_manager=manager)
    assert manager.loaded is False
    assert manager.mounted is False


@pytest.mark.asyncio
async def test_repeated_app_creation_has_isolated_routes_and_state(
    tmp_path: Path,
) -> None:
    first_manager = FakeServerManager()
    second_manager = FakeServerManager()
    first = create_app(
        database=Database(tmp_path / "first.db"), server_manager=first_manager
    )
    second = create_app(
        database=Database(tmp_path / "second.db"), server_manager=second_manager
    )

    for application in (first, second):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/health")
        assert response.json()["ready"] is True
    assert first.state.database.path != second.state.database.path
    first.state.database.dispose()
    second.state.database.dispose()


@pytest.mark.asyncio
async def test_disabling_skills_router_keeps_storage_and_health_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mcpcat_skills_enabled", False)
    database = Database(tmp_path / "skills-disabled.db")
    application = create_app(database=database, server_manager=FakeServerManager())
    config = ConfigService.load_raw_config()
    api_key = config["security"]["api_keys"][0]["key"]
    auth_header = config["security"].get("auth_header_name", "Mcpcat-Key")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        health = await client.get("/api/health")
        skills = await client.get("/api/skills", headers={auth_header: api_key})
    assert health.json()["ready"] is True
    assert skills.status_code == 404
    assert application.state.schema_status.current == "0002_skills_registry"
    database.dispose()
