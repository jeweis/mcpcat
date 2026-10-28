"""企业微信配置及受管 API Key 账号，复用既有 SecurityService。"""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.mcp_config import APIKeyConfig
from app.models.wecom import WeComConfig
from app.services.config_service import ConfigService
from app.services.security_service import SecurityService, security_service
from app.services.wecom_auth_service import WeComUserInfo
from app.storage.models import APIKeyRecord
from app.storage.unit_of_work import UnitOfWork


class WeComAccountService:
    """单企业配置，成员身份按 CorpID + UserID 唯一绑定。"""

    def __init__(self, security: SecurityService = security_service) -> None:
        self.security = security

    def config(self) -> WeComConfig:
        """获取内部配置，解密应用 Secret。"""
        data = dict(ConfigService.get_setting_section("wecom", {}) or {})
        data["app_secret"] = self.security._decrypt_secret(data.get("app_secret")) or ""
        return WeComConfig(**data)

    def update_settings(self, updates: dict) -> WeComConfig:
        """验证合并后的配置并加密保存；空白 Secret 保持已有值。"""
        data = self.config().model_dump()
        updates = {key: value for key, value in updates.items() if value is not None}
        if not str(updates.get("app_secret", "")).strip():
            updates.pop("app_secret", None)
        data.update(updates)
        config = WeComConfig(**data)
        if config.enabled and not all(
            (config.corp_id, config.agent_id, config.app_secret, config.redirect_uri)
        ):
            raise ValueError("启用企业微信登录需要完整配置")
        stored = config.model_dump(mode="json")
        stored["app_secret"] = (
            self.security._encrypt_secret(config.app_secret)
            if config.app_secret
            else None
        )
        if not ConfigService.update_setting_section("wecom", stored):
            raise RuntimeError("企业微信设置保存失败")
        return config

    def find_or_create(
        self, config: WeComConfig, user: WeComUserInfo
    ) -> tuple[APIKeyConfig, bool]:
        """并发首登依赖数据库唯一约束；老成员只刷新展示资料。"""
        try:
            with UnitOfWork(ConfigService._require_database()) as unit:
                existing = unit.api_keys.get_by_wecom_identity(
                    config.corp_id, user.user_id
                )
                if existing is not None:
                    updates = {}
                    if user.name:
                        updates["name"] = user.name
                    if user.avatar_url:
                        updates["avatar_url"] = user.avatar_url
                    unit.api_keys.update(existing["key"], updates)
                    unit.commit()
                    return APIKeyConfig(**{**existing, **updates}), False
                key = APIKeyConfig(
                    key=self.security.generate_api_key(),
                    name=user.name or user.user_id,
                    permission=config.default_permission,
                    created_at=datetime.now(),
                    source="wecom",
                    wecom_corp_id=config.corp_id,
                    wecom_user_id=user.user_id,
                    avatar_url=user.avatar_url,
                )
                unit.api_keys.add(key.model_dump())
                unit.commit()
                return key, True
        except IntegrityError:
            # 另一请求已完成同一身份的创建，复用其权限、启用状态与 Key。
            with UnitOfWork(ConfigService._require_database()) as unit:
                existing = unit.api_keys.get_by_wecom_identity(
                    config.corp_id, user.user_id
                )
            if existing is None:
                raise
            return APIKeyConfig(**existing), False

    def list_members(self) -> list[dict]:
        """返回全部企微成员（含历史企业），不暴露 Key。"""
        with UnitOfWork(ConfigService._require_database()) as unit:
            rows = unit.session.scalars(
                select(APIKeyRecord)
                .where(APIKeyRecord.source == "wecom")
                .order_by(APIKeyRecord.id)
            ).all()
            return [self._member(row) for row in rows]

    def update_member(self, record_id: int, updates: dict) -> Optional[dict]:
        """按稳定账号 ID 更新权限/启用状态，不能修改其他来源账号。"""
        with UnitOfWork(ConfigService._require_database()) as unit:
            row = unit.session.get(APIKeyRecord, record_id)
            if row is None or row.source != "wecom":
                return None
            unit.api_keys.update(row.key, updates)
            unit.commit()
            unit.session.refresh(row)
            return self._member(row)

    @staticmethod
    def _member(row: APIKeyRecord) -> dict:
        return {
            "id": row.id,
            "corp_id": row.wecom_corp_id,
            "user_id": row.wecom_user_id,
            "name": row.name,
            "avatar_url": row.avatar_url,
            "permission": row.permission,
            "enabled": row.enabled,
            "created_at": row.created_at,
        }


wecom_account_service = WeComAccountService()
