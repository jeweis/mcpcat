"""临时实例的真实 HTTP 诊断验收；--serve 可供浏览器验收使用。"""

import argparse
import asyncio
import json
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx2

from app.application import create_app
from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.market_service import MarketService
from app.services.security_service import security_service
from app.storage.database import Database
from scripts.probe_fastmcp_network import serve_app


def prepare(root: Path):
    """只使用临时数据库和随机测试凭证，不读取生产配置。"""
    read, write = secrets.token_hex(24), secrets.token_hex(24)
    settings.mcpcat_config_path = str(root / "config.json")
    settings.mcpcat_database_path = str(root / "data.db")
    settings.mcpcat_artifacts_path = str(root / "artifacts")
    settings.mcpcat_backups_path = str(root / "backups")
    settings.mcpcat_default_admin_key = write
    settings.mcpcat_default_read_key = read
    security_service._first_run_keys = None
    Path(settings.mcpcat_config_path).write_text('{"mcpServers": {}}')
    MarketService.refresh_async = lambda self: None
    app = create_app(database=Database(root / "data.db"))
    app.state.diagnostic_logs.record(
        "网络诊断验收夹具", operation="probe", detail="fixture error"
    )
    return app, read, write


async def execute(api, key, **body):
    """解析真实 HTTP 流，要求服务端交付最终状态。"""
    async with api.stream(
        "POST", "/api/admin/commands/run", json=body, headers={"Mcpcat-Key": key}
    ) as response:
        assert response.status_code == 200, (
            response.status_code,
            await response.aread(),
        )
        rows = [json.loads(line) async for line in response.aiter_lines() if line]
    assert rows[-1]["type"] == "result", rows
    return rows


async def verify(api, read, write):
    """包含回环 Ping、DNS、TCP、HTTP、停止和权限边界。"""
    endpoint = "/api/admin/commands"
    assert (await api.get(endpoint)).status_code == 401
    assert (await api.get(endpoint, headers={"Mcpcat-Key": read})).status_code == 403
    for kind, target, extra in [
        ("ping", "127.0.0.1", {}),
        ("dns", "localhost", {}),
        ("tcp", "127.0.0.1", {"port": api.base_url.port}),
        ("http", f"{str(api.base_url).rstrip('/')}/ui/index.html", {}),
    ]:
        rows = await execute(api, write, kind=kind, target=target, **extra)
        assert rows[-1]["status"] == "completed", rows
        assert any(row["type"] == "output" for row in rows)
    rows = await execute(
        api,
        write,
        kind="shell",
        command="printf 'arbitrary-shell-ok\\n' | cat; printf 'stderr-ok\\n' >&2; exit 7",
    )
    assert rows[-1]["exit_code"] == 7 and rows[-1]["status"] == "failed", rows
    assert "arbitrary-shell-ok" in str(rows) and "stderr-ok" in str(rows)
    async with api.stream(
        "POST",
        endpoint + "/run",
        headers={"Mcpcat-Key": write},
        json={"kind": "ping", "target": "127.0.0.1", "count": 10},
    ) as response:
        async for line in response.aiter_lines():
            if json.loads(line)["type"] == "output":
                break
    for _ in range(50):
        status = await api.get(endpoint, headers={"Mcpcat-Key": write})
        if status.json()["running"] == 0:
            break
        await asyncio.sleep(0.1)
    assert status.json()["running"] == 0, "断开后诊断进程未回收"
    rows = await execute(
        api, write, kind="ping", target="127.0.0.1", count=10, timeout=1
    )
    assert rows[-1]["status"] == "timed_out", rows
    print(
        "PASS: arbitrary shell/pipes/stderr/exit code, Ping/DNS/TCP/HTTP HEAD, timeout, disconnect cleanup, admin guards"
    )


async def main():
    """验收完成即清理临时实例；服务模式不打印测试密钥。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", type=Path)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="mcpcat-network-probe-") as folder:
        app, read, write = prepare(Path(folder))
        async with serve_app(app) as url:
            async with httpx2.AsyncClient(base_url=url, timeout=75) as api:
                await verify(api, read, write)
            if args.serve:
                args.serve.write_text(
                    json.dumps({"url": url, "read": read, "write": write})
                )
                args.serve.chmod(0o600)
                try:
                    await asyncio.Event().wait()
                finally:
                    args.serve.unlink(missing_ok=True)
        ConfigService.reset_database()


if __name__ == "__main__":
    asyncio.run(main())
