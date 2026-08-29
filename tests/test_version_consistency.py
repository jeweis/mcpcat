"""发布版本一致性测试。"""

import json
import tomllib
from pathlib import Path

from app.version import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]


def test_python_package_matches_runtime_version() -> None:
    """Python 包元数据必须与运行时报告版本一致。"""

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == APP_VERSION


def test_default_compose_versions_match_runtime() -> None:
    """官方 Compose 模板不得注入不同的应用版本。"""

    for filename in ("docker-compose.yml", "docker-compose-fpk.yml"):
        content = (ROOT / filename).read_text(encoding="utf-8")
        assert f"APP_VERSION={APP_VERSION}" in content


def test_embedded_frontend_matches_runtime_version() -> None:
    """Docker 内置的 Flutter 构建必须属于同一产品版本。"""

    metadata = json.loads(
        (ROOT / "static" / "version.json").read_text(encoding="utf-8")
    )
    assert metadata["version"] == APP_VERSION
