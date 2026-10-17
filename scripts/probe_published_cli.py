"""公共 npm CLI 1.0.1 与升级后实际 Registry 的隔离验收。"""

import asyncio
import json
import os
import sys
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

INTEGRITY = "sha512-JkRraLDigA65XAwjyi1NV3PsaK1H7VqYZDqxDJMMBhTF4CZhBJUBqaawAnQLjKhxDLE+mJd5uILH8kAYEWBMIw=="
KEY = "published-cli-private-fixture-key"
SLUG = "compat-probe"
DIRECTORIES = {
    "codex": ".agents/skills",
    "claude": ".claude/skills",
    "openclaw": ".openclaw/skills",
    "workbuddy": ".workbuddy/skills",
    "codebuddy": ".codebuddy/skills",
    "qoder": ".qoder/skills",
    "pi": ".agents/skills",
    "dsh": ".agents/skills",
    "cursor": ".agents/skills",
    "generic": "custom-skills",
}


def published_package(installation: Path) -> Path:
    """锁定公共包来源和完整性，不把本地 npm pack 充当历史发布版本。"""
    lock = json.loads((installation / "package-lock.json").read_text())
    entry = lock["packages"]["node_modules/@jeweis/mcpcat"]
    assert entry["version"] == "1.0.1" and entry["integrity"] == INTEGRITY
    assert (
        entry["resolved"]
        == "https://registry.npmjs.org/@jeweis/mcpcat/-/mcpcat-1.0.1.tgz"
    )
    return installation / "node_modules/@jeweis/mcpcat/dist/bin.js"


def package(version: str) -> bytes:
    """可信测试包；附带脚本仅供安装，绝不能执行。"""
    from app.services.skill_package_validator import build_deterministic_skill_zip

    return build_deterministic_skill_zip(
        {
            f"{SLUG}/SKILL.md": (
                f"---\nname: {SLUG}\ndescription: Review compatibility fixture tasks.\n---\n# Fixture\n"
            ).encode(),
            f"{SLUG}/VERSION": (version + "\n").encode(),
            f"{SLUG}/scripts/task.py": b"raise RuntimeError('must never execute')\n",
        }
    )


def publish(database: "Database", version: str, selection: str = "local") -> None:
    """新旧交付模式均通过正式服务发布，不直接修改 Registry 响应。"""
    from app.services.skill_profile_upload import upload_skill_profiles
    from app.services.skill_variant_publication import publish_content_version

    upload_skill_profiles(
        database, content=package(version), version=version, selection=selection
    )
    publish_content_version(database, slug=SLUG, version=version)


def prepare(directory: Path) -> tuple["FastAPI", "Database"]:
    """仅创建临时数据，不加载或执行用户 MCP 配置。"""
    from app.application import create_app
    from app.core.config import settings
    from app.services.config_service import ConfigService
    from app.storage.database import Database
    from app.storage.migrations import upgrade_schema

    settings.mcpcat_config_path = str(directory / "absent.json")
    settings.mcpcat_artifacts_path = str(directory / "artifacts")
    settings.mcpcat_backups_path = str(directory / "backups")
    settings.mcpcat_default_admin_key = KEY
    settings.mcpcat_default_read_key = "published-cli-fixture-read"
    database = Database(directory / "fixture.db")
    upgrade_schema(database)
    ConfigService.configure_database(database)
    ConfigService.save_config(
        {
            "mcpServers": {},
            "catalog": {"enabled": False},
            "security": {"api_keys": []},
            "code_mode": {"enabled": False},
            "skills_mcp": {"enabled": False},
            "apps": {"enabled": False},
        }
    )
    app = create_app(database=database)
    publish(database, "1.0.0")
    return app, database


