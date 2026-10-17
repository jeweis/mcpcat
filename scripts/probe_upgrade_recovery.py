"""完整隔离恢复演练：匹配旧程序/快照、保留升级后写入和旧 CLI 生命周期。"""

# flake8: noqa: E402

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import AsyncIterator
from unittest.mock import patch

import httpx
from fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from legacy_v101_fixture import make_old_fixture, snapshot

from app.core.config import settings
from app.services.config_service import ConfigService
from app.storage.database import Database
from app.storage.maintenance import backup_database, check_integrity
from app.storage.migrations import get_schema_status, upgrade_schema
from scripts.probe_fastmcp_network import serve, serve_app
from scripts.probe_published_cli import (
    KEY,
    SLUG,
    CliProbe,
    package,
    publish,
    published_package,
)


def configure(data: Path) -> None:
    """所有可写路径均收敛到一次性目录，不读取用户配置。"""
    settings.mcpcat_config_path = str(data / "absent.json")
    settings.mcpcat_artifacts_path = str(data / "artifacts")
    settings.mcpcat_apps_artifacts_path = str(data / "apps-artifacts")
    settings.mcpcat_backups_path = str(data / "backups")


def freeze(database: Database, data: Path, target: Path) -> dict:
    """停服务后用 SQLite backup API 与匹配制品/配置保存完整快照。"""
    target.mkdir()
    path = backup_database(database.path, target, label="matched-snapshot")
    assert path is not None
    path.rename(target / "fixture.db")
    shutil.copytree(data / "artifacts", target / "artifacts")
    if (data / "apps-artifacts").exists():
        shutil.copytree(data / "apps-artifacts", target / "apps-artifacts")
    config = ConfigService.load_raw_config()
    output = target / "config.json"
    output.write_text(json.dumps(config), encoding="utf-8")
    output.chmod(0o600)
    return {
        item.relative_to(target)
        .as_posix(): hashlib.sha256(item.read_bytes())
        .hexdigest()
        for item in target.rglob("*")
        if item.is_file()
    }


@asynccontextmanager
async def old_host(python: str, source: Path, data: Path) -> AsyncIterator[dict]:
    """只启动自己创建的旧版子进程，退出必须等待资源清理。"""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MCPCAT_", "MCP_", "PYTHON"))
    }
    environment.update(
        {"HOME": str(data), "PYTHONNOUSERSITE": "1", "NO_PROXY": "127.0.0.1,localhost"}
    )
    with (data / "old-host.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            python,
            str(ROOT / "scripts/fixtures/recovery_legacy_host.py"),
            str(source),
            str(data),
            cwd=source,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=log,
        )
        try:
            async with asyncio.timeout(15):
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        raise RuntimeError((data / "old-host.log").read_text()[-2500:])
                    if line.startswith(b"MCPCAT_RECOVERY_READY "):
                        ready = json.loads(line.split(b" ", 1)[1])
                        break
            yield ready
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()


async def upload_local(root: str, version: str) -> None:
    """旧程序通过正式管理 API 上传/发布，不伪造 Registry 响应。"""
    async with httpx.AsyncClient(base_url=root, headers={"Mcpcat-Key": KEY}) as client:
        response = await client.post(
            "/api/skills/upload",
            files={"file": ("fixture.zip", package(version), "application/zip")},
            data={"version": version},
        )
        assert response.status_code == 200, response.status_code
        response = await client.post(f"/api/skills/{SLUG}/versions/{version}/publish")
        assert response.status_code == 200, response.status_code


def prepare(data: Path, upstream: str) -> Database:
    """原 DDL/旧状态/未知字段/合成凭证保留，外部服务明确禁用。"""
    configure(data)
    database, *_ = make_old_fixture(data)
    preserved = backup_database(database.path, data / "backups", label="fixture-seed")
    assert preserved is not None
    database.dispose()
    preserved.rename(data / "fixture.db")
    database = Database(data / "fixture.db")
    ConfigService.configure_database(database)
    config = ConfigService.load_raw_config()
    for item in config["mcpServers"].values():
        item["enabled"] = False
    config["mcpServers"]["recovery-source"] = {
        "type": "streamable-http",
        "url": upstream,
        "require_auth": True,
    }
    config["catalog"] = {"enabled": False}
    config["security"]["auth_header_name"] = "Mcpcat-Key"
    config["security"]["api_keys"][0]["key"] = KEY
    assert ConfigService.save_config(config)
    return database


async def upgrade_and_stop(database: Database, data: Path) -> None:
    """制造升级后真实写入，关闭新增远程入口并停止业务以形成写入边界。"""
    from app.application import create_app
    from app.services.app_registry_service import upload_app
    from app.services.inspector_service import InspectorService
    from app.services.market_service import MarketService

    upgrade_schema(database)
    assert get_schema_status(database).current == "0005_app_render_review"
    publish(database, "1.0.1")
    publish(database, "2.0.0", selection="remote")
    upload_app(
        database,
        slug="recovery-app",
        display_name="Recovery fixture",
        version="1.0.0",
        content=b"<html>Preserve upgraded data</html>",
        filename="index.html",
    )
    for section in ("skills_mcp", "code_mode", "apps"):
        assert ConfigService.update_setting_section(section, {"enabled": True})
    assert ConfigService.update_setting_section(
        "code_mode",
        {"enabled": True, "limits": {"max_tool_calls": 3}, "unknown": {"keep": True}},
    )
    with patch.object(MarketService, "refresh_async", lambda self: None), patch.object(
        InspectorService, "start_cleanup_task", lambda self: None
    ):
        app = create_app(database=database)
        async with serve_app(app) as root:
            async with httpx.AsyncClient(
                base_url=root, headers={"Mcpcat-Key": KEY}
            ) as client:
                response = await client.get("/api/skills/registry")
                assert response.status_code == 200
                entry = next(
                    item for item in response.json()["skills"] if item["slug"] == SLUG
                )
                assert entry["latest_published_version"] == "1.0.1"
    # 必须实际退出生命周期停机，废弃开关不再承担恢复安全边界。
    assert not app.state.skills_mcp_runtime.alive
    assert not app.state.apps_mcp_runtime.alive
    assert not app.state.code_mode_runtime.alive
    configure(data)
    ConfigService.configure_database(database)


