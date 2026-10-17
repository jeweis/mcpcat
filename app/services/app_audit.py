"""Apps 调用者的非秘密标识；不记录 Key、名称或外部用户资料。"""

import re
from collections.abc import Callable

from sqlalchemy import select

from app.storage.database import Database
from app.storage.models import APIKeyRecord


def app_audit_actor(database: Database, key: str) -> str:
    """读取现有本地记录 ID；不创建身份或把无效 Key 摘要写入日志。"""
    if not key:
        return "anonymous"
    with database.session_scope() as session:
        identifier = session.scalar(
            select(APIKeyRecord.id).where(APIKeyRecord.key == key)
        )
    return f"key:{identifier}" if identifier is not None else "unknown"


def safe_app_actor(principal: Callable[[], str] | None) -> str:
    """审计辅助失败不改变调用结果，回调也不能注入秘密或日志行。"""
    try:
        value = principal() if principal else "internal"
        if isinstance(value, str) and re.fullmatch(
            r"(?:key:[1-9][0-9]*|anonymous|unknown|internal)", value
        ):
            return value
    except Exception:
        pass
    return "unknown"
