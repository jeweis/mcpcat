"""每身份执行槽跨进程互斥，崩溃后由 OS 释放，不依赖租约时钟。"""

import re
from pathlib import Path

from filelock import FileLock, Timeout


class ExecutionLeasePool:
    """共享 SQLite 文件的 worker 使用相同稳定身份摘要和锁路径。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def acquire(self, principal: str, limit: int) -> FileLock | None:
        """非阻塞取得一个槽；独立 FileLock 防止同线程递归重入绕过限制。"""
        if not re.fullmatch(r"[a-f0-9]{64}", principal) or not 1 <= limit <= 16:
            raise ValueError("执行身份或并发限制无效")
        gate = FileLock(
            f"{self.database_path}.code-execute-{principal}.gate.lock",
            timeout=0,
            thread_local=False,
        )
        try:
            gate.acquire()
        except Timeout:
            return None
        try:
            return self._probe(principal, limit)
        finally:
            gate.release()

    def _probe(self, principal: str, limit: int) -> FileLock | None:
        free: list[FileLock] = []
        chosen = None
        busy = 0
        try:
            for slot in range(16):
                lock = FileLock(
                    f"{self.database_path}.code-execute-{principal}-{slot}.lock",
                    timeout=0,
                    thread_local=False,
                )
                try:
                    lock.acquire()
                    free.append(lock)
                except Timeout:
                    busy += 1
            if busy < limit and free:
                chosen = free[0]
            return chosen
        finally:
            for lock in free:
                if lock is not chosen:
                    lock.release()
