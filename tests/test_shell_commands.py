"""管理员自由 Shell：真实命令组合、退出码、隔离与子进程清理。"""

import asyncio
import json
import os
import sys

import httpx2
import pytest
from pydantic import ValidationError
from test_skill_content_workflow import READ, WRITE, content_app  # noqa: F401

from app.services.diagnostic_logs import DiagnosticLogs
from app.services.network_command_runner import NetworkCommandRunner
from app.services.network_commands import command_args
from app.services.shell_commands import ShellCommand


async def run(command: str, timeout: int = 5):
    request = ShellCommand(kind="shell", command=command, timeout=timeout)
    runner = NetworkCommandRunner(DiagnosticLogs())
    rows = [
        json.loads(item) async for item in runner.events(request, command_args(request))
    ]
    assert runner.running == 0
    return rows


@pytest.mark.parametrize("text", ["", " \n", "echo\x00invalid"])
def test_rejects_empty_and_nul(text):
    with pytest.raises(ValidationError):
        ShellCommand(kind="shell", command=text)


@pytest.mark.asyncio
async def test_shell_pipes_redirection_multiline_exit_and_working_directory(tmp_path):
    rows = await run(f"""cd '{tmp_path}'
printf 'hello\\nworld\\n' | grep world > result.txt
cat result.txt
pwd
printf 'stderr-message\\n' >&2
exit 7""")
    output = "".join(row.get("text", "") for row in rows)
    assert "world" in output and str(tmp_path) in output and "stderr-message" in output
    assert (tmp_path / "result.txt").read_text() == "world\n"
    assert rows[-1]["exit_code"] == 7 and rows[-1]["status"] == "failed"
    rows = await run("pwd")
    assert os.getcwd() in "".join(row.get("text", "") for row in rows)


@pytest.mark.asyncio
async def test_timeout_reaps_shell_child_group(tmp_path):
    pid = tmp_path / "pid"
    source = f"import os,time;open({str(pid)!r},'w').write(str(os.getpid()));print('ready',flush=True);time.sleep(30)"
    import shlex

    text = f"{shlex.quote(sys.executable)} -u -c {shlex.quote(source)} & wait"
    rows = await run(text, timeout=1)
    assert rows[-1]["status"] == "timed_out"
    # 同组父/子进程同时被杀时，孤儿可能短暂处于 zombie；必须没有活进程。
    process = await asyncio.create_subprocess_exec(
        "ps", "-o", "stat=", "-p", pid.read_text(), stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    assert not stdout.strip() or stdout.strip().startswith(b"Z")


@pytest.mark.asyncio
async def test_admin_auth_guards_arbitrary_shell(content_app):
    app, _ = content_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as api:
        payload = {"kind": "shell", "command": "printf 'arbitrary-shell-ok\\n'"}
        assert (
            await api.post("/api/admin/commands/run", json=payload)
        ).status_code == 401
        assert (
            await api.post(
                "/api/admin/commands/run", json=payload, headers={"Mcpcat-Key": READ}
            )
        ).status_code == 403
        result = await api.post(
            "/api/admin/commands/run", json=payload, headers={"Mcpcat-Key": WRITE}
        )
        assert result.status_code == 200, result.text
        assert "arbitrary-shell-ok" in result.text
        assert json.loads(result.text.splitlines()[-1])["exit_code"] == 0
        capabilities = (
            await api.get("/api/admin/commands", headers={"Mcpcat-Key": WRITE})
        ).json()
        assert (
            "shell" in capabilities["commands"] and capabilities["shell"] == "/bin/sh"
        )