async def restored_cli(node: str, executable: Path, root: str, data: Path) -> int:
    """旧程序恢复后，公共 CLI 使用独立 HOME 真正执行固定/升级/回滚。"""
    probe = CliProbe(node, executable, root, data)
    cell = await probe.install("codex", "project")
    await upload_local(root, "1.0.1")
    await probe.lifecycle("codex", "project", cell)
    return probe.count


async def restore_and_verify(
    old_python: str,
    source: Path,
    node: str,
    executable: Path,
    data: Path,
    baseline: dict,
    before: dict,
) -> tuple[dict, int]:
    """旧副本启动前核对所有旧列、配置；CLI 写入后旧制品仍不变。"""
    restored = data / "restored"
    shutil.copytree(data / "before", restored)
    configure(restored)
    recovered = Database(restored / "fixture.db")
    try:
        assert get_schema_status(recovered).current == "0002_skills_registry"
        assert snapshot(recovered) == baseline
        ConfigService.configure_database(recovered)
        config = ConfigService.load_raw_config()
        assert config == json.loads((restored / "config.json").read_text())
        assert "code_mode" not in config
        async with old_host(old_python, source, restored) as report:
            assert snapshot(recovered) == baseline
            commands = await restored_cli(node, executable, report["url"], data)
        for name, digest in before.items():
            if name.startswith("artifacts/"):
                assert (
                    hashlib.sha256((restored / name).read_bytes()).hexdigest() == digest
                )
        assert check_integrity(recovered) == "ok"
        return report, commands
    finally:
        recovered.dispose()


def verify_preserved_upgrade(data: Path, hashes: dict) -> None:
    """旧程序恢复不能覆盖升级后版本、Apps、CodeMode 设置和新制品。"""
    after = Database(data / "after/fixture.db")
    try:
        assert get_schema_status(after).current == "0005_app_render_review"
        with sqlite3.connect(after.path) as connection:
            versions = connection.execute(
                "SELECT version FROM skill_versions WHERE skill_id = (SELECT id FROM skills WHERE slug=?) ORDER BY version",
                (SLUG,),
            ).fetchall()
            assert versions == [("1.0.0",), ("1.0.1",), ("2.0.0",)]
            assert connection.execute("SELECT slug FROM apps").fetchall() == [
                ("recovery-app",)
            ]
            settings_row = connection.execute(
                "SELECT value_json FROM system_settings WHERE section='code_mode'"
            ).fetchone()
            assert json.loads(settings_row[0]) == {
                "enabled": True,
                "limits": {"max_tool_calls": 3},
                "unknown": {"keep": True},
            }
        assert check_integrity(after) == "ok"
    finally:
        after.dispose()
    assert all(
        hashlib.sha256((data / "after" / name).read_bytes()).hexdigest() == digest
        for name, digest in hashes.items()
    )


async def check(
    old_python: str, source: Path, node: str, installation: Path, data: Path
) -> dict:
    """完整串联旧程序、升级写入、双快照保存和旧程序恢复。"""
    executable = published_package(installation)
    upstream = FastMCP("Trusted recovery fixture")

    @upstream.tool()
    def echo(value: str) -> str:
        """仅返回合成输入；不读文件、执行上传脚本或调用外网。"""
        return value

    async with serve(upstream, "http") as address:
        live = data / "live"
        live.mkdir()
        database = prepare(live, address)
        async with old_host(old_python, source, live) as initial:
            await upload_local(initial["url"], "1.0.0")
        baseline = snapshot(database)
        pre = freeze(database, live, data / "before")
        await upgrade_and_stop(database, live)
        post = freeze(database, live, data / "after")
        assert check_integrity(database) == "ok"
        database.dispose()
        report, commands = await restore_and_verify(
            old_python, source, node, executable, data, baseline, pre
        )
        verify_preserved_upgrade(data, post)
        return {
            "result": "passed",
            "old_program": "v1.0.1",
            "old_fastmcp": report["fastmcp"],
            "old_sdk": report["mcp"],
            "mcp_http_sse": report["http_sse"],
            "published_cli": "1.0.1",
            "cli_commands": commands,
            "old_tables_equal": True,
            "post_upgrade_preserved": True,
            "new_instance_stopped": True,
            "snapshot_files": {"before": len(pre), "after": len(post)},
        }


async def main() -> None:
    """参数为旧 Python、可信 v1.0.1 导出目录、Node 和公共 CLI 安装目录。"""
    # 保留 venv/bin/python 的符号链接路径，resolve 会绕过虚拟环境。
    old_python = Path(sys.argv[1]).absolute()
    source, node, installation = map(Path, sys.argv[2:5])
    source, node, installation = (
        source.resolve(),
        node.resolve(),
        installation.resolve(),
    )
    with TemporaryDirectory(prefix="mcpcat-recovery-drill-") as temporary:
        try:
            report = await check(
                str(old_python), source, str(node), installation, Path(temporary)
            )
            print(json.dumps(report))
        finally:
            ConfigService.reset_database()


if __name__ == "__main__":
    asyncio.run(main())
