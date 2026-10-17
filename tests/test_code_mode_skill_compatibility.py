"""CodeMode 配置/快照恢复不切换既有 local/remote Skill 模板或绑定。"""

from pathlib import Path

import pytest
from test_mcp_skill_generator import (
    _catalog_manager,
    _manager,
)
from test_mcp_skill_generator import database as _database_fixture
from test_mcp_skill_generator import runtime_patches as _runtime_fixture

from app.services import mcp_skill_generator as generator
from app.services.code_mode_settings import code_mode_config
from app.services.config_service import ConfigService
from app.services.skill_content_service import load_variant
from app.services.skill_variant_publication import publish_content_version
from app.storage.database import Database
from app.storage.maintenance import backup_database
from app.storage.runtime_identities import endpoint_identity

database = _database_fixture
runtime_patches = _runtime_fixture

CONFIG = {
    "enabled": True,
    "allowed_tools": [],
    "limits": {"max_tool_calls": 3},
    "unknown": {"keep": True},
}


@pytest.mark.parametrize("kind", ["service", "catalog"])
async def test_config_restore_does_not_rebind_existing_skills(
    database: Database, runtime_patches: None, tmp_path: Path, kind: str
) -> None:
    """新开关开/关与恢复均保持同一生成快照、原字节及来源端点。"""
    ConfigService.configure_database(database)
    restored = None
    manager = _manager() if kind == "service" else _catalog_manager()
    try:
        before = ConfigService.load_raw_config()
        assert "enabled" not in code_mode_config().model_dump()
        assert ConfigService.load_raw_config() == before
        first = await generate(database, manager, kind)
        publish_content_version(database, slug=first.slug, version=first.version)
        binding = endpoint_identity(
            database, kind, "Weather / China" if kind == "service" else None
        )
        original = contents(database, first, binding)
        restored = restore_config(database, tmp_path)
        for enabled in (True, False):
            assert ConfigService.update_setting_section(
                "code_mode", {**CONFIG, "enabled": enabled}
            )
            again = await generate(restored, manager, kind)
            assert not again.changed and again.version_id == first.version_id
            assert contents(restored, first, binding) == original
    finally:
        ConfigService.reset_database()
        if restored is not None:
            restored.dispose()


def restore_config(database: Database, directory: Path) -> Database:
    """备份后修改原库，再从备份恢复未知字段与非默认限额。"""
    assert ConfigService.update_setting_section("code_mode", CONFIG)
    backup = backup_database(database.path, directory / "snapshots")
    assert backup is not None
    assert ConfigService.update_setting_section("code_mode", {"enabled": False})
    restored = Database(backup)
    ConfigService.configure_database(restored)
    assert ConfigService.get_setting_section("code_mode") == CONFIG
    assert code_mode_config().limits.max_tool_calls == 3
    return restored


async def generate(
    database: Database, manager: object, kind: str
) -> generator.GeneratedSkillResult:
    """只调用原生成器，不使用 CodeMode 工具生成第三套模板。"""
    options = {"manager": manager, "actor": "fixture", "selection": "both"}
    if kind == "service":
        return await generator.generate_mcp_skill(
            database, server_name="Weather / China", **options
        )
    return await generator.generate_catalog_skill(database, **options)


def contents(
    database: Database, result: generator.GeneratedSkillResult, binding: str
) -> list[tuple[dict, bytes]]:
    """逐模式比较完整元数据及 ZIP 原字节，不能只比版本标签。"""
    return [
        load_variant(
            database,
            slug=result.slug,
            version=result.version,
            profile=profile,
            binding=binding if profile == "remote" else "",
        )
        for profile in ("local", "remote")
    ]
