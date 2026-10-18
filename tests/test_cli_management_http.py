"""构建后的用户 CLI 连接隔离 SQLite/制品目录，验证真实 HTTP 生命周期。

仅为维护者回归，不向用户 CLI 添加 test/inspect 功能，不连接已有 Docker。
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from test_skill_content_workflow import READ, WRITE, content_app

from app.core.config import settings


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js 与已构建 CLI")
@pytest.mark.skipif(
    not (Path(__file__).parents[1] / "cli" / "dist" / "bin.js").is_file(),
    reason="独立后端环境无 CLI 构建；先在 cli 执行 pnpm build 再运行此集成回归",
)
def test_built_cli_real_management_http(content_app, tmp_path, monkeypatch):
    app, _ = content_app
    cli = Path(__file__).parents[1] / "cli" / "dist" / "bin.js"
    assert cli.is_file(), "请先在 cli 执行 pnpm build"
    monkeypatch.setattr(settings, "mcpcat_apps_artifacts_path", str(tmp_path / "apps"))
    # 绑定随机回环端口，不触碰用户已运行的服务、配置或制品。
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()

    def run(*args, key=WRITE, code=0):
        env = {
            **os.environ,
            "MCPCAT_URL": f"http://127.0.0.1:{port}",
            "MCPCAT_API_KEY": key,
            "CI": "true",
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "cli-config"),
        }
        result = subprocess.run(
            [shutil.which("node"), str(cli), *args, "--json", "--non-interactive"],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert key not in result.stdout + result.stderr
        assert result.returncode == code, result.stdout + result.stderr
        return json.loads(result.stdout or result.stderr)

    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.02)
        assert server.started
        skill = tmp_path / "cli-clock"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: cli-clock\ndescription: Plan international meetings.\n---\n# Clock\n",
            encoding="utf-8",
        )
        (skill / ".env").write_text("DO_NOT_UPLOAD=synthetic", encoding="utf-8")
        run(
            "skills",
            "manage",
            "upload",
            "--file",
            str(skill),
            "--version",
            "1.0.0",
            "--mode",
            "both",
            "--yes",
            key=READ,
            code=4,
        )
        uploaded = run(
            "skills",
            "manage",
            "upload",
            "--file",
            str(skill),
            "--version",
            "1.0.0",
            "--mode",
            "both",
            "--yes",
        )
        assert ".env" in uploaded["data"]["excluded"]
        run(
            "skills",
            "manage",
            "publish",
            "cli-clock",
            "--version",
            "1.0.0",
            "--reviewed",
            "--yes",
        )
        run("skills", "manage", "info", "cli-clock", key=READ)
        run(
            "skills",
            "manage",
            "file",
            "cli-clock",
            "--version",
            "1.0.0",
            "--path",
            "cli-clock/SKILL.md",
            key=READ,
        )
        run("skills", "list", key=READ)  # 原 Registry 命令仍可读取新上传内容。
        run(
            "skills",
            "manage",
            "download",
            "cli-clock",
            "--version",
            "1.0.0",
            "--output",
            str(tmp_path / "skill.zip"),
            key=READ,
        )
        run("skills", "manage", "connections", "cli-clock", key=READ)
        run(
            "skills",
            "manage",
            "withdraw-remote",
            "cli-clock",
            "--version",
            "1.0.0",
            "--yes",
        )

        html = tmp_path / "clock.html"
        html.write_text(
            "<!doctype html><html><body>World clock</body></html>", encoding="utf-8"
        )
        run(
            "apps",
            "upload",
            "cli-clock",
            "--file",
            str(html),
            "--display-name",
            "Clock",
            "--version",
            "1.0.0",
            "--yes",
        )
        config = tmp_path / "review.json"
        config.write_text(
            json.dumps({"csp": {}, "permissions": {}, "changelog": "Reviewed"}),
            encoding="utf-8",
        )
        run(
            "apps",
            "config",
            "cli-clock",
            "--version",
            "1.0.0",
            "--file",
            str(config),
            "--yes",
        )
        config.write_text('{"bindings": []}', encoding="utf-8")
        run(
            "apps",
            "bindings",
            "cli-clock",
            "--version",
            "1.0.0",
            "--file",
            str(config),
            "--yes",
        )
        run("apps", "publish", "cli-clock", "--version", "1.0.0", "--reviewed", "--yes")
        run("apps", "list", key=READ)
        run("apps", "info", "cli-clock", key=READ)
        run(
            "apps",
            "file",
            "cli-clock",
            "--version",
            "1.0.0",
            "--path",
            "index.html",
            key=READ,
        )
        output = tmp_path / "downloaded.html"
        run(
            "apps",
            "download",
            "cli-clock",
            "--version",
            "1.0.0",
            "--output",
            str(output),
            key=READ,
        )
        assert output.read_bytes() == html.read_bytes()
        run("apps", "connections", "cli-clock", key=READ)
        run("apps", "withdraw", "cli-clock", "--version", "1.0.0", "--yes")
        run("apps", "info", "cli-clock", key=READ, code=5)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
