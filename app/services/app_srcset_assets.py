"""响应式图片候选装配；逗号不能直接分割包含 data URI 的地址。"""

import math
import re
from collections.abc import Iterator

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_content_service import AppDomainError

SPACE = "\t\n\f\r "
FLOAT = re.compile(r"-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _descriptor(value: str) -> str:
    fields: dict[str, str] = {}
    for part in re.split(r"[\t\n\f\r ]+", value) if value else []:
        suffix, number = part[-1:], part[:-1]
        if suffix not in {"w", "h", "x"} or suffix in fields:
            raise AppDomainError("App srcset 描述符无效")
        if suffix in {"w", "h"}:
            valid = bool(re.fullmatch(r"[0-9]+", number)) and int(number) > 0
        else:
            valid = bool(FLOAT.fullmatch(number))
            valid = valid and math.isfinite(float(number)) and float(number) >= 0
        if not valid:
            raise AppDomainError("App srcset 描述符无效")
        fields[suffix] = part
    if ("h" in fields and "w" not in fields) or ("x" in fields and len(fields) > 1):
        raise AppDomainError("App srcset 描述符组合无效")
    return " ".join(fields.values())


def _candidates(value: str) -> Iterator[tuple[str, str]]:
    position, count = 0, 0
    while position < len(value):
        while position < len(value) and value[position] in SPACE + ",":
            position += 1
        if position == len(value):
            return
        start = position
        while position < len(value) and value[position] not in SPACE:
            position += 1
        token = value[start:position]
        url = token.rstrip(",")
        description = ""
        if not token.endswith(","):
            start = position
            while position < len(value) and value[position] != ",":
                position += 1
            description = _descriptor(value[start:position].strip(SPACE))
        if not url:
            raise AppDomainError("App srcset 缺少候选地址")
        count += 1
        if count > 512:
            raise AppDomainError("App srcset 候选超过数量限制")
        yield url, description


def assemble_app_srcset(value: str, *, resolver: AppAssetResolver, base: str) -> str:
    """每个候选复用相同路径/审核/容量检查，保留浏览器选择职责。"""
    return ", ".join(
        resolver.data_uri(url, base) + (" " + description if description else "")
        for url, description in _candidates(value)
    )
