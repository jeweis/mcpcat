"""网关自身的订阅快照读取标记，不改写 SDK 请求上下文。"""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

_snapshot_read = ContextVar("mcpcat_subscription_snapshot_read", default=False)


def is_subscription_snapshot_read() -> bool:
    """内部目录检查不能递归注册客户端订阅。"""
    return _snapshot_read.get()


@contextmanager
def subscription_snapshot_read() -> Iterator[None]:
    """保持身份与协议上下文，只抑制网关自身的自动监听注册。"""
    token = _snapshot_read.set(True)
    try:
        yield
    finally:
        _snapshot_read.reset(token)
