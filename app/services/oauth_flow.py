"""OAuth 授权流程服务 - 处理第三方 MCP 服务器的 OAuth 2.1 客户端认证"""

import logging
import secrets
import time
import hashlib
import base64
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from app.models.mcp_config import OAuthConfig, OAuthToken, AuthStatus

logger = logging.getLogger(__name__)

# 临时授权状态：server_name → {code_verifier, state, created_at, oauth_config}
_auth_states: Dict[str, Dict[str, Any]] = {}
_STATE_TTL = 300  # 5 分钟


class OAuthFlowService:
    """OAuth 授权流程管理服务"""

    @staticmethod
    async def detect_auth_requirement(url: str, headers: Optional[Dict] = None) -> AuthStatus:
        """
        检测第三方 MCP 服务器是否需要 OAuth 授权。

        先尝试无认证 initialize，401 时检查 well-known 端点。

        Args:
            url: 第三方 MCP 服务器 URL
            headers: 可选的已有 headers

        Returns:
            AuthStatus: none / auth_required / auth_unsupported
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # 尝试无认证 initialize 请求
                resp = await client.post(
                    url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "mcpcat", "version": "0.1.1"},
                        },
                    },
                    headers=headers or {},
                )

                if resp.status_code == 200:
                    return AuthStatus.NONE

                if resp.status_code == 401:
                    # 检查 OAuth 端点发现
                    endpoints = await OAuthFlowService.discover_endpoints(url)
                    if endpoints and endpoints.get("authorization_endpoint"):
                        return AuthStatus.AUTH_REQUIRED
                    return AuthStatus.AUTH_UNSUPPORTED

                # 其他状态码视为无需认证（可能是其他错误）
                logger.warning(f"检测认证需求时返回非预期状态码: {resp.status_code}")
                return AuthStatus.NONE

        except Exception as e:
            logger.warning(f"检测认证需求失败 {url}: {e}")
            # 连接失败不一定是认证问题，返回 none 让正常加载流程处理
            return AuthStatus.NONE

    @staticmethod
    async def discover_endpoints(url: str) -> Optional[Dict[str, Any]]:
        """
        发现 OAuth 端点元数据。

        GET {url}/.well-known/oauth-authorization-server

        Returns:
            包含 authorization_endpoint / token_endpoint / registration_endpoint / scopes_supported 的字典，失败返回 None
        """
        # 从服务器 URL 提取 base URL
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        well_known_url = f"{base_url}/.well-known/oauth-authorization-server"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(well_known_url)
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "authorization_endpoint": data.get("authorization_endpoint"),
                        "token_endpoint": data.get("token_endpoint"),
                        "registration_endpoint": data.get("registration_endpoint"),
                        "scopes_supported": data.get("scopes_supported", []),
                    }
                logger.info(f"OAuth 端点发现返回 {resp.status_code}: {well_known_url}")
                return None
        except Exception as e:
            logger.info(f"OAuth 端点发现失败 {well_known_url}: {e}")
            return None

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
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    registration_endpoint,
                    json={
                        "client_name": "mcpcat",
                        "redirect_uris": redirect_uris,
                        "grant_types": ["authorization_code", "refresh_token"],
                        "response_types": ["code"],
                        "token_endpoint_auth_method": "client_secret_basic",
                    },
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    return data.get("client_id"), data.get("client_secret")
                logger.warning(f"DCR 注册返回 {resp.status_code}: {resp.text}")
                return None
        except Exception as e:
            logger.warning(f"DCR 注册失败: {e}")
            return None

    @staticmethod
    def generate_pkce() -> Tuple[str, str]:
        """
        生成 PKCE code_verifier 和 code_challenge。

        Returns:
            (code_verifier, code_challenge) 元组
        """
        code_verifier = secrets.token_urlsafe(64)[:128]
        challenge_bytes = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(challenge_bytes).decode("ascii").rstrip("=")
        return code_verifier, code_challenge

    @staticmethod
    def generate_authorize_url(
        server_name: str,
        oauth_config: OAuthConfig,
        public_base_url: Optional[str] = None,
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
        code_verifier, code_challenge = OAuthFlowService.generate_pkce()
        state = f"{server_name}:{secrets.token_urlsafe(16)}"

        # 确定回调模式和 redirect_uri
        redirect_mode = oauth_config.redirect_mode
        if redirect_mode == "auto" and public_base_url:
            redirect_uri = f"{public_base_url.rstrip('/')}/api/oauth/callback/{server_name}"
        else:
            redirect_mode = "manual"
            redirect_uri = f"http://localhost:8765/callback"

        # 存入临时状态
        _auth_states[server_name] = {
            "code_verifier": code_verifier,
            "state": state,
            "created_at": time.time(),
            "oauth_config": oauth_config,
            "redirect_uri": redirect_uri,
        }

        # 拼接授权 URL
        params = {
            "response_type": "code",
            "client_id": oauth_config.client_id or "",
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if oauth_config.scopes:
            params["scope"] = " ".join(oauth_config.scopes)

        authorize_url = (
            f"{oauth_config.authorization_endpoint}?{urlencode(params)}"
            if oauth_config.authorization_endpoint
            else ""
        )

        return {
            "authorize_url": authorize_url,
            "redirect_mode": redirect_mode,
            "redirect_uri": redirect_uri,
            "state": state,
        }

    @staticmethod
    def _get_and_clear_state(server_name: str, state: str) -> Optional[Dict[str, Any]]:
        """校验并取出临时状态"""
        entry = _auth_states.get(server_name)
        if not entry:
            logger.warning(f"未找到授权状态: {server_name}")
            return None

        if entry["state"] != state:
            logger.warning(f"State 不匹配: 期望={entry['state']}, 实际={state}")
            return None

        if time.time() - entry["created_at"] > _STATE_TTL:
            _auth_states.pop(server_name, None)
            logger.warning(f"授权状态已过期: {server_name}")
            return None

        _auth_states.pop(server_name, None)
        return entry

    @staticmethod
    async def exchange_code_for_token(
        server_name: str, code: str, state: str
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
        entry = OAuthFlowService._get_and_clear_state(server_name, state)
        if not entry:
            return None

        oauth_config: OAuthConfig = entry["oauth_config"]
        code_verifier = entry["code_verifier"]
        redirect_uri = entry["redirect_uri"]

        if not oauth_config.token_endpoint:
            logger.error(f"缺少 token_endpoint: {server_name}")
            return None

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "client_id": oauth_config.client_id or "",
        }
        if oauth_config.client_secret:
            data["client_secret"] = oauth_config.client_secret

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    oauth_config.token_endpoint,
                    data=data,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code == 200:
                    token_data = resp.json()
                    expires_in = token_data.get("expires_in")
                    expires_at = None
                    if expires_in:
                        expires_at = (
                            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                        ).isoformat()

                    return OAuthToken(
                        access_token=token_data["access_token"],
                        refresh_token=token_data.get("refresh_token"),
                        expires_at=expires_at,
                        token_type=token_data.get("token_type", "Bearer"),
                    )
                logger.error(f"Token 交换失败 {resp.status_code}: {resp.text}")
                return None
        except Exception as e:
            logger.error(f"Token 交换异常: {e}")
            return None

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

        if not oauth_config.token_endpoint:
            logger.error(f"缺少 token_endpoint: {server_name}")
            return None

        data = {
            "grant_type": "refresh_token",
            "refresh_token": oauth_config.token.refresh_token,
            "client_id": oauth_config.client_id or "",
        }
        if oauth_config.client_secret:
            data["client_secret"] = oauth_config.client_secret

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    oauth_config.token_endpoint,
                    data=data,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code == 200:
                    token_data = resp.json()
                    expires_in = token_data.get("expires_in")
                    expires_at = None
                    if expires_in:
                        expires_at = (
                            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                        ).isoformat()

                    return OAuthToken(
                        access_token=token_data["access_token"],
                        refresh_token=token_data.get("refresh_token", oauth_config.token.refresh_token),
                        expires_at=expires_at,
                        token_type=token_data.get("token_type", "Bearer"),
                    )
                logger.warning(f"Token 刷新失败 {resp.status_code}: {resp.text}")
                return None
        except Exception as e:
            logger.warning(f"Token 刷新异常: {e}")
            return None

    @staticmethod
    def handle_manual_callback(server_name: str, callback_url: str) -> Optional[Tuple[str, str]]:
        """
        从手动粘贴的回调 URL 中解析 code 和 state。

        Args:
            server_name: 服务器名称
            callback_url: 用户粘贴的完整回调 URL

        Returns:
            (code, state) 元组，失败返回 None
        """
        try:
            parsed = urlparse(callback_url)
            params = parse_qs(parsed.query)
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            if not code or not state:
                logger.error(f"回调 URL 缺少 code 或 state: {callback_url}")
                return None
            return code, state
        except Exception as e:
            logger.error(f"解析回调 URL 失败: {e}")
            return None

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
            return False


oauth_flow_service = OAuthFlowService()