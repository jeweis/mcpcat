"""单次连接的阶段、stderr 和错误归因；不与其他用户共用连接状态。"""

import json
import re
import uuid

from mcp.shared.exceptions import MCPError
from mcp_types import CONNECTION_CLOSED

from app.services.diagnostic_logs import diagnostic, diagnostic_store
from app.services.runtime_environments import RuntimeEnvironment
from app.services.runtime_packages import package_snapshot

ERRORS = [
    ("executable", ("可执行文件启动失败",), "上游可执行文件无法启动"),
    (
        "protocol",
        ("unsupported protocol version", "no common protocol"),
        "上游 MCP 协议版本不兼容",
    ),
    (
        "dependency",
        (
            "modulenotfounderror",
            "err_module_not_found",
            "cannot find module",
            "importerror",
            "requires-python",
            "ebadengine",
        ),
        "上游依赖或运行时不兼容",
    ),
    ("cache", ("enotempty", "eintegrity", "enoent"), "上游安装或缓存失败"),
    (
        "authentication",
        (
            "unauthorized",
            "forbidden",
            "authentication failed",
            "login failed",
        ),
        "上游认证或权限失败",
    ),
    (
        "tls",
        (
            "certificate verify",
            "unable_to_verify",
            "sslerror",
            "self signed certificate",
        ),
        "上游 TLS/证书失败",
    ),
    (
        "dns",
        (
            "enotfound",
            "eai_again",
            "name or service not known",
            "temporary failure in name resolution",
        ),
        "上游 DNS 解析失败",
    ),
    (
        "network",
        (
            "econnrefused",
            "connection refused",
            "econnreset",
            "network is unreachable",
            "all connection attempts failed",
            "connecterror",
        ),
        "上游网络连接失败",
    ),
]


def classify(text: str, code: int | None = None) -> tuple[str, str]:
    """有明确证据才分类，未知断连保留未知，不推测为网络或协议错误。"""
    lower = text.lower()
    if re.search(r"\bhttp status (?:401|403)\b", lower):
        return "authentication", "上游认证或权限失败"
    if "[mcpcat_startup:" in lower:
        return "preparation", "上游环境准备失败"
    for category, evidence, title in ERRORS:
        if any(item in lower for item in evidence):
            return category, title
    if "http status " in lower:
        return "http", "上游 HTTP 返回错误状态，请查看具体状态码"
    if code in {-9, 137}:
        return "killed", "上游进程被强制终止，需检查内存限制或外部终止"
    if code not in {None, 0}:
        return "process_exit", "上游进程异常退出"
    return "connection_closed", "上游连接关闭，尚无足够证据确认原因"


def error_evidence(error: BaseException) -> str:
    """展开异常组与原因链，避免只得到 TaskGroup 的外层提示。"""
    pending, seen, parts = [error], set(), []
    while pending and len(seen) < 20:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        parts.append(type(item).__name__ + ": " + str(item)[:2000])
        pending.extend(
            child for child in (item.__cause__, item.__context__) if child is not None
        )
        pending.extend(getattr(item, "exceptions", ()))
    return "\n".join(parts)[-16000:]


class RuntimeAttempt:
    """一次 transport 连接的独立状态，stderr 只保留有界尾部。"""

    def __init__(
        self, server: str, environment: RuntimeEnvironment | None = None
    ) -> None:
        self.id = uuid.uuid4().hex
        self.server, self.environment = server, environment
        self.stage = "spawn"
        self.ready = False
        self.protocol = None
        self.stderr = ""
        self.category = None
        self.recovery_profile = None
        self.exit_code = None
        self.pid = None
        self.shutdown = False
        self.closed_rpc = False
        self.discover_closed = False
        self.observed = {}
        self.metadata = (
            environment.directory / ("attempt-" + self.id + ".json")
            if environment
            else None
        )
        self.emit("上游连接开始")

    def text(self, text: str) -> None:
        """仅在当前尝试内累积，避免同服务并发连接的错误串线。"""
        self.stderr = (self.stderr + text + "\n")[-16000:]

    def phase(self, stage: str, protocol: str | None = None) -> None:
        """记录协商和就绪阶段；业务请求阶段不再具备启动重试资格。"""
        self.stage = stage
        if stage == "ready":
            self.ready = True
            self.protocol = protocol
            self.emit("上游协议协商完成")

    def read_exit(self) -> None:
        """监督器输出仅包含 pid/退出状态，不相信 stdout 中的伪造状态。"""
        if self.metadata and self.metadata.exists():
            try:
                data = json.loads(self.metadata.read_text())
                self.exit_code, self.pid = data.get("exit_code"), data.get("pid")
                self.shutdown = data.get("shutdown", False)
                self.observed = data.get("observed") or {}
                if data.get("error"):
                    self.text("可执行文件启动失败：" + data["error"])
            except (OSError, ValueError):
                pass

    def snapshot(self) -> dict:
        """返回可持久化的安全摘要，原始 stderr 只进入脱敏诊断日志。"""
        return {
            "id": self.id,
            "stage": self.stage,
            "ready": self.ready,
            "protocol": self.protocol,
            "exit_code": self.exit_code,
            "pid": self.pid,
            "error_code": self.category,
            "recovery_profile": self.recovery_profile,
            "closed_rpc": self.closed_rpc,
            "discover_closed": self.discover_closed,
            "revision": self.environment.revision if self.environment else None,
        }

    def emit(
        self, message: str, level: str = "INFO", error: BaseException | None = None
    ) -> None:
        diagnostic(
            message,
            server=self.server,
            operation=self.stage,
            level=level,
            detail=json.dumps(self.snapshot(), ensure_ascii=False),
            error=error,
            attempt_id=self.id,
            stage=self.stage,
            exit_code=self.exit_code,
            error_code=self.category,
            environment_revision=(
                self.environment.revision if self.environment else None
            ),
        )

    def failed(self, error: BaseException) -> None:
        """优先展示此尝试的实际进程错误，同时保留原协议异常。"""
        self.read_exit()
        self.closed_rpc = (
            isinstance(error, MCPError) and error.code == CONNECTION_CLOSED
        )
        self.category, title = classify(
            self.stderr + "\n" + error_evidence(error), self.exit_code
        )
        store = diagnostic_store()
        detail = json.dumps(self.snapshot(), ensure_ascii=False) + "\n" + self.stderr
        if store:
            store.record(
                title,
                server=self.server,
                operation=self.stage,
                level="ERROR",
                detail=detail,
                attempt_id=self.id,
                stage=self.stage,
                exit_code=self.exit_code,
                error_code=self.category,
                environment_revision=(
                    self.environment.revision if self.environment else None
                ),
            )
        self.emit("上游连接异常详情", "ERROR", error)

    def finish(self) -> None:
        """持久化安全结果，移除仅供当前进程使用的元数据文件。"""
        self.read_exit()
        if self.exit_code not in {None, 0} and not self.shutdown and not self.category:
            self.failed(RuntimeError("上游进程退出"))
        if self.environment:
            packages = (
                package_snapshot(self.environment.directory, self.observed)
                if self.ready
                else None
            )
            self.environment.record(
                {
                    **self.snapshot(),
                    "packages_scope": (
                        "resolved" if self.observed else "cache_inventory"
                    ),
                },
                packages=packages,
            )
        if self.metadata:
            self.metadata.unlink(missing_ok=True)
