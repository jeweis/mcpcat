"""mcpcat 核心实体 Repository。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import (
    APIKeyRecord,
    MCPServerRecord,
    MigrationHistoryRecord,
    SystemSettingRecord,
    utc_now,
)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _string_value(value: Any) -> str:
    """兼容 str Enum 与普通字符串。"""

    return str(getattr(value, "value", value))


class MCPServerRepository:
    """MCP 服务持久化操作。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_configs(self) -> Dict[str, Dict[str, Any]]:
        rows = self.session.scalars(select(MCPServerRecord)).all()
        return {row.name: deepcopy(row.config_json) for row in rows}

    def get_config(self, name: str) -> Optional[Dict[str, Any]]:
        row = self.session.get(MCPServerRecord, name)
        return deepcopy(row.config_json) if row else None

    def upsert(self, name: str, config: Dict[str, Any]) -> None:
        payload = deepcopy(config)
        row = self.session.get(MCPServerRecord, name)
        if row is None:
            row = MCPServerRecord(
                name=name,
                type=str(payload.get("type", "")),
                enabled=bool(payload.get("enabled", True)),
                config_json=payload,
            )
            self.session.add(row)
            return
        row.type = str(payload.get("type", ""))
        row.enabled = bool(payload.get("enabled", True))
        row.config_json = payload
        row.updated_at = utc_now()

    def remove(self, name: str) -> bool:
        row = self.session.get(MCPServerRecord, name)
        if row is None:
            return False
        self.session.delete(row)
        return True


class APIKeyRepository:
    """API Key 持久化操作。"""

    KNOWN_FIELDS = {
        "key",
        "name",
        "permission",
        "enabled",
        "created_at",
        "expires_at",
        "feishu_union_id",
        "feishu_open_id",
        "avatar_url",
        "source",
    }

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[Dict[str, Any]]:
        rows = self.session.scalars(
            select(APIKeyRecord).order_by(APIKeyRecord.id)
        ).all()
        return [self._to_dict(row) for row in rows]

    def get_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        row = self.session.scalar(select(APIKeyRecord).where(APIKeyRecord.key == key))
        return self._to_dict(row) if row else None

    def get_by_feishu_union_id(self, union_id: str) -> Optional[Dict[str, Any]]:
        row = self.session.scalar(
            select(APIKeyRecord).where(APIKeyRecord.feishu_union_id == union_id)
        )
        return self._to_dict(row) if row else None

    def replace_all(self, values: Iterable[Dict[str, Any]]) -> None:
        for row in self.session.scalars(select(APIKeyRecord)).all():
            self.session.delete(row)
        self.session.flush()
        for value in values:
            self.add(value)

    def add(self, value: Dict[str, Any]) -> APIKeyRecord:
        extra = {
            key: deepcopy(item)
            for key, item in value.items()
            if key not in self.KNOWN_FIELDS
        }
        row = APIKeyRecord(
            key=str(value["key"]),
            name=str(value["name"]),
            permission=_string_value(value["permission"]),
            enabled=bool(value.get("enabled", True)),
            created_at=_parse_datetime(value.get("created_at")),
            expires_at=_parse_datetime(value.get("expires_at")),
            feishu_union_id=value.get("feishu_union_id"),
            feishu_open_id=value.get("feishu_open_id"),
            avatar_url=value.get("avatar_url"),
            source=_string_value(value.get("source", "manual")),
            present_fields_json=sorted(self.KNOWN_FIELDS.intersection(value)),
            extra_json=extra,
        )
        self.session.add(row)
        return row

    def update(self, key: str, updates: Dict[str, Any]) -> bool:
        row = self.session.scalar(select(APIKeyRecord).where(APIKeyRecord.key == key))
        if row is None:
            return False
        current = self._to_dict(row)
        current.update(deepcopy(updates))
        extra = {
            name: item
            for name, item in current.items()
            if name not in self.KNOWN_FIELDS
        }
        for field in self.KNOWN_FIELDS - {"key"}:
            if field not in current:
                continue
            value = current[field]
            if field in {"created_at", "expires_at"}:
                value = _parse_datetime(value)
            elif field in {"permission", "source"}:
                value = _string_value(value)
            setattr(row, field, value)
        row.present_fields_json = sorted(self.KNOWN_FIELDS.intersection(current))
        row.extra_json = extra
        return True

    def remove(self, key: str) -> bool:
        row = self.session.scalar(select(APIKeyRecord).where(APIKeyRecord.key == key))
        if row is None:
            return False
        self.session.delete(row)
        return True

    @staticmethod
    def _to_dict(row: APIKeyRecord) -> Dict[str, Any]:
        result: Dict[str, Any] = deepcopy(row.extra_json or {})
        known_values = {
            "key": row.key,
            "name": row.name,
            "permission": row.permission,
            "enabled": row.enabled,
            "created_at": _serialize_datetime(row.created_at),
            "expires_at": _serialize_datetime(row.expires_at),
            "feishu_union_id": row.feishu_union_id,
            "feishu_open_id": row.feishu_open_id,
            "avatar_url": row.avatar_url,
            "source": row.source,
        }
        present_fields = set(row.present_fields_json or APIKeyRepository.KNOWN_FIELDS)
        result.update(
            {key: value for key, value in known_values.items() if key in present_fields}
        )
        return {key: value for key, value in result.items() if value is not None}


class SettingsRepository:
    """顶层配置段持久化操作。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> Dict[str, Any]:
        rows = self.session.scalars(select(SystemSettingRecord)).all()
        return {row.section: deepcopy(row.value_json) for row in rows}

    def get(self, section: str, default: Any = None) -> Any:
        row = self.session.get(SystemSettingRecord, section)
        return deepcopy(row.value_json) if row else deepcopy(default)

    def set(self, section: str, value: Any) -> None:
        row = self.session.get(SystemSettingRecord, section)
        if row is None:
            self.session.add(
                SystemSettingRecord(section=section, value_json=deepcopy(value))
            )
            return
        row.value_json = deepcopy(value)
        row.updated_at = utc_now()

    def remove(self, section: str) -> bool:
        row = self.session.get(SystemSettingRecord, section)
        if row is None:
            return False
        self.session.delete(row)
        return True


class MigrationHistoryRepository:
    """数据迁移历史持久化操作。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def find(
        self, migration_type: str, source_sha256: str
    ) -> Optional[MigrationHistoryRecord]:
        return self.session.scalar(
            select(MigrationHistoryRecord).where(
                MigrationHistoryRecord.migration_type == migration_type,
                MigrationHistoryRecord.source_sha256 == source_sha256,
            )
        )

    def start(
        self, migration_type: str, source_path: str, source_sha256: str
    ) -> MigrationHistoryRecord:
        row = MigrationHistoryRecord(
            migration_type=migration_type,
            source_path=source_path,
            source_sha256=source_sha256,
            status="running",
            counters_json={},
        )
        self.session.add(row)
        return row

    @staticmethod
    def complete(row: MigrationHistoryRecord, counters: Dict[str, Any]) -> None:
        row.status = "completed"
        row.counters_json = deepcopy(counters)
        row.error_message = None
        row.completed_at = utc_now()

    @staticmethod
    def fail(row: MigrationHistoryRecord, error_message: str) -> None:
        row.status = "failed"
        row.error_message = error_message
        row.completed_at = utc_now()
