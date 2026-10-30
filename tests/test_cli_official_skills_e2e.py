"""按官方 Skills 工作流验证真实 CLI、HTTP、存储与 MCP 生命周期。

子进程运行正式 runCli，只注入临时存储路径，不替换请求、认证或业务处理。
不使用系统 Keychain，不读取或覆盖用户 CLI 配置，不连接真实业务实例。
"""

import asyncio
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastmcp import FastMCP

from app.application import create_app
from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.market_service import MarketService
from app.services.security_service import security_service
from app.storage.database import Database
from app.storage.migrations import upgrade_schema
from scripts.probe_fastmcp_network import serve, serve_app

ROOT = Path(__file__).resolve().parents[1]
READ = "official-skills-fixture-read"
WRITE = "official-skills-fixture-write"
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (ROOT / "cli/dist/cli.js").is_file(),
    reason="需要 Node.js 与 cli 构建产物；先在 cli 运行 pnpm build",
)


class CliScenario:
    """在隔离存储中通过 Node 子进程运行用户 CLI。"""

    def __init__(self, directory: Path, url: str) -> None:
        self.directory = directory
        self.url = url
        self.calls = 0
        self.wrapper = directory / "cli.mjs"
        modules = {
            "runCli": "cli",
            "ProfileStore": "profiles",
            "InstallationStore": "install-lock",
            "AgentStore,createAgentContext": "agents",
        }
        module_root = (ROOT / "cli/dist").as_uri()
        imports = "\n".join(
            f"import {{{names}}} from {json.dumps(module_root + '/' + module + '.js')};"
            for names, module in modules.items()
        )
        self.wrapper.write_text(
            imports + "\nimport {join} from 'node:path';\n"
            "const root=process.env.MCPCAT_E2E_DIRECTORY;\n"
            "const profiles=new ProfileStore(join(root,'profiles.json'));\n"
            "const installs=new InstallationStore(join(root,'installations.json'));\n"
            "process.exitCode=await runCli(process.argv.slice(2),{\n"
            "profileStore:profiles,installationStore:installs,\n"
            "agentStore:new AgentStore(join(root,'agents.json')),\n"
            "credentialStore:null,\n"
            "agentContext:createAgentContext({homeDir:join(root,'agent-home')})});\n",
            encoding="utf-8",
        )

    async def run(self, *args: str, key: str = WRITE, code: int = 0) -> dict:
        """凭证仅经环境注入，断言输出脱敏，不自动重试写入。"""
        env = {k: v for k, v in os.environ.items() if not k.startswith("MCPCAT_")}
        env.update(MCPCAT_API_KEY=key, MCPCAT_E2E_DIRECTORY=str(self.directory))
        process = await asyncio.create_subprocess_exec(
            shutil.which("node"),
            str(self.wrapper),
            *args,
            "--json",
            "--non-interactive",
            env=env,
            cwd=self.directory,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        text = stdout.decode() + stderr.decode()
        assert key not in text
        assert process.returncode == code, text
        result = json.loads(stdout)
        assert result["ok"] is (code == 0), result
        self.calls += 1
        return result.get("data", result.get("error"))

    def json_file(self, name: str, value: dict) -> str:
        """写入本次场景的 JSON 输入文件。"""
        path = self.directory / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)


@pytest_asyncio.fixture
async def scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[CliScenario]:
    """真实应用与迁移，关闭无关的外部市场刷新。"""
    for key, value in {
        "mcpcat_config_path": str(tmp_path / "absent.json"),
        "mcpcat_artifacts_path": str(tmp_path / "artifacts"),
        "mcpcat_apps_artifacts_path": str(tmp_path / "apps"),
        "mcpcat_backups_path": str(tmp_path / "backups"),
        "mcpcat_default_admin_key": WRITE,
        "mcpcat_default_read_key": READ,
    }.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(security_service, "_first_run_keys", None)
    database = Database(tmp_path / "fixture.db")
    upgrade_schema(database)
    ConfigService.configure_database(database)
    ConfigService.save_config(
        {
            "mcpServers": {},
            "security": {"api_keys": []},
            "catalog": {"enabled": False},
            "code_mode": {"enabled": False},
        }
    )
    try:
        async with serve_app(create_app(database=database)) as url:
            cli = CliScenario(tmp_path, url)
            connected = await cli.run("connect", "fixture", url)
            assert connected["credentialPersistence"] == "environment"
            yield cli
    finally:
        ConfigService.reset_database()
        database.dispose()


