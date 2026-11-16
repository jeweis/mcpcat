"""真实 npm 冷缓存、HTTP 测试台、并发身份与工具发现验收；全部使用临时数据。"""

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

import httpx2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.application import create_app
from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from scripts.probe_fastmcp_network import serve_app
from scripts.stdio_startup_fixture import events, real_npx_config


def prepare(root: Path, delay: float) -> tuple[object, str, str]:
    """启动真实应用工厂；密钥随机生成且不写入输出报告。"""
    settings.mcpcat_config_path = str(root / "config.json")
    settings.mcpcat_database_path = str(root / "app.db")
    settings.mcpcat_artifacts_path = str(root / "artifacts")
    settings.mcpcat_apps_artifacts_path = str(root / "apps")
    settings.mcpcat_backups_path = str(root / "backups")
    settings.mcpcat_default_admin_key = secrets.token_urlsafe(24)
    settings.mcpcat_default_read_key = secrets.token_urlsafe(24)
    config = real_npx_config(root / "source", delay=delay)
    Path(settings.mcpcat_config_path).write_text(
        json.dumps(
            {
                "mcpServers": {"cold": {**config, "require_auth": True}},
            }
        )
    )
    app = create_app()
    app.state.market_service.refresh_async = lambda: None
    return app, settings.mcpcat_default_read_key, settings.mcpcat_default_admin_key


async def session(api: httpx2.AsyncClient, key: str, owner: str) -> tuple[str, float]:
    """每个用户拥有独立 Roots 声明和测试会话。"""
    started = monotonic()
    response = await api.post(
        "/api/inspector/sessions",
        headers={"Mcpcat-Key": key},
        json={
            "server_name": "cold",
            "client_options": {"roots": [{"uri": f"file:///fixture/{owner}"}]},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"], monotonic() - started


async def exercise(api: httpx2.AsyncClient, key: str, sid: str, owner: str) -> int:
    """查询真实发现结果，再验证两跳工具调用返回当前用户的 Roots。"""
    path = f"/api/inspector/sessions/{sid}"
    headers = {"Mcpcat-Key": key}
    for _ in range(2):
        response = await api.get(path + "/tools", headers=headers)
        assert response.status_code == 200, response.text
        assert [tool["name"] for tool in response.json()["tools"]] == ["identity"]
    response = await api.post(
        path + "/call",
        headers=headers,
        json={"tool": "identity", "arguments": {"pause": 1}},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["roots"] == [f"file:///fixture/{owner}"], data
    return data["pid"]


async def failure_and_logs(api: httpx2.AsyncClient, read: str, write: str) -> None:
    """验证失败来源不伪装为空列表，错误原因进入管理员日志。"""
    response = await api.post(
        "/api/servers",
        headers={"Mcpcat-Key": write},
        json={
            "name": "broken",
            "config": {
                "type": "stdio",
                "command": sys.executable,
                "args": [
                    "-c",
                    "import sys; print('npm error ENOTEMPTY fixture', file=sys.stderr); sys.exit(1)",
                ],
            },
        },
    )
    assert response.status_code == 200, response.text
    response = await api.post(
        "/api/inspector/sessions",
        headers={"Mcpcat-Key": read},
        json={"server_name": "broken"},
    )
    if response.status_code == 200:
        sid = response.json()["session_id"]
        try:
            listed = await api.get(
                f"/api/inspector/sessions/{sid}/tools", headers={"Mcpcat-Key": read}
            )
            assert listed.status_code >= 400, listed.text
        finally:
            await api.delete(
                f"/api/inspector/sessions/{sid}", headers={"Mcpcat-Key": read}
            )
    else:
        assert response.status_code >= 400, response.text
    assert (await api.get("/api/admin/logs")).status_code == 401
    assert (
        await api.get("/api/admin/logs", headers={"Mcpcat-Key": read})
    ).status_code == 403
    response = await api.get(
        "/api/admin/logs",
        headers={"Mcpcat-Key": write},
        params={"server": "broken", "level": "ERROR"},
    )
    assert response.status_code == 200
    assert "ENOTEMPTY" in response.text
    assert read not in response.text and write not in response.text


async def verify(root: Path, delay: float) -> dict:
    """不访问已有容器、用户数据库或网络 npm 仓库。"""
    app, read, write = prepare(root, delay)
    async with serve_app(app) as url:
        app.state.port = int(url.rsplit(":", 1)[1])
        async with httpx2.AsyncClient(base_url=url, timeout=180) as api:
            created = await asyncio.gather(
                session(api, read, "alice"), session(api, write, "bob")
            )
            ids = [item[0] for item in created]
            try:
                pids = await asyncio.gather(
                    exercise(api, read, ids[0], "alice"),
                    exercise(api, write, ids[1], "bob"),
                )
                assert len(set(pids)) == 2
                for sid, foreign in [(ids[0], write), (ids[1], read)]:
                    assert (
                        await api.get(
                            f"/api/inspector/sessions/{sid}/tools",
                            headers={"Mcpcat-Key": foreign},
                        )
                    ).status_code == 404
                await failure_and_logs(api, read, write)
            finally:
                for sid, key in zip(ids, [read, write]):
                    await api.delete(
                        f"/api/inspector/sessions/{sid}", headers={"Mcpcat-Key": key}
                    )
    installs = (root / "source" / "installs.txt").read_text().splitlines()
    # npm 对 file: tarball 可能每次重新安装；验证真实启动均成功且无安装重叠。
    assert installs and all(item == "installed" for item in installs), installs
    for pid in pids:
        with_process = True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            with_process = False
        assert not with_process, f"fixture process not released: {pid}"
    waits = [round(item[1], 2) for item in created]
    assert max(waits) >= delay
    return {
        "npm": subprocess.check_output(["npm", "--version"], text=True).strip(),
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "cold_delay_seconds": delay,
        "session_wait_seconds": waits,
        "installs": len(installs),
        "tools": ["identity"],
        "distinct_processes": len(set(pids)),
        "cross_identity_access": "404",
        "stderr_and_admin_permissions": "passed",
        "source_process_starts": sum(
            row["event"] == "spawn" for row in events(root / "source")
        ),
        "process_cleanup": "passed",
    }


async def main(delay: float) -> None:
    """每次执行创建空缓存和数据库；退出时清理全部夹具数据。"""
    try:
        with TemporaryDirectory(prefix="mcpcat-startup-e2e-") as folder:
            report = await verify(Path(folder), delay)
            print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        for sid in list(inspector_service.sessions):
            await inspector_service.close_session(sid)
        ConfigService.reset_database()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cold-delay", type=float, default=31)
    asyncio.run(main(parser.parse_args().cold_delay))
