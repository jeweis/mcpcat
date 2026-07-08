"""飞书 OpenAPI 适配层

只负责与飞书第三方 OpenAPI 交互（OAuth 换码、获取用户信息），
不感知 mcpcat 的账号/鉴权模型，通过统一的 FeishuAPIError 向上传递错误。
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib import error, request

logger = logging.getLogger(__name__)


class FeishuAPIError(Exception):
    """飞书 OpenAPI 调用过程中产生的统一错误"""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class FeishuUserInfo:
    """飞书用户身份信息"""
    union_id: str
    open_id: Optional[str]
    name: str
    avatar_url: Optional[str]


class FeishuAuthService:
    """飞书认证适配层，封装授权码换取用户身份的两步调用"""

    def exchange_code_v2(self, *, base_url: str, app_id: str, app_secret: str,
                         code: str, redirect_uri: Optional[str] = None) -> str:
        """用授权码换取 user_access_token"""
        payload: Dict[str, Any] = {
            "grant_type": "authorization_code",
            "client_id": app_id,
            "client_secret": app_secret,
            "code": code,
        }
        normalized_redirect_uri = (redirect_uri or "").strip()
        if normalized_redirect_uri:
            payload["redirect_uri"] = normalized_redirect_uri

        url = f"{base_url.rstrip('/')}/open-apis/authen/v2/oauth/token"
        data = self._post_json(url, payload)
        self._assert_success(data, error_code="FEISHU_TOKEN_EXCHANGE_FAILED")

        access_token = data.get("access_token")
        if not access_token or not isinstance(access_token, str):
            raise FeishuAPIError(
                code="FEISHU_TOKEN_EXCHANGE_FAILED",
                message="飞书换码成功但响应中缺少 access_token",
                details={"response": data},
            )
        return access_token

    def get_user_info(self, *, base_url: str, user_access_token: str) -> FeishuUserInfo:
        """获取飞书用户身份信息"""
        url = f"{base_url.rstrip('/')}/open-apis/authen/v1/user_info"
        data = self._get_json(
            url,
            headers={
                "Authorization": f"Bearer {user_access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        self._assert_success(data, error_code="FEISHU_USERINFO_FAILED")

        payload = data.get("data")
        if not isinstance(payload, dict):
            raise FeishuAPIError(
                code="FEISHU_USERINFO_FAILED",
                message="飞书用户信息响应格式无效",
                details={"response": data},
            )

        union_id = payload.get("union_id")
        if not isinstance(union_id, str) or not union_id:
            raise FeishuAPIError(
                code="FEISHU_UNION_ID_MISSING",
                message="飞书用户信息中缺少 union_id",
                details={"payload": payload},
            )

        name = payload.get("name")
        if not isinstance(name, str) or not name:
            name = "飞书用户"

        open_id = payload.get("open_id")
        avatar_url = payload.get("avatar_url")
        return FeishuUserInfo(
            union_id=union_id,
            open_id=open_id if isinstance(open_id, str) and open_id else None,
            name=name,
            avatar_url=avatar_url if isinstance(avatar_url, str) and avatar_url else None,
        )

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        return self._open_json(req)

    def _get_json(self, url: str, headers: Dict[str, str]) -> Dict[str, Any]:
        req = request.Request(url=url, headers=headers, method="GET")
        return self._open_json(req)

    def _open_json(self, req: request.Request) -> Dict[str, Any]:
        try:
            with request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise FeishuAPIError(
                code="FEISHU_HTTP_ERROR",
                message="调用飞书 API 时返回 HTTP 错误",
                details={"status": exc.code, "body": body, "url": req.full_url},
            ) from exc
        except Exception as exc:
            raise FeishuAPIError(
                code="FEISHU_HTTP_ERROR",
                message="调用飞书 API 失败",
                details={"reason": str(exc), "url": req.full_url},
            ) from exc

        try:
            parsed = json.loads(raw)
        except Exception as exc:
            raise FeishuAPIError(
                code="FEISHU_HTTP_ERROR",
                message="飞书 API 响应不是合法的 JSON",
                details={"raw": raw},
            ) from exc

        if not isinstance(parsed, dict):
            raise FeishuAPIError(
                code="FEISHU_HTTP_ERROR",
                message="飞书 API 响应格式不符合预期",
                details={"response": parsed},
            )
        return parsed

    def _assert_success(self, payload: Dict[str, Any], error_code: str) -> None:
        code = payload.get("code")
        if code == 0:
            return
        logger.warning(f"飞书 API 返回非零 code: {payload}")
        raise FeishuAPIError(
            code=error_code,
            message="飞书 API 返回了非成功状态码",
            details={"response": payload},
        )


feishu_auth_service = FeishuAuthService()
