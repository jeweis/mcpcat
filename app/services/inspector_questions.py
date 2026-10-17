"""将 SDK 用户交互转为有界展示数据，不访问上游提供的链接。"""

import json
import uuid
from typing import Any
from urllib.parse import urlsplit

MAX_INPUT_BYTES = 64 * 1024


def make_question(message: str, params: Any) -> dict | None:
    """校验表单/URL，仅返回当前用户可审阅的问题。"""
    mode = getattr(params, "mode", "form")
    question = {"id": str(uuid.uuid4()), "mode": mode, "message": message}
    if mode == "form":
        question["schema"] = params.requested_schema
    elif mode == "url":
        url = getattr(params, "url", "")
        if not isinstance(url, str) or len(url) > 8192:
            return None
        if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
            return None
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None):
                return None
            _ = parsed.port
        except ValueError:
            return None
        question.update(url=url, host=parsed.netloc)
    else:
        return None
    encoded = json.dumps(question)
    if len(encoded.encode()) > MAX_INPUT_BYTES:
        return None
    if mode == "form" and any(
        f'"{key}"' in encoded for key in ("$ref", "$dynamicRef", "$recursiveRef")
    ):
        return None
    return question
