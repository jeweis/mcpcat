"""参考 Host 夹具自身的来源、作用域与完整交付观测回归。"""

import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastmcp import Client
from starlette.types import Message, Receive, Scope, Send

from tests.browser.fixture_delivery_probe import DeliveryProbe
from tests.browser.reference_apps_host import ReferenceRoutes, tool_names
from tests.browser.reference_upstream_apps import make_source


@pytest.mark.parametrize("name", ["source-a", "source-b"])
async def test_reference_source_keeps_original_app_action(
    name: str, tmp_path: Path
) -> None:
    """fixture 中的 save 名和动态 payload 不预先改成 Catalog callback。"""
    (tmp_path / "reference-app.js").write_text("/* trusted fixture */")
    counts = {}
    source = make_source(name, tmp_path, counts)
    async with Client(source) as client:
        html = await client.call_tool("open_html", {})
        assert html.structured_content == {"source": name, "count": 0}
        before = await client.call_tool("open_prefab", {})
        assert tool_names(before.structured_content) == {"save"}
        assert counts == {name: 0}
        saved = await client.call_tool("save", {"increment": 1})
        assert saved.structured_content == {"source": name, "count": 1}
        after = await client.call_tool("open_prefab", {})
        assert after.structured_content["state"] == {"source": name, "count": 1}
        assert tool_names(after.structured_content) == {"save"}


def test_reference_scope_only_reads_explicit_action_and_binding() -> None:
    """普通业务字符串不能被夹具误判为获准工具。"""
    assert tool_names(
        {
            "mcpcat": {"toolBindings": {"store": "approved"}},
            "view": [{"action": "toolCall", "tool": "save"}, {"tool": "not-an-action"}],
        }
    ) == {"approved", "save"}


@pytest.mark.parametrize("slug", ["../../private", "source-c:html"])
def test_reference_routes_reject_unknown_fixture(slug: str, tmp_path: Path) -> None:
    """临时适配器不开放任意 App/来源转发或文件路径。"""
    with pytest.raises(HTTPException):
        ReferenceRoutes(tmp_path).slug({"slug": slug})


@pytest.mark.parametrize("fail_send", [False, True])
async def test_delivery_observer_only_records_completed_original_bytes(
    fail_send: bool,
) -> None:
    """最终发送失败不能记作完整下载，也不改变原始二进制消息。"""
    messages = delivery_messages()
    sent, counters = [], {"downloads": []}

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        """原消息逐个发送，观测器不能改变消息。"""
        for message in messages:
            await send(message)

    async def send(message: Message) -> None:
        """模拟正常交付或最终 body 发送失败。"""
        if fail_send and message is messages[-1]:
            raise ConnectionError("fixture closed")
        sent.append(message)

    async def receive() -> Message:
        """本测试不消耗请求体。"""
        return {"type": "http.disconnect"}

    operation = DeliveryProbe(app, counters)(
        {"type": "http", "path": "/api/skill-variants/fixture/versions/1.0.0/download"},
        receive,
        send,
    )
    if fail_send:
        with pytest.raises(ConnectionError):
            await operation
        assert counters["downloads"] == []
    else:
        await operation
        assert sent == messages
        assert counters["downloads"][0]["size"] == 6
        assert (
            counters["downloads"][0]["sha256"]
            == hashlib.sha256(b"\x00\xfftail").hexdigest()
        )


def delivery_messages() -> list[Message]:
    """跨两个 body 分片的原始二进制响应。"""
    return [
        {"type": "http.response.start", "status": 200, "headers": []},
        {"type": "http.response.body", "body": b"\x00\xff", "more_body": True},
        {"type": "http.response.body", "body": b"tail", "more_body": False},
    ]
