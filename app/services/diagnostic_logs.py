"""当前进程的有界诊断日志；不持有异常对象、请求正文或工具参数。"""

import logging
import threading
import traceback
import uuid
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from app.services.diagnostic_redaction import redact, secret_values

current_diagnostics: ContextVar[Any] = ContextVar("diagnostic_store", default=None)


class DiagnosticLogs:
    """线程安全的有界文本快照；响应中明确保留范围和截断情况。"""

    def __init__(self, capacity: int = 1000) -> None:
        self.capacity = capacity
        self.instance_id = uuid.uuid4().hex
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.rows: deque[dict] = deque(maxlen=capacity)
        self.secrets: set[str] = set()
        self.lock = threading.RLock()
        self.sequence = 0

    def protect(self, value: object) -> None:
        """更新脱敏集合；查询时还会重新脱敏以覆盖刚修改的配置。"""
        with self.lock:
            self.secrets.update(secret_values(value))

    def record(
        self,
        message: str,
        *,
        level: str = "ERROR",
        server: str = "",
        operation: str = "",
        error: BaseException | None = None,
        source: str = "mcpcat",
        detail: str = "",
        attempt_id: str | None = None,
        stage: str | None = None,
        exit_code: int | None = None,
        error_code: str | None = None,
        environment_revision: str | None = None,
    ) -> int:
        """保存已脱敏、有界的异常链，不保存 traceback 对象。"""
        if error is not None:
            detail = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
        with self.lock:
            self.sequence += 1
            text = redact(detail, self.secrets)
            self.rows.append(
                {
                    "id": self.sequence,
                    **(
                        {
                            "attempt_id": attempt_id,
                            "stage": stage,
                            "exit_code": exit_code,
                            "error_code": error_code,
                            "environment_revision": environment_revision,
                        }
                        if attempt_id
                        else {}
                    ),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": level,
                    "server": redact(server, self.secrets)[:256],
                    "operation": redact(operation, self.secrets)[:128],
                    "source": redact(source, self.secrets)[:256],
                    "message": redact(message, self.secrets)[:2000],
                    "detail": text[:16000],
                    "truncated": len(text) > 16000,
                }
            )
            return self.sequence

    def query(
        self,
        *,
        level: str = "ALL",
        server: str = "",
        search: str = "",
        before: int | None = None,
        limit: int = 100,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict:
        """按快照倒序分页，后续新日志不会推移已读取页的游标。"""
        threshold = getattr(logging, level, 0)
        with self.lock:
            rows = [self._safe(row) for row in reversed(self.rows)]
            servers = sorted({row["server"] for row in rows if row["server"]})
        matched = [
            row
            for row in rows
            if getattr(logging, row["level"], 0) >= threshold
            and (not server or row["server"] == server)
            and (before is None or row["id"] < before)
            and (since is None or datetime.fromisoformat(row["timestamp"]) >= since)
            and (until is None or datetime.fromisoformat(row["timestamp"]) <= until)
            and search.casefold() in " ".join(str(v) for v in row.values()).casefold()
        ]
        page = matched[:limit]
        return {
            "items": page,
            "servers": servers,
            "capacity": self.capacity,
            "retained": len(rows),
            "instance_id": self.instance_id,
            "started_at": self.started_at,
            "persistent": False,
            "next_before": page[-1]["id"] if len(matched) > limit else None,
        }

    def _safe(self, row: dict) -> dict:
        return {
            key: (
                redact(value, self.secrets)
                if isinstance(value, str) and key not in {"stage", "error_code"}
                else value
            )
            for key, value in row.items()
        }


class DiagnosticHandler(logging.Handler):
    """补充收集应用和 MCP 库的警告/错误，不改变原有控制台 handler。"""

    def __init__(self, store: DiagnosticLogs) -> None:
        super().__init__(logging.WARNING)
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        """日志采集失败不干扰原请求，也不递归写日志。"""
        if not record.name.startswith(("app.", "fastmcp", "mcp.")):
            return
        try:
            detail = (
                "".join(traceback.format_exception(*record.exc_info))
                if record.exc_info
                else ""
            )
            store = current_diagnostics.get() or self.store
            store.record(
                record.getMessage(),
                level=record.levelname,
                source=record.name,
                detail=detail,
                server=getattr(record, "server_name", ""),
                operation=getattr(record, "operation", ""),
            )
        except Exception:
            pass


def install_diagnostics() -> DiagnosticLogs:
    """应用工厂重复调用时替换本组件的 handler，避免重复和跨实例读取。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, DiagnosticHandler):
            root.removeHandler(handler)
            handler.close()
    store = DiagnosticLogs()
    root.addHandler(DiagnosticHandler(store))
    return store


def diagnostic_store() -> DiagnosticLogs | None:
    """取得所属请求的存储，后台任务使用应用安装的存储。"""
    store = current_diagnostics.get()
    if store is None:
        store = next(
            (
                h.store
                for h in logging.getLogger().handlers
                if isinstance(h, DiagnosticHandler)
            ),
            None,
        )
    return store


def diagnostic(message: str, **kwargs: Any) -> None:
    """记录当前请求或后台任务的诊断，不将原始异常打印到控制台。"""
    store = diagnostic_store()
    if store is not None:
        store.record(message, **kwargs)
