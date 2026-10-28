"""Apps 新表与上传纯静态校验的兼容、安全测试。"""

import io
import json
import zipfile

import pytest
from sqlalchemy import inspect, text

from alembic import command
from app.core.config import settings
from app.services.app_package_validator import (
    validate_app_package,
    validate_prefab_json,
)
from app.services.app_registry_service import read_app_artifact, upload_app
from app.storage.database import Database
from app.storage.migrations import create_alembic_config, upgrade_schema
from app.storage.unit_of_work import UnitOfWork


def test_upgrade_existing_database_preserves_old_rows(tmp_path, monkeypatch):
    """从真实旧 revision 增量升级，未知配置与旧业务数据不改写。"""
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    database = Database(tmp_path / "old.db")
    command.upgrade(create_alembic_config(database.path), "0002_skills_registry")
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO system_settings(section, value_json, updated_at) "
                "VALUES ('fixture', '{\"unknown\": 7}', CURRENT_TIMESTAMP)"
            )
        )
    assert upgrade_schema(database).current == "0006_wecom_login"
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT value_json FROM system_settings WHERE section='fixture'")
            )
            == '{"unknown": 7}'
        )
    assert {"apps", "app_versions", "app_artifacts", "app_tool_bindings"}.issubset(
        inspect(database.engine).get_table_names()
    )
    assert upgrade_schema(database).current == "0006_wecom_login"
    assert "mcpcat_apps_enabled" not in type(settings).model_fields
    database.dispose()


def test_app_defaults_and_published_binding_freeze(tmp_path):
    database = Database(tmp_path / "apps.db")
    upgrade_schema(database)
    with UnitOfWork(database) as unit:
        app = unit.apps.create(slug="counter", display_name="Counter")
        version = unit.apps.create_version(
            app_id=app.id,
            version="1.0.0",
            content_format="prefab",
            entrypoint="app.json",
            manifest={},
        )
        unit.apps.bind_tool(
            version_id=version.id,
            alias="count",
            server_name="fixture",
            tool_name="get_count",
        )
        assert app.status == version.status == "draft"
        assert app.visibility == "private"
        version.status = "published"
        unit.commit()
        with pytest.raises(ValueError, match="草稿"):
            unit.apps.bind_tool(
                version_id=version.id,
                alias="other",
                server_name="fixture",
                tool_name="write",
            )
    database.dispose()


def test_prefab_and_html_preserve_original_bytes():
    wire = {"view": {"type": "Heading", "content": "Dashboard"}, "state": {"count": 7}}
    content = json.dumps(wire).encode()
    result = validate_app_package(content, filename="dashboard.json")
    assert result.original_content == result.files["app.json"] == content
    assert result.content_format == "prefab"
    html = b"<html><script>window.test = 1</script></html>\r\n"
    result = validate_app_package(html, filename="index.html")
    assert result.original_content == result.files["index.html"] == html


@pytest.mark.parametrize(
    "content",
    [
        b'{"view": {"type": "Unknown"}}',
        b'{"view": {"type": "Heading"}}',
        b'{"view": {}, "version": "future"}',
        b'{"view": {}, "view": {}}',
        b'{"view": {}, "state": {"__proto__": {}}}',
        b'{"view": {}, "state": {"count": NaN}}',
    ],
)
def test_prefab_invalid_definitions_are_rejected(content):
    with pytest.raises(ValueError):
        validate_prefab_json(content)


@pytest.mark.parametrize(
    "path",
    [
        "../index.html",
        "/index.html",
        "C:/index.html",
        "dir\\index.html",
        "dir/./index.html",
    ],
)
def test_zip_path_traversal_rejected(path):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(path, "<html>Test</html>")
    with pytest.raises(ValueError, match="路径"):
        validate_app_package(output.getvalue(), filename="test.zip")


def test_upload_does_not_execute_python_attachment(tmp_path):
    marker = tmp_path / "must-not-exist"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("index.html", "<html>Test</html>")
        archive.writestr(
            "attachment.py", f"open({str(marker)!r}, 'w').write('executed')"
        )
    result = validate_app_package(output.getvalue(), filename="test.zip")
    assert "attachment.py" in result.files
    assert not marker.exists()


def test_upload_artifact_is_immutable_and_checked(tmp_path):
    database = Database(tmp_path / "upload.db")
    upgrade_schema(database)
    root = tmp_path / "artifacts"
    html = b"<html>Original</html>\r\n"
    version_id = upload_app(
        database,
        slug="test-app",
        display_name="Test",
        version="1.0.0",
        content=html,
        filename="index.html",
        artifact_root=root,
    )
    assert (
        read_app_artifact(database, version_id=version_id, artifact_root=root) == html
    )
    with pytest.raises(ValueError, match="已存在"):
        upload_app(
            database,
            slug="test-app",
            display_name="Test",
            version="1.0.0",
            content=b"Replacement",
            filename="index.html",
            artifact_root=root,
        )
    assert (
        read_app_artifact(database, version_id=version_id, artifact_root=root) == html
    )
    path = next(root.rglob("*.package"))
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="完整性"):
        read_app_artifact(database, version_id=version_id, artifact_root=root)
    database.dispose()


def test_prefab_state_type_is_not_a_component():
    wire = {
        "view": {"type": "Heading", "content": "Test"},
        "state": {"record": {"type": "business-data"}},
    }
    assert validate_prefab_json(json.dumps(wire).encode()) == wire
