"""stdio 进程监督器。stdout 完全由子进程占用，元数据使用独立私有文件。"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if __package__:
    from app.services.runtime_process_probe import inspect_process
else:
    from runtime_process_probe import inspect_process


def report(path: Path, **data: object) -> None:
    """只报告进程身份与状态，不输出 argv 或环境变量。"""
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(data, stream)
    temporary.replace(path)


def main() -> int:
    """透明连接 stdin/stdout/stderr，等待并报告真实上游退出码。"""
    path = Path(sys.argv[1])
    try:
        child = subprocess.Popen(
            sys.argv[2:],
            stdin=sys.stdin.buffer,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )
    except OSError as error:
        report(path, exit_code=127, error=type(error).__name__)
        print(f"MCPCat process launch failed: {type(error).__name__}", file=sys.stderr)
        return 127
    report(path, pid=child.pid, exit_code=None)
    shutdown = False

    def terminate(*_):
        nonlocal shutdown
        shutdown = True
        if child.poll() is None:
            child.terminate()

    if os.name == "posix":
        signal.signal(signal.SIGTERM, terminate)
    observed = {}
    while child.poll() is None:
        current = inspect_process(child.pid, path.parent)
        if current and current != observed:
            observed = current
            report(path, pid=child.pid, exit_code=None, observed=observed)
        time.sleep(0.05)
    code = child.wait()
    report(path, pid=child.pid, exit_code=code, shutdown=shutdown, observed=observed)
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
