"""在临时数据与回环端口运行固定官方 Skills 场景，不触及用户实例。"""

import asyncio
import json
import os
import sys
from importlib.metadata import version as package_version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.storage.database import Database

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.probe_fastmcp_network import serve_app

PINS = json.loads((ROOT / "scripts/fixtures/skills-conformance.json").read_text())


async def command(*arguments: str, cwd: Path, home: Path | None = None) -> str:
    """测试子进程有界运行，取消/超时不遗留进程。"""
    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=cwd,
        env=fixture_environment(home) if home else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(stdout.decode() + stderr.decode())
    return stdout.decode()


def fixture_environment(home: Path) -> dict[str, str]:
    """隔离文件 Profile 不足以隔离 macOS Keychain，明确选择内存后端。"""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MCP_", "MCPCAT_"))
    }
    return {
        **environment,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "MCP_INSPECTOR_SECRET_STORE": "memory",
        "MCP_OAUTH_STATE_PATH": str(home / "oauth.json"),
        "NO_PROXY": "127.0.0.1,localhost",
    }


def prepare(directory: Path) -> tuple["FastAPI", "Database"]:
    """所有应用导入均发生在临时 cwd 后；只创建合成公开测试 Skills。"""
    from app.application import create_app
    from app.core.config import settings
    from app.services.config_service import ConfigService
    from app.services.skill_package_validator import build_deterministic_skill_zip
    from app.services.skill_profile_upload import upload_skill_profiles
    from app.services.skill_variant_publication import publish_content_version
    from app.storage.database import Database
    from app.storage.migrations import upgrade_schema

    settings.mcpcat_artifacts_path = str(directory / "artifacts")
    settings.mcpcat_backups_path = str(directory / "backups")
    settings.mcpcat_config_path = str(directory / "absent.json")
    database = Database(directory / "fixture.db")
    upgrade_schema(database)
    ConfigService.configure_database(database)
    ConfigService.save_config(
        {
            "mcpServers": {},
            "catalog": {"enabled": False},
            "security": {"api_keys": []},
            "skills_mcp": {"enabled": True, "require_auth": False, "page_size": 1},
        }
    )
    for slug in ("alpha", "beta", "gamma"):
        package = build_deterministic_skill_zip(
            {
                f"{slug}/SKILL.md": f"---\nname: {slug}\ndescription: Review fixture tasks.\nlicense: MIT\nmetadata:\n  owner: fixture\n---\n# 可信夹具\n".encode(),
                f"{slug}/references/guide.md": "# 参考\r\n".encode(),
                f"{slug}/assets/large.bin": b"\x00\xff\x80" * 200_000,
                f"{slug}/scripts/task.py": b"raise RuntimeError('must never run')\n",
            }
        )
        upload_skill_profiles(
            database, content=package, version="1.0.0", selection="both"
        )
        publish_content_version(
            database, slug=slug, version="1.0.0", remote_public=True
        )
    return create_app(database=database), database


def inspector_arguments(package: Path, root: str, directory: Path) -> tuple[str, ...]:
    """使用只读 mcp.json 的公开 protocolEra/metadata 配置，不改客户端代码。"""
    config = directory / "inspector.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fixture": {
                        "type": "streamable-http",
                        "url": root + "/mcp/skills/",
                        "protocolEra": "modern",
                        "metadata": {
                            "io.modelcontextprotocol/clientCapabilities": {
                                "extensions": {"io.modelcontextprotocol/skills": {}}
                            }
                        },
                    }
                }
            }
        )
    )
    return (
        "node",
        str(package / "clients/cli/build/index.js"),
        "--config",
        str(config),
        "--server",
        "fixture",
        "--stored-auth-only",
        "--client-config",
        str(directory / "client.json"),
        "--verify",
    )


async def check_inspector(installation: Path, root: str, directory: Path) -> None:
    """固定已发布 CLI；HOME/catalog/client config 都在夹具目录。"""
    package = installation / "node_modules/@modelcontextprotocol/inspector"
    version = json.loads((package / "package.json").read_text())["version"]
    assert version == PINS["inspector"]["version"]
    lock = json.loads((installation / "package-lock.json").read_text())
    assert (
        lock["packages"]["node_modules/@modelcontextprotocol/inspector"]["integrity"]
        == PINS["inspector"]["integrity"]
    )
    arguments = inspector_arguments(package, root, directory)
    output = await command(
        *arguments, "--method", "skills/list", cwd=directory, home=directory
    )
    reports = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert len(reports) == 3 and all(
        report["outcome"] == "verified" for report in reports
    ), output
    assert all(len(report["files"]) == 4 for report in reports)
    for report in reports:
        output = await command(
            *arguments,
            "--method",
            "skills/get",
            "--uri",
            report["uri"],
            cwd=directory,
            home=directory,
        )
        assert json.loads(output)["outcome"] == "verified", output
    print(
        json.dumps(
            {
                "client": "@modelcontextprotocol/inspector@" + version,
                "listed_verified": len(reports),
                "direct_get_verified": len(reports),
            }
        )
    )


async def check(source: Path, directory: Path, inspector: Path | None) -> None:
    """真正应用/认证/扩展挂载；只关闭无关的市场后台网络与定时清理。"""
    assert (await command("git", "rev-parse", "HEAD", cwd=source)).strip() == PINS[
        "conformance"
    ]["commit"]
    assert not await command("git", "diff", "--exit-code", cwd=source)
    assert (
        json.loads((source / "package.json").read_text())["version"]
        == PINS["conformance"]["version"]
    )
    assert package_version("fastmcp") == PINS["fastmcp"]
    from app.services.config_service import ConfigService
    from app.services.inspector_service import InspectorService
    from app.services.market_service import MarketService

    database = None
    try:
        with patch.object(
            MarketService, "refresh_async", lambda self: None
        ), patch.object(InspectorService, "start_cleanup_task", lambda self: None):
            app, database = prepare(directory)
            async with serve_app(app) as root:
                reports = json.loads(
                    await command(
                        "node",
                        "--import",
                        str(source / "node_modules/tsx/dist/loader.mjs"),
                        str(ROOT / "scripts/run_skills_conformance.mjs"),
                        str(source),
                        root + "/mcp/skills/",
                        cwd=source,
                    )
                )
                summarize(reports)
                if inspector is not None:
                    await check_inspector(inspector, root, directory)
    finally:
        ConfigService.reset_database()
        if database is not None:
            database.dispose()


def summarize(reports: list[dict]) -> None:
    """未实现的可选 directory 场景只能报告跳过，不能冒充通过。"""
    for report in reports:
        counts = {
            status: sum(check["status"] == status for check in report["checks"])
            for status in ("SUCCESS", "FAILURE", "WARNING", "SKIPPED")
        }
        print(
            json.dumps(
                {"scenario": report["name"], "counts": counts}, ensure_ascii=False
            )
        )
        for item in report["checks"]:
            if item["status"] in {"FAILURE", "WARNING"}:
                print(json.dumps(item, ensure_ascii=False))
        assert not counts["FAILURE"]
        if "directory" not in report["name"]:
            assert not counts["SKIPPED"] and not counts["WARNING"]


async def main() -> None:
    """参数为已安装依赖的固定官方测试源码目录。"""
    source = Path(sys.argv[1]).resolve()
    inspector = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
    previous = os.getcwd()
    with TemporaryDirectory(prefix="mcpcat-official-skills-") as directory:
        os.chdir(directory)
        try:
            await check(source, Path(directory), inspector)
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    asyncio.run(main())
