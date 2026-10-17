"""稳定来源身份，不依赖公网地址或可覆盖的旧配置段。"""

from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.storage.database import Database
from app.storage.skill_variant_models import RuntimeIdentityRecord


def ensure_runtime_identity(database: Database, key: str) -> str:
    """并发登记时保留已有 UUID；不会因访问地址改变重建身份。"""
    if not key or len(key) > 255 or "\x00" in key:
        raise ValueError("运行身份 key 无效")
    with database.session_scope() as session:
        session.execute(
            insert(RuntimeIdentityRecord)
            .values(
                key=key,
                value=str(uuid4()),
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
        return session.get(RuntimeIdentityRecord, key).value


def instance_identity(database: Database) -> str:
    """读取本实例稳定身份。"""
    return ensure_runtime_identity(database, "instance")


def endpoint_identity(database: Database, kind: str, source: str = "") -> str:
    """为稳定端点角色/来源登记 UUID，调用者不能用 public URL 代替身份。"""
    if kind not in {"service", "catalog", "skills", "apps", "code-mode"}:
        raise ValueError("不支持的端点身份类型")
    if kind == "service" and not source:
        raise ValueError("服务端点必须指定来源")
    return ensure_runtime_identity(
        database, f"endpoint:{kind}" + (f":{source}" if source else "")
    )


def retire_service_identity(session: Session, source: str) -> None:
    """与来源删除同一事务退休身份；重建同名服务不能复用旧 URI/binding。"""
    session.execute(
        delete(RuntimeIdentityRecord).where(
            RuntimeIdentityRecord.key == f"endpoint:service:{source}"
        )
    )


def current_endpoint_identity(
    database: Database, kind: str, source: str = ""
) -> str | None:
    """请求只读取当前身份，不能因访问退休 URI 重新创建来源身份。"""
    key = f"endpoint:{kind}" + (f":{source}" if source else "")
    with database.session_scope() as session:
        row = session.get(RuntimeIdentityRecord, key)
        return row.value if row else None
