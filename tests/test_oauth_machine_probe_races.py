"""机器认证探测不得把迟到结果应用到已变更的来源配置。"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy

import pytest

from app.services import oauth_machine_probe
from app.services.async_compat import operation_timeout
from tests.test_oauth_machine_client import configuration
from tests.test_oauth_probe_manager import manager_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("change", ["credential", "url", "replace_config", "stop"])
async def test_machine_probe_discards_changed_source(
    monkeypatch: pytest.MonkeyPatch, failed: bool, change: str
) -> None:
    """同一实例内换凭证/地址/配置及停服后，成功和失败都不得写回。"""
    manager, config = manager_fixture()
    config.update(configuration())
    info = manager.server_info["fixture"]
    started, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def connection():
        started.set()
        await release.wait()
        if failed:
            raise RuntimeError("synthetic failure")
        yield

    captured = []

    def factory(snapshot):
        assert snapshot is not config and snapshot == config
        assert snapshot["oauth"] is not config["oauth"]
        captured.append(snapshot)
        return connection

    monkeypatch.setattr(oauth_machine_probe, "make_proxy_client_factory", factory)
    task = asyncio.create_task(manager._detect_auth_status("fixture", config))
    try:
        async with operation_timeout(3):
            await started.wait()
            if change == "credential":
                config["oauth"]["client_secret"] = "replacement-secret"
            elif change == "url":
                config["url"] = "https://replacement.fixture/mcp"
            elif change == "replace_config":
                info["config"] = deepcopy(config)
            else:
                info["status"] = "stopped"
            expected = deepcopy(info)
            release.set()
            await task
            assert info == expected
            assert captured[0]["oauth"]["client_secret"] == "synthetic-client-secret"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