class CliProbe:
    """真正运行公共包的 bin.js；所有 Profile/锁/安装路径位于临时 HOME。"""

    def __init__(self, node: str, executable: Path, root: str, directory: Path) -> None:
        self.node, self.executable, self.root, self.directory = (
            node,
            executable,
            root,
            directory,
        )
        self.count = 0

    async def run(self, home: Path, cwd: Path, *arguments: str) -> dict:
        """凭证只经环境注入，不写命令行、输出或系统 Keychain。"""
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("MCP_", "MCPCAT_", "NODE_"))
        }
        environment.update(
            {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / "xdg"),
                "MCPCAT_API_KEY": KEY,
                "NO_PROXY": "127.0.0.1,localhost",
                "PATH": str(Path(self.node).parent) + os.pathsep + os.defpath,
            }
        )
        process = await asyncio.create_subprocess_exec(
            self.node,
            str(self.executable),
            *arguments,
            "--allow-http",
            "--non-interactive",
            "--json",
            cwd=cwd,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), 30)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        assert KEY.encode() not in output + error
        assert process.returncode == 0, (arguments, output.decode(), error.decode())
        result = json.loads(output)
        assert result["ok"], result
        self.count += 1
        return result["data"]

    async def install(self, agent: str, scope: str) -> tuple[Path, Path, Path]:
        """相同 .agents 路径的不同 Agent 使用独立 HOME，避免互相覆盖掩盖问题。"""
        cell = self.directory / f"{agent}-{scope}"
        home, cwd = cell / "home", cell / "workspace"
        home.mkdir(parents=True)
        cwd.mkdir()
        target = (home if scope == "user" else cwd) / DIRECTORIES[agent]
        if agent == "openclaw" and scope == "project":
            target = cwd / "skills"
        extra = ("--target-dir", str(target)) if agent == "generic" else ()
        await self.run(home, cwd, "connect", "fixture", self.root)
        await self.run(home, cwd, "skills", "list")
        await self.run(home, cwd, "skills", "info", SLUG)
        await self.run(
            home,
            cwd,
            "skills",
            "install",
            SLUG,
            "--agent",
            agent,
            "--scope",
            scope,
            *extra,
        )
        assert (target / SLUG / "VERSION").read_text() == "1.0.0\n"
        return home, cwd, target / SLUG

    async def lifecycle(
        self, agent: str, scope: str, cell: tuple[Path, Path, Path]
    ) -> None:
        """pin 阻止升级，unpin 后只升级 local latest，再从本地备份回滚。"""
        home, cwd, target = cell
        filters = ("--agent", agent, "--scope", scope)
        await self.run(home, cwd, "skills", "pin", SLUG, *filters)
        await self.run(home, cwd, "skills", "update", SLUG, *filters)
        assert (target / "VERSION").read_text() == "1.0.0\n"
        await self.run(home, cwd, "skills", "unpin", SLUG, *filters)
        await self.run(home, cwd, "skills", "update", SLUG, *filters)
        assert (target / "VERSION").read_text() == "1.0.1\n"
        await self.run(home, cwd, "skills", "rollback", SLUG, *filters)
        assert (target / "VERSION").read_text() == "1.0.0\n"
        doctor = await self.run(home, cwd, "doctor")
        assert doctor["healthy"], doctor
        assert all(check["status"] != "error" for check in doctor["checks"])
        assert (
            next(check for check in doctor["checks"] if check["name"] == "mcporter")[
                "status"
            ]
            == "warn"
        )
        # mcporter 不在本验收中执行；缺失只接受 doctor 明确警告，不假称成功。
        for path in home.rglob("*.json"):
            assert KEY not in path.read_text()


async def check(node: str, executable: Path, directory: Path) -> None:
    """测试全部现有 Agent × 两种 Scope；不调用 Agent 或安装包脚本。"""
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
                probe = CliProbe(node, executable, root, directory)
                cells = {}
                for agent in DIRECTORIES:
                    for scope in ("user", "project"):
                        cells[agent, scope] = await probe.install(agent, scope)
                publish(database, "1.0.1")
                publish(database, "2.0.0", selection="remote")
                for (agent, scope), cell in cells.items():
                    await probe.lifecycle(agent, scope, cell)
                print(
                    json.dumps(
                        {
                            "package": "@jeweis/mcpcat@1.0.1",
                            "integrity": INTEGRITY,
                            "agents": list(DIRECTORIES),
                            "scopes": ["user", "project"],
                            "cells": len(cells),
                            "commands": probe.count,
                            "remote_only_ignored": True,
                            "result": "passed",
                        }
                    )
                )
    finally:
        ConfigService.reset_database()
        if database is not None:
            database.dispose()


async def main() -> None:
    """参数为 Node >=24 可执行文件与公共 npm 安装目录。"""
    node = str(Path(sys.argv[1]).resolve())
    executable = published_package(Path(sys.argv[2]).resolve())
    previous = os.getcwd()
    with TemporaryDirectory(prefix="mcpcat-published-cli-") as directory:
        os.chdir(directory)
        try:
            await check(node, executable, Path(directory))
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    asyncio.run(main())
