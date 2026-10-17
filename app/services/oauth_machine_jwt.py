"""使用 SDK 签发机器认证断言；私钥只通过明确的服务端环境变量引用。"""

import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from mcp.client.auth import OAuthFlowError
from mcp.client.auth.extensions.client_credentials import SignedJWTParameters


def machine_assertion_provider(
    oauth: dict[str, Any], client_id: str
) -> Callable[[str], Awaitable[str]]:
    """读取一次受控引用，限制非对称算法；不接受内联私钥或自定义 claims。"""
    name = oauth.get("private_key_env")
    algorithm = oauth.get("signing_algorithm", "RS256")
    if not isinstance(name, str) or re.fullmatch(r"[A-Z_][A-Z0-9_]*", name) is None:
        raise ValueError("机器认证必须配置合法的私钥环境变量名")
    if algorithm not in {"RS256", "ES256"}:
        raise ValueError("机器认证仅支持 RS256 或 ES256 私钥签名")
    pem = os.environ.get(name)
    if not pem or len(pem) > 32768:
        raise ValueError("机器认证私钥环境变量缺失或超限")
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        valid = (
            algorithm == "RS256"
            and isinstance(key, rsa.RSAPrivateKey)
            and key.key_size >= 2048
        ) or (
            algorithm == "ES256"
            and isinstance(key, ec.EllipticCurvePrivateKey)
            and isinstance(key.curve, ec.SECP256R1)
        )
        if not valid:
            raise ValueError
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ValueError("机器认证私钥格式或算法不匹配") from None
    sign = SignedJWTParameters(
        issuer=client_id,
        subject=client_id,
        signing_key=pem,
        signing_algorithm=algorithm,
        lifetime_seconds=60,
    ).create_assertion_provider()

    async def assertion(audience: str) -> str:
        try:
            return await sign(audience)
        except Exception:
            raise OAuthFlowError(
                "Machine authorization assertion signing failed"
            ) from None

    return assertion