async def publish_skill(cli: CliScenario, slug: str, version: str) -> None:
    """上传已审核的本仓库官方说明并发布，仅影响临时实例。"""
    await cli.run(
        "skills",
        "manage",
        "upload",
        "--file",
        str(ROOT / "skills" / slug),
        "--version",
        version,
        "--mode",
        "both",
        "--yes",
    )
    await cli.run(
        "skills", "manage", "publish", slug, "--version", version, "--reviewed", "--yes"
    )


async def inspect_published_skill(cli: CliScenario, slug: str) -> None:
    """按清单读取两种变体，校验实际下载的官方 Skill 内容。"""
    await cli.run("skills", "manage", "info", slug, key=READ)
    await cli.run("skills", "manage", "connections", slug, key=READ)
    for mode in ("local", "remote"):
        await cli.run(
            "skills",
            "manage",
            "file",
            slug,
            "--version",
            "1.0.0",
            "--mode",
            mode,
            "--path",
            f"{slug}/SKILL.md",
            key=READ,
        )
        output = f"{slug}-{mode}.zip"
        await cli.run(
            "skills",
            "manage",
            "download",
            slug,
            "--version",
            "1.0.0",
            "--mode",
            mode,
            "--output",
            output,
            key=READ,
        )
        with zipfile.ZipFile(cli.directory / output) as archive:
            assert (
                archive.read(f"{slug}/SKILL.md")
                == (ROOT / "skills" / slug / "SKILL.md").read_bytes()
            )


