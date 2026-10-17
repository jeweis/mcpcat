"""将 Sampling 请求交给测试者，不调用模型、不执行返回的工具指令。"""

import json
import uuid
from typing import Any

from mcp.types import CreateMessageResult, CreateMessageResultWithTools
from pydantic import ValidationError

MAX_BYTES = 64 * 1024


def sampling_question(params: Any) -> dict:
    """完整展示有界请求，超限拒绝，不截断后假装已审阅。"""
    request = params.model_dump(mode="json", by_alias=True, exclude_none=True)
    text = json.dumps(request, ensure_ascii=False)
    if len(text.encode()) > MAX_BYTES:
        raise ValueError("Sampling 请求超过测试台 64 KiB 限制")
    return {
        "id": str(uuid.uuid4()), "mode": "sampling",
        "message": "模拟 Sampling 响应：以下为服务请求，不是执行指令。\n" + text,
        "with_tools": bool(getattr(params, "tools", None)),
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"response_json": {
                "type": "string", "maxLength": MAX_BYTES,
                "description": '填写 MCP 响应 JSON，例如 {"role":"assistant",'
                '"model":"manual-test","content":{"type":"text","text":"示例"}}',
            }},
            "required": ["response_json"],
        },
    }


def sampling_answer(question: dict, content: dict | None) -> Any:
    """用 SDK 结果模型验证手工响应；不执行工具或访问媒体地址。"""
    if not isinstance(content, dict) or set(content) != {"response_json"}:
        raise ValueError("请提供 response_json")
    raw = content["response_json"]
    if not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES:
        raise ValueError("Sampling 响应超过限制或类型错误")
    model = CreateMessageResultWithTools if question["with_tools"] else CreateMessageResult
    try:
        return model.model_validate(json.loads(raw))
    except (ValueError, ValidationError, RecursionError) as error:
        raise ValueError("Sampling 响应不是有效的 MCP 结果 JSON") from error
