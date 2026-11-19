"""Linux 只读观察子进程使用的环境路径，不收集完整 argv 或环境变量。"""

from pathlib import Path


def inspect_process(pid: int, cache_root: Path | None = None) -> dict:
    """在有界子进程树中寻找实际 Python 环境或 npx 安装目录。"""
    pending, seen, result = [pid], set(), {}
    while pending and len(seen) < 24:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        root = Path("/proc") / str(current)
        try:
            pending.extend(
                int(value)
                for value in (root / "task" / str(current) / "children")
                .read_text()
                .split()
            )
            parts = (root / "cmdline").read_bytes()[:16000].split(b"\0")
            for part in parts[:4]:
                path = Path(part.decode("utf-8", "replace"))
                prefix = path.parent.parent.resolve()
                if (
                    path.parent.name in {"bin", "Scripts"}
                    and (cache_root is None or prefix.is_relative_to(cache_root))
                    and (
                        (prefix / "pyvenv.cfg").is_file()
                        or any(prefix.glob("lib/python*/site-packages"))
                    )
                ):
                    result["python_environment"] = str(prefix)
                if (
                    "/_npx/" in str(path)
                    and "/node_modules/" in str(path)
                    and (cache_root is None or path.is_relative_to(cache_root))
                ):
                    result["node_environment"] = str(path).split("/node_modules/", 1)[0]
        except (OSError, ValueError):
            continue
    return result
