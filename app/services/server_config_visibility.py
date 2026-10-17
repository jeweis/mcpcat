"""将管理员执行配置投影为只读描述，默认隐藏未知配置字段。"""

from copy import deepcopy
from typing import Any

_PUBLIC_CONFIG_FIELDS = frozenset(
    {
        "type",
        "name",
        "enabled",
        "timeout",
        "require_auth",
        "note",
        "tags",
        "expose_in_catalog",
    }
)


def reader_config(config: dict[str, Any]) -> dict[str, Any]:
    """仅保留显式公开的描述字段；URL/参数/扩展可能含秘密，不猜测名称。"""
    visible = {
        name: deepcopy(value)
        for name, value in config.items()
        if name in _PUBLIC_CONFIG_FIELDS
    }
    if "oauth" in config:
        visible["oauth"] = None
    return visible
