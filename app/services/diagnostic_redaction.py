"""诊断文本脱敏；保留异常类型与调用链，不保存认证容器。"""

import json
import re
from urllib.parse import quote, urlsplit, urlunsplit

SECRET_NAME = re.compile(
    r"key|secret|token|password|authorization|cookie|credential", re.I
)


def secret_values(value: object, private: bool = False) -> set[str]:
    """收集配置中的凭证和 env/headers 容器值，不返回整个配置。"""
    if isinstance(value, dict):
        result: set[str] = set()
        for key, item in value.items():
            hidden = private or bool(SECRET_NAME.search(str(key)))
            result.update(secret_values(item, hidden or key in {"env", "headers"}))
        return result
    if isinstance(value, list):
        return set().union(*(secret_values(item, private) for item in value))
    return {value} if private and isinstance(value, str) and len(value) >= 4 else set()


def _safe_url(match: re.Match) -> str:
    """URL 的认证部分、查询参数与 fragment 不进入诊断内容。"""
    try:
        url = urlsplit(match.group())
        host = url.netloc.rsplit("@", 1)[-1]
        return urlunsplit(
            (url.scheme, host, url.path, "[REDACTED]" if url.query else "", "")
        )
    except ValueError:
        return "[REDACTED_URL]"


def redact(text: str, secrets: set[str]) -> str:
    """在保留排障文本前移除已知秘密及常见凭证格式。"""
    for secret in sorted(secrets, key=len, reverse=True):
        for variant in {secret, quote(secret, safe=""), json.dumps(secret)[1:-1]}:
            text = text.replace(variant, "[REDACTED]")
    text = re.sub(r"https?://[^\s\"'<>]+", _safe_url, text)
    text = re.sub(
        r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=][^\r\n]+",
        r"\1: [REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(?<![\w-])([\"']?(?:[\w-]{0,64}(?:api[_-]?key|secret|token|password|credential)[\w-]{0,32})"
        r"[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+[^\s\"',;}]+", "Bearer [REDACTED]", text)
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
