"""使用 FastMCP 公开客户端接口隔离上游连接及凭证。"""

from typing import Any, Callable, Dict

import mcp.types as types
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import (
    SSETransport,
    StreamableHttpTransport,
    infer_transport,
)
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.proxy import ProxyClient
from mcp.shared.exceptions import MCPError
from mcp.client.extension import advertise
from mcp.types import CreateMessageRequest
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from app.services.input_exchange import current_exchange
from app.services.legacy_input_bridge import answer_input
from app.services.oauth_tokens import current_access_token
from app.services.oauth_machine_client import machine_client_options
from app.services.proxy_capability_transport import CapabilityTransport
from app.services.proxy_logging import already_forwarded_log, request_log_handler
from app.services.roots_notifications import current_roots_relay


def request_interaction_options() -> dict[str, Any]:
    """捕获原客户端，转发目录及用户输入，不在网关读取或填写。"""
    options: dict[str, Any] = {"roots": None, "elicitation_handler": None}
    try:
        context = get_context()
        if context.request_context is None:
            return options
        session = context.session
    except RuntimeError:
        return options
    capabilities = session.client_capabilities
    if capabilities is None:
        return options
    exchange = (
        current_exchange.get()
        if context.request_context.protocol_version in MODERN_PROTOCOL_VERSIONS
        else None
    )

    async def relay(request):
        if exchange is not None:
            return await exchange.ask(request)
        return await answer_input(session, request)

    async def roots(callback_context):
        result = await relay(types.ListRootsRequest(method="roots/list"))
        return result.roots

    async def elicit(message, response_type, params, callback_context):
        mode = "url" if isinstance(params, types.ElicitRequestURLParams) else "form"
        if getattr(capabilities.elicitation, mode, None) is None:
            raise MCPError(types.INVALID_REQUEST, "Client input mode unavailable")
        return await relay(
            types.ElicitRequest(method="elicitation/create", params=params)
        )

    if capabilities.roots is not None:
        options["roots"] = roots
    if capabilities.elicitation is not None:
        options["elicitation_handler"] = elicit
    return options


def request_sampling_options() -> dict[str, Any]:
    """捕获当前请求的客户端会话，完整转发旧协议 Sampling。

    工厂为每次代理操作创建客户端；在接收循环启动前捕获公开 session，
    不在回调中重新解析可能属于上游的 ContextVar，也不读取框架私有状态。
    管理侧无 MCP 请求的客户端不安装此回调。
    """
    try:
        context = get_context()
        if context.request_context is None:
            return {"sampling_handler": None}
        session = context.session
    except RuntimeError:
        return {"sampling_handler": None}
    capabilities = session.client_capabilities
    sampling = capabilities.sampling if capabilities is not None else None
    if sampling is None:
        return {"sampling_handler": None}
    exchange = (
        current_exchange.get()
        if context.request_context.protocol_version in MODERN_PROTOCOL_VERSIONS
        else None
    )

    async def relay(messages, params, callback_context):
        if exchange is not None:
            return await exchange.ask(
                CreateMessageRequest(method="sampling/createMessage", params=params)
            )
        return await session.create_message(
            messages=list(messages),
            max_tokens=params.max_tokens,
            system_prompt=params.system_prompt,
            include_context=params.include_context,
            temperature=params.temperature,
            stop_sequences=params.stop_sequences,
            metadata=params.metadata,
            model_preferences=params.model_preferences,
            tools=params.tools,
            tool_choice=params.tool_choice,
        )

    return {
        "sampling_handler": relay,
        "sampling_capabilities": sampling.model_copy(deep=True),
    }


def upstream_mode(server_type: str = "streamable-http") -> str:
    """各跳独立协商；只有 SSE 传输自身必须使用握手时代。"""
    return "legacy" if server_type == "sse" else "auto"


def make_proxy_client_factory(
    config: Dict[str, Any], token: str | None = None
) -> Callable[..., ProxyClient]:
    """每次创建独立 transport/client，仅使用已配置的上游凭证。"""
    server_type = config["type"]
    headers = dict(config.get("headers") or {})
    url = config.get("url", "")
    timeout = config.get("timeout", 30)
    if server_type == "stdio":
        target = {
            "mcpServers": {
                "default": {
                    "command": config["command"],
                    "args": list(config.get("args") or []),
                    "env": dict(config.get("env") or {}),
                    # 每次操作独立创建；Tasks 在整个任务生命周期持有上下文。
                    "keep_alive": False,
                }
            }
        }

    def create_client(
        *, message_handler: Any = None, extensions: Any = None
    ) -> ProxyClient:
        client_extensions = list(extensions or ())
        if server_type == "stdio":
            transport = target
        else:
            machine_options = machine_client_options(config)
            if machine_options is not None:
                identifier = "io.modelcontextprotocol/oauth-client-credentials"
                client_extensions = [
                    extension
                    for extension in client_extensions
                    if extension.identifier != identifier
                ]
                client_extensions.append(advertise(identifier))
            current_token = (
                (
                    current_access_token(config)
                    if "oauth" in config or token is None
                    else token
                )
                if machine_options is None
                else None
            )
            transport_class = (
                SSETransport if server_type == "sse" else StreamableHttpTransport
            )
            transport = transport_class(
                url=url,
                headers=dict(headers),
                **(
                    machine_options
                    if machine_options is not None
                    else {
                        "auth": (
                            BearerAuth(token=current_token) if current_token else None
                        ),
                    }
                ),
            )
        callbacks = {**request_sampling_options(), **request_interaction_options()}
        callbacks["log_handler"] = request_log_handler()
        elicitation = None
        try:
            capabilities = get_context().session.client_capabilities
            if capabilities is not None and capabilities.elicitation is not None:
                elicitation = capabilities.elicitation.model_dump(
                    by_alias=True, mode="json", exclude_none=True
                )
        except RuntimeError:
            pass
        relay = current_roots_relay.get()
        roots_key = relay.capture() if relay is not None else None
        roots_watch = (
            (lambda session: relay.watch(roots_key, session))
            if roots_key is not None
            else None
        )
        exchange = current_exchange.get()
        if exchange is not None:
            callbacks["progress_handler"] = exchange.report_progress
            callbacks["log_handler"] = already_forwarded_log
        transport = CapabilityTransport(
            infer_transport(transport),
            elicitation,
            roots_watch,
            ordered_log_handler=exchange.report_log if exchange is not None else None,
        )
        if message_handler is not None:
            callbacks["message_handler"] = message_handler
        return ProxyClient(
            transport,
            forward_incoming_headers=False,
            mode=upstream_mode(server_type),
            timeout=timeout,
            extensions=(
                client_extensions
                if extensions is not None or client_extensions
                else None
            ),
            **callbacks,
        )

    return create_client
