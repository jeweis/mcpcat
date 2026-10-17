"""OAuth 授权流程服务 - 处理第三方 MCP 服务器的 OAuth 2.1 客户端认证"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from mcp.client.auth import PKCEParameters

from app.models.mcp_config import AuthStatus, OAuthConfig, OAuthToken
from app.services.oauth_authorization import (
    begin_authorization,
    consume_authorization,
    parse_callback_url,
)
from app.services.oauth_client_registration import register_oauth_client
from app.services.oauth_metadata import discover_oauth_endpoints
from app.services.oauth_probe import probe_mcp_auth
from app.services.oauth_tokens import request_oauth_token

logger = logging.getLogger(__name__)


class OAuthFlowService:
    """OAuth 授权流程管理服务"""

    @staticmethod
    async def inspect_auth_requirement(
        url: str,
        headers: Optional[Dict] = None,
        server_type: str = "streamable-http",
    ) -> Tuple[AuthStatus, Optional[Dict[str, Any]]]:
        """公开 SDK 协商后判定授权；保留 challenge 进行同次元数据发现。"""
        result = await probe_mcp_auth(url, headers, server_type)
        if result.challenge is None:
            return AuthStatus.NONE, None
        endpoints = await discover_oauth_endpoints(url, result.challenge)
        status = AuthStatus.AUTH_REQUIRED if endpoints else AuthStatus.AUTH_UNSUPPORTED
        return status, endpoints

    @staticmethod
    async def detect_auth_requirement(
        url: str, headers: Optional[Dict] = None
    ) -> AuthStatus:
        """保持旧方法返回值；连接/版本错误抛出安全诊断而非 NONE。"""
        status, _ = await OAuthFlowService.inspect_auth_requirement(url, headers)
        return status

    @staticmethod
    async def discover_endpoints(
        url: str,
        headers: Optional[Dict] = None,
        server_type: str = "streamable-http",
    ) -> Optional[Dict[str, Any]]:
        """兼容原授权入口；重取 challenge，不缓存其他来源/身份的元数据。"""
        result = await probe_mcp_auth(url, headers, server_type)
        return await discover_oauth_endpoints(url, result.challenge)

    @staticmethod
    async def dynamic_client_register(
        registration_endpoint: str, redirect_uris: list
    ) -> Optional[Tuple[str, Optional[str]]]:
        """
        动态客户端注册（RFC 7591）。

        Args:
            registration_endpoint: DCR 端点 URL
            redirect_uris: 允许的回调 URI 列表

        Returns:
            (client_id, client_secret) 元组，失败返回 None
        """
        if not redirect_uris:
            return None
        info = await register_oauth_client(
            OAuthConfig(registration_endpoint=registration_endpoint),
            redirect_uris[0],
            redirect_uris[1:],
        )
        return (info.client_id, info.client_secret) if info else None

    @staticmethod
    def generate_pkce() -> Tuple[str, str]:
        """复用 SDK 生成 PKCE，保持原 verifier/challenge 返回值。"""
        parameters = PKCEParameters.generate()
        return parameters.code_verifier, parameters.code_challenge

    @staticmethod
    def generate_authorize_url(
        server_name: str,
        oauth_config: OAuthConfig,
        public_base_url: Optional[str] = None,
        source_identity: object | None = None,
    ) -> Dict[str, Any]:
        """
        生成 OAuth 授权 URL。

        Args:
            server_name: 服务器名称
            oauth_config: OAuth 配置（需包含 authorization_endpoint）
            public_base_url: mcpcat 对外 URL（用于自动回调模式）

        Returns:
            包含 authorize_url / redirect_mode / redirect_uri 的字典
        """
        return begin_authorization(
            server_name, oauth_config, public_base_url, source_identity
        )

    @staticmethod
    async def exchange_code_for_token(
        server_name: str,
        code: str,
        state: str,
        *,
        issuer: str | None = None,
        source_identity: object | None = None,
        current_config: OAuthConfig | None = None,
        callback_url: str | None = None,
    ) -> Optional[OAuthToken]:
        """
        用授权码交换 token。

        Args:
            server_name: 服务器名称
            code: 授权码
            state: state 参数

        Returns:
            OAuthToken 实例，失败返回 None
        """
        entry = consume_authorization(
            server_name,
            state,
            issuer=issuer,
            source_identity=source_identity,
            current_config=current_config,
            callback_url=callback_url,
        )
        if not entry:
            return None

        return await request_oauth_token(
            entry["oauth_config"],
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": entry["code_verifier"],
                "redirect_uri": entry["redirect_uri"],
            },
        )

    @staticmethod
    async def refresh_oauth_token(
        server_name: str, oauth_config: OAuthConfig
    ) -> Optional[OAuthToken]:
        """
        使用 refresh_token 刷新 access_token。

        Args:
            server_name: 服务器名称
            oauth_config: OAuth 配置（需包含 token 和 refresh_token）

        Returns:
            新的 OAuthToken，失败返回 None
        """
        if not oauth_config.token or not oauth_config.token.refresh_token:
            return None

        return await request_oauth_token(
            oauth_config,
            {
                "grant_type": "refresh_token",
                "refresh_token": oauth_config.token.refresh_token,
            },
            oauth_config.token.refresh_token,
        )

    @staticmethod
    def handle_manual_callback(
        server_name: str, callback_url: str
    ) -> Optional[Tuple[str, str]]:
        """保持旧 code/state 元组；原 REST 另行校验 issuer 和 redirect。"""
        parsed = parse_callback_url(callback_url)
        return (parsed.code, parsed.state) if parsed else None

    @staticmethod
    def is_token_expired(token: OAuthToken) -> bool:
        """检查 token 是否已过期"""
        if not token.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(token.expires_at)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= expires
        except Exception:
            return True


oauth_flow_service = OAuthFlowService()
