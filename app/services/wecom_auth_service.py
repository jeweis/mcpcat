"""企业微信自建应用 Web 登录 OpenAPI 适配。"""

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from app.models.wecom import WeComConfig

API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
AUTHORIZE_URL = "https://login.work.weixin.qq.com/wwlogin/sso/login"
INVALID_TOKEN_CODES = {40014, 42001}


class WeComAPIError(Exception):
    """仅暴露固定错误说明与数值错误码，不包含 URL/Secret/Token。"""

    def __init__(
        self, code: str, message: str, upstream_code: Optional[int] = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.upstream_code = upstream_code


@dataclass(frozen=True)
class WeComUserInfo:
    """已确认属于自建应用所属企业的成员身份。"""

    user_id: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class WeComAuthService:
    """固定官方端点，按应用凭证缓存 access_token 并处理失效刷新。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[str, tuple[str, float]] = {}

    def authorize_url(self, config: WeComConfig, state: str) -> str:
        """构造企业自建应用新版 Web 登录链接。"""
        return (
            AUTHORIZE_URL
            + "?"
            + urlencode(
                {
                    "login_type": "CorpApp",
                    "appid": config.corp_id,
                    "agentid": config.agent_id,
                    "redirect_uri": config.redirect_uri,
                    "state": state,
                    "lang": "zh",
                }
            )
        )

    def _request(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            # 不允许重定向到非官方站点，也不把含 Secret 的请求 URL 写入日志。
            response = requests.get(
                f"{API_BASE}/{path}",
                params=params,
                timeout=10,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise WeComAPIError(
                    "WECOM_HTTP_ERROR", "企业微信服务暂时不可用，请稍后重试"
                )
            data = response.json()
        except (requests.RequestException, ValueError):
            raise WeComAPIError(
                "WECOM_HTTP_ERROR", "无法连接企业微信服务或响应无效"
            ) from None
        if not isinstance(data, dict):
            raise WeComAPIError("WECOM_RESPONSE_INVALID", "企业微信响应格式无效")
        if data.get("errcode") != 0:
            code = data.get("errcode")
            messages = {
                40029: "企业微信授权码无效，请重新登录",
                40013: "企业 ID 配置无效，请联系管理员",
                40001: "企业微信应用 Secret 无效，请联系管理员",
                60020: "后端出口 IP 不在企业可信 IP 范围内，请联系管理员",
                50001: "企业微信应用权限或可信域名配置不正确，请联系管理员",
            }
            raise WeComAPIError(
                "WECOM_API_ERROR",
                messages.get(code, "企业微信身份验证失败，请重试或联系管理员"),
                code if isinstance(code, int) else None,
            )
        return data

    def _access_token(self, config: WeComConfig, *, refresh: bool = False) -> str:
        cache_key = hashlib.sha256(
            f"{config.corp_id}:{config.agent_id}:{config.app_secret}".encode()
        ).hexdigest()
        with self._lock:
            cached = self._tokens.get(cache_key)
            if not refresh and cached and cached[1] > time.monotonic():
                return cached[0]
            data = self._request(
                "gettoken",
                {
                    "corpid": config.corp_id,
                    "corpsecret": config.app_secret,
                },
            )
            token = data.get("access_token")
            expires = data.get("expires_in")
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(expires, int)
                or expires <= 0
            ):
                raise WeComAPIError("WECOM_TOKEN_INVALID", "企业微信返回的应用凭证无效")
            self._tokens = {cache_key: (token, time.monotonic() + max(0, expires - 60))}
            return token

    def _authenticated_request(
        self, config: WeComConfig, path: str, params: dict
    ) -> dict:
        token = self._access_token(config)
        try:
            return self._request(path, {**params, "access_token": token})
        except WeComAPIError as error:
            if error.upstream_code not in INVALID_TOKEN_CODES:
                raise
        token = self._access_token(config, refresh=True)
        return self._request(path, {**params, "access_token": token})

    def exchange_code(self, config: WeComConfig, code: str) -> WeComUserInfo:
        """换取内部成员身份，拒绝只有 openid/外部联系人身份的响应。"""
        data = self._authenticated_request(config, "auth/getuserinfo", {"code": code})
        user_id = data.get("userid")
        if not isinstance(user_id, str) or not user_id.strip():
            raise WeComAPIError(
                "WECOM_MEMBER_REQUIRED", "仅允许本企业应用可见范围内的成员登录"
            )
        # 基础身份不依赖通讯录敏感字段；无权限读取姓名/头像时仍可登录。
        try:
            profile = self._authenticated_request(
                config, "user/get", {"userid": user_id}
            )
        except WeComAPIError:
            profile = {}
        name, avatar = profile.get("name"), profile.get("avatar")
        return WeComUserInfo(
            user_id=user_id,
            name=name if isinstance(name, str) and name.strip() else None,
            avatar_url=(
                avatar
                if isinstance(avatar, str) and avatar.startswith("https://")
                else None
            ),
        )


wecom_auth_service = WeComAuthService()
