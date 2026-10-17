"""CodeMode 配置/依赖/路径检查，不启动沙箱或改变旧配置。"""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from app.models.code_mode_config import CodeModeConfig
from app.services.config_service import ConfigService

CODE_MODE_DEPENDENCIES = {"fastmcp": "4.1.0", "pydantic-monty": "1.1.0"}


def code_mode_config() -> CodeModeConfig:
    """缺失采用有限默认值，读取不写原配置；非法限制必须报错。"""
    return CodeModeConfig.model_validate(
        ConfigService.get_setting_section("code_mode", {}) or {}
    )


def code_mode_dependency_versions() -> dict[str, str | None]:
    """报告固定依赖的实际安装版本，不隐式安装或升级。"""
    versions = {}
    for name in CODE_MODE_DEPENDENCIES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    return versions


def validate_code_mode_dependencies() -> None:
    """启用需要审核过的固定组合，漂移时拒绝保存启用配置。"""
    actual = code_mode_dependency_versions()
    if actual != CODE_MODE_DEPENDENCIES:
        raise ValueError("CodeMode 需要 fastmcp==4.1.0 和 pydantic-monty==1.1.0")


def validate_code_mode_path(config: CodeModeConfig, manager: Any) -> None:
    """检查配置归属，不覆盖普通服务、Catalog、Skills 或 Apps。"""
    from app.services.platform_endpoints import validate_platform_path

    validate_platform_path("code_mode", config.path_name, manager)


def code_mode_path_reserved(name: str) -> bool:
    """已保存启用但尚待挂载的系统路径也不能交给普通用户服务。"""
    config = code_mode_config()
    return config.path_name == name