@pytest.mark.asyncio
async def test_official_skills_publish_install_and_lifecycle(
    scenario: CliScenario,
) -> None:
    """原样发布四个 Skill，实际安装并校验内容，再走版本生命周期。"""
    cli = scenario
    slugs = sorted(path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md"))
    assert len(slugs) == 4
    profiles = await cli.run("profiles")
    assert "fixture" in json.dumps(profiles)
    doctor = await cli.run("doctor", "--profile", "fixture")
    assert all(
        item["status"] == "ok"
        for item in doctor["checks"]
        if item["name"] in {"profile", "credential", "api"}
    )
    for slug in slugs:
        await publish_skill(cli, slug, "1.0.0")
        await inspect_published_skill(cli, slug)
        await cli.run("skills", "info", slug, "--version", "1.0.0", key=READ)
        await cli.run(
            "skills",
            "install",
            slug,
            "--version",
            "1.0.0",
            "--agent",
            "codex",
            "--scope",
            "project",
            key=READ,
        )
        installed = cli.directory / ".agents/skills" / slug / "SKILL.md"
        assert (
            installed.read_bytes() == (ROOT / "skills" / slug / "SKILL.md").read_bytes()
        )
    await check_installation_lifecycle(cli)


async def check_installation_lifecycle(cli: CliScenario) -> None:
    """固定、跳过、更新及回滚使用同一隔离安装记录。"""
    slug = "mcpcat-cli"
    await cli.run(
        "skills",
        "install",
        slug,
        "--version",
        "1.0.0",
        "--agent",
        "claude",
        "--scope",
        "user",
        key=READ,
    )
    await publish_skill(cli, slug, "1.0.1")
    common = (slug, "--agent", "claude", "--scope", "user")
    await cli.run("skills", "pin", *common, "--version", "1.0.0", key=READ)
    skipped = await cli.run("skills", "update", *common, key=READ)
    assert skipped["results"][0]["reason"] == "pinned"
    await cli.run("skills", "unpin", *common, key=READ)
    updated = await cli.run("skills", "update", *common, key=READ)
    assert updated["results"][0]["toVersion"] == "1.0.1"
    rolled = await cli.run(
        "skills", "rollback", *common, "--version", "1.0.0", key=READ
    )
    assert rolled["results"][0]["toVersion"] == "1.0.0"


@pytest.mark.asyncio
async def test_official_server_workflow(scenario: CliScenario) -> None:
    """真实远程 FastMCP 服务的添加、完整更新、启停和删除。"""
    cli, upstream = scenario, FastMCP("Official Skills Fixture")

    @upstream.tool()
    def ping() -> str:
        """Return fixture health."""
        return "pong"

    async with serve(upstream, "http") as url:
        config = {"type": "streamable-http", "url": url, "require_auth": True}
        path = cli.json_file("server.json", config)
        await cli.run("servers", "add", "fixture-time", "--file", path, "--yes")
        assert (await cli.run("servers", "info", "fixture-time"))["status"] == "running"
        await cli.run("servers", "config", "fixture-time", "--output", "export.json")
        assert json.loads((cli.directory / "export.json").read_text()) == config
        await cli.run("servers", "update", "fixture-time", "--file", path, "--yes")
        for action in ("stop", "start", "restart"):
            await cli.run("servers", action, "fixture-time", "--yes")
            state = await cli.run("servers", "info", "fixture-time")
            assert state["status"] == ("stopped" if action == "stop" else "running")
        await cli.run(
            "skills", "manage", "generate", "fixture-time", "--mode", "both", "--yes"
        )
        await prepare_app_draft(
            cli,
            bindings=[
                {"alias": "ping", "server_name": "fixture-time", "tool_name": "ping"}
            ],
        )
        await cli.run(
            "apps",
            "publish",
            "fixture-clock",
            "--version",
            "1.0.0",
            "--reviewed",
            "--yes",
        )
        await cli.run("servers", "delete", "fixture-time", "--yes", key=READ, code=4)
        await cli.run("servers", "delete", "fixture-time", "--yes")
        assert "fixture-time" not in (await cli.run("servers", "list"))["servers"]


@pytest.mark.asyncio
async def test_official_apps_workflow(scenario: CliScenario) -> None:
    """实际托管制品、审核配置、空绑定、发布、下载和撤回。"""
    cli = scenario
    content = await prepare_app_draft(cli)
    await cli.run(
        "apps", "publish", "fixture-clock", "--version", "1.0.0", "--yes", code=2
    )
    await cli.run(
        "apps", "publish", "fixture-clock", "--version", "1.0.0", "--reviewed", "--yes"
    )
    listing = await cli.run("apps", "list", key=READ)
    assert listing["complete"] is True
    assert (await cli.run("apps", "info", "fixture-clock", key=READ))[
        "visibility"
    ] == "private"
    await cli.run(
        "apps",
        "file",
        "fixture-clock",
        "--version",
        "1.0.0",
        "--path",
        "index.html",
        key=READ,
    )
    await cli.run(
        "apps",
        "download",
        "fixture-clock",
        "--version",
        "1.0.0",
        "--output",
        "download.html",
        key=READ,
    )
    assert (cli.directory / "download.html").read_text() == content
    await cli.run("apps", "connections", "fixture-clock", key=READ)
    await cli.run("apps", "withdraw", "fixture-clock", "--version", "1.0.0", code=10)
    await cli.run("apps", "withdraw", "fixture-clock", "--version", "1.0.0", "--yes")
    await cli.run("apps", "info", "fixture-clock", key=READ, code=5)


async def prepare_app_draft(
    cli: CliScenario, version: str = "1.0.0", bindings: list[dict] | None = None
) -> str:
    """上传已知测试 HTML，配置审核项和明确工具绑定。"""
    content = "<!doctype html><html><body>Official CLI fixture</body></html>"
    (cli.directory / "clock.html").write_text(content, encoding="utf-8")
    await cli.run(
        "apps",
        "upload",
        "fixture-clock",
        "--file",
        "clock.html",
        "--version",
        version,
        "--display-name",
        "Fixture Clock",
        "--yes",
    )
    await cli.run(
        "apps",
        "config",
        "fixture-clock",
        "--version",
        version,
        "--file",
        cli.json_file("review.json", {"csp": {}, "permissions": {}}),
        "--yes",
    )
    await cli.run(
        "apps",
        "bindings",
        "fixture-clock",
        "--version",
        version,
        "--file",
        cli.json_file("bindings.json", {"bindings": bindings or []}),
        "--yes",
    )
    return content


@pytest.mark.asyncio
async def test_official_apps_visibility_scope(scenario: CliScenario) -> None:
    """复现 Skill 提醒的跨版本公开/私有范围。"""
    cli = scenario
    await prepare_app_draft(cli)
    await cli.run(
        "apps",
        "publish",
        "fixture-clock",
        "--version",
        "1.0.0",
        "--reviewed",
        "--public",
        "--yes",
    )
    assert (await cli.run("apps", "info", "fixture-clock"))["visibility"] == "public"
    await prepare_app_draft(cli, version="1.0.1")
    await cli.run(
        "apps", "publish", "fixture-clock", "--version", "1.0.1", "--reviewed", "--yes"
    )
    info = await cli.run("apps", "info", "fixture-clock")
    assert info["visibility"] == "private"
    assert {row["version"] for row in info["versions"]} == {"1.0.0", "1.0.1"}
