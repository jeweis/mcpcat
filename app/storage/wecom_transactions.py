"""持久化、绑定浏览器且只能原子消费一次的登录 state/票据。"""

import hashlib
import secrets
import time
from typing import Any, Optional

from sqlalchemy import JSON, Float, String, delete
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Database
from app.storage.models import Base


class WeComLoginTransaction(Base):
    """只存随机凭证摘要，票据中不保存第三方访问凭证。"""

    __tablename__ = "wecom_login_transactions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


def digest(value: str) -> str:
    """随机凭证仅以 SHA-256 摘要用于数据库匹配。"""
    return hashlib.sha256(value.encode()).hexdigest()


class WeComTransactions:
    """通过 SQLite DELETE RETURNING 防止重复回调和并发重放。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    def issue(self, kind: str, browser: str, payload: dict, ttl: int) -> str:
        """签发有有效期的一次性凭证，并清理过期记录。"""
        token = secrets.token_urlsafe(32)
        with self.database.session_scope() as session:
            session.execute(
                delete(WeComLoginTransaction).where(
                    WeComLoginTransaction.expires_at <= time.time()
                )
            )
            session.add(
                WeComLoginTransaction(
                    token_hash=digest(token),
                    browser_hash=digest(browser),
                    kind=kind,
                    expires_at=time.time() + ttl,
                    payload_json=payload,
                )
            )
        return token

    def consume(self, kind: str, token: str, browser: str) -> Optional[dict]:
        """只有原浏览器且尚未过期时才原子删除并返回载荷。"""
        if not token or not browser:
            return None
        with self.database.session_scope() as session:
            return session.execute(
                delete(WeComLoginTransaction)
                .where(
                    WeComLoginTransaction.token_hash == digest(token),
                    WeComLoginTransaction.browser_hash == digest(browser),
                    WeComLoginTransaction.kind == kind,
                    WeComLoginTransaction.expires_at > time.time(),
                )
                .returning(WeComLoginTransaction.payload_json)
            ).scalar_one_or_none()
