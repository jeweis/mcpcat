"""提交后的进程内失效信号；不携带行数据，也不直接作为 MCP 通知发送。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import RLock
from weakref import WeakValueDictionary

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

OBSERVED_TABLES = frozenset(
    {
        "skills",
        "skill_versions",
        "skill_content_variants",
        "skill_remote_artifacts",
        "apps",
        "app_versions",
        "mcp_servers",
        "system_settings",
        "api_keys",
    }
)
_HUBS: WeakValueDictionary = WeakValueDictionary()
_HUBS_LOCK = RLock()
_JOURNAL = "mcpcat_committed_changes"


class ChangeNotifications:
    """每个监听者只有一个待处理槽，合并连续提交的表名，内存有界。"""

    def __init__(self) -> None:
        self._listeners = {}
        self._lock = RLock()

    @asynccontextmanager
    async def watch(self) -> AsyncIterator[asyncio.Queue[frozenset[str]]]:
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=1)
        token = object()

        def deliver(tables):
            with self._lock:
                if token not in self._listeners:
                    return
            merged = set(tables)
            if not queue.empty():
                merged.update(queue.get_nowait())
            queue.put_nowait(frozenset(merged))

        with self._lock:
            self._listeners[token] = (loop, deliver)
        try:
            yield queue
        finally:
            with self._lock:
                self._listeners.pop(token, None)

    def publish(self, tables: set[str]) -> None:
        """可从数据库工作线程调用；永不包含凭证、SQL 或制品内容。"""
        if not tables:
            return
        with self._lock:
            listeners = list(self._listeners.values())
        for loop, deliver in listeners:
            try:
                loop.call_soon_threadsafe(deliver, frozenset(tables))
            except RuntimeError:
                # 正在退出的事件循环没有消费者；不得把已成功提交报告为失败。
                continue


def database_changes(path: str) -> ChangeNotifications:
    """同进程、同文件的多个 Database 句柄共享失效信号。"""
    with _HUBS_LOCK:
        hub = _HUBS.get(path)
        if hub is None:
            hub = ChangeNotifications()
            _HUBS[path] = hub
        return hub


def install_change_notifications(factory: sessionmaker, hub: ChangeNotifications) -> None:
    """监听 ORM 事务；嵌套提交暂存，外层提交才发布，回滚丢弃。"""

    @event.listens_for(factory, "before_flush")
    def collect(session, flush_context, instances):
        transaction = session.get_nested_transaction() or session.get_transaction()
        if transaction is None:
            return
        tables = {
            obj.__tablename__
            for obj in session.new | session.dirty | session.deleted
            if getattr(obj, "__tablename__", None) in OBSERVED_TABLES
            and (
                obj in session.new or obj in session.deleted or session.is_modified(obj)
            )
        }
        journal = session.info.setdefault(_JOURNAL, {})
        journal.setdefault(transaction, set()).update(tables)

    @event.listens_for(factory, "after_commit")
    def committed(session):
        transaction = session.get_nested_transaction() or session.get_transaction()
        journal = session.info.setdefault(_JOURNAL, {})
        tables = journal.pop(transaction, set())
        if transaction is not None and transaction.parent is not None:
            journal.setdefault(transaction.parent, set()).update(tables)
        else:
            journal.clear()
            hub.publish(tables)

    @event.listens_for(factory, "do_orm_execute")
    def collect_bulk(state):
        # Repository 的批量变体删除不进入 session.deleted，也必须覆盖。
        if not (state.is_insert or state.is_update or state.is_delete):
            return
        table = getattr(getattr(state.statement, "table", None), "name", None)
        if table not in OBSERVED_TABLES:
            return
        result = state.invoke_statement()
        session = state.session
        transaction = session.get_nested_transaction() or session.get_transaction()
        if transaction is not None:
            session.info.setdefault(_JOURNAL, {}).setdefault(transaction, set()).add(
                table
            )
        return result

    @event.listens_for(factory, "after_rollback")
    def rolled_back(session):
        transaction = session.get_nested_transaction() or session.get_transaction()
        journal = session.info.setdefault(_JOURNAL, {})
        journal.pop(transaction, None)
        if transaction is None or transaction.parent is None:
            journal.clear()
