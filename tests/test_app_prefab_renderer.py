"""官方 bundled renderer、动态数据与绑定动作装配，不执行上传代码。"""

import hashlib
import json

import pytest
from prefab_ui import PrefabApp
from prefab_ui.renderer import get_renderer_html

from app.services.app_content_service import AppDomainError
from app.services.app_package_validator import (
    validate_app_package,
    validate_prefab_json,
)
from app.services.app_prefab_renderer import prepare_prefab_app


def package(wire, bindings=None):
    content = json.dumps(wire).encode()
    validated = validate_app_package(content, filename="app.json")
    return {
        "content_format": "prefab",
        "renderer_version": "0.20.2",
        "entrypoint": "app.json",
        "manifest": validated.manifest,
        "media_type": "application/json",
        "csp": {},
        "permissions": {},
        "bindings": bindings or [],
    }, content


def test_official_bundle_matches_and_dynamic_state_and_actions_preserved():
    info, content = package(
        {
            "view": {
                "type": "Button",
                "label": "Load",
                "onClick": {
                    "action": "toolCall",
                    "tool": "load",
                    "arguments": {"value": "{{ count }}"},
                    "onSuccess": {
                        "action": "setState",
                        "key": "count",
                        "value": "{{ $result.value }}",
                    },
                },
            },
            "state": {"count": 7},
        },
        [{"alias": "load"}],
    )
    snapshot = bytes(content)
    rendered = prepare_prefab_app(info, content, tool_names={"load": "app_1_v1_load"})
    assert rendered.html == get_renderer_html("bundled")
    assert len(rendered.html) > 6_000_000
    assert rendered.sha256 == hashlib.sha256(rendered.html.encode()).hexdigest()
    payload = rendered.structured_content
    assert payload["$prefab"]["version"] == "0.3"
    assert payload["state"] == {"count": 7}
    button = payload["view"]["children"][0]
    assert button["onClick"]["tool"] == "app_1_v1_load"
    assert button["onClick"]["arguments"] == {"value": "{{ count }}"}
    assert button["onClick"]["onSuccess"]["value"] == "{{ $result.value }}"
    assert content == snapshot
    assert rendered.metadata["ui"]["csp"]["resourceDomains"] == []


def test_approved_csp_and_permissions_not_replaced_with_renderer_wildcards():
    info, content = package({"view": {"type": "Heading", "content": "Dashboard"}})
    info["csp"] = {
        "connectDomains": ["https://example.com"],
        "resourceDomains": ["https://assets.example.com"],
    }
    info["permissions"] = {"clipboardWrite": {}}
    rendered = prepare_prefab_app(info, content, tool_names={})
    assert rendered.metadata["ui"]["csp"] == info["csp"]
    assert rendered.metadata["ui"]["permissions"] == info["permissions"]


def test_real_official_wire_protocol_header_is_validated():
    wire = PrefabApp.from_json(
        {"view": {"type": "Heading", "content": "Test"}}
    ).to_json()
    assert (
        validate_prefab_json(json.dumps(wire).encode())["$prefab"]["version"] == "0.3"
    )
    wire["$prefab"]["version"] = "future"
    with pytest.raises(ValueError, match="协议版本"):
        validate_prefab_json(json.dumps(wire).encode())


@pytest.mark.parametrize(
    "names", [{}, {"load": "other/path"}, {"load": "valid", "unknown": "extra"}]
)
def test_mapping_cannot_guess_or_expand_bound_tool_scope(names):
    info, content = package(
        {
            "view": {
                "type": "Button",
                "label": "Load",
                "onClick": {"action": "toolCall", "tool": "load"},
            }
        },
        [{"alias": "load"}],
    )
    with pytest.raises(AppDomainError):
        prepare_prefab_app(info, content, tool_names=names)


def test_undeclared_tool_reference_is_rejected():
    info, content = package(
        {
            "view": {
                "type": "Button",
                "label": "Load",
                "onClick": {"action": "toolCall", "tool": "not-authorized"},
            }
        }
    )
    with pytest.raises(AppDomainError, match="未绑定"):
        prepare_prefab_app(info, content, tool_names={})


def test_state_code_is_not_evaluated_and_renderer_mismatch_fails(tmp_path):
    marker = tmp_path / "never-executed"
    wire = {
        "view": {"type": "Heading", "content": "{{ message }}"},
        "state": {"message": f"__import__('pathlib').Path({str(marker)!r}).touch()"},
    }
    info, content = package(wire)
    rendered = prepare_prefab_app(info, content, tool_names={})
    assert rendered.structured_content["state"] == wire["state"]
    assert not marker.exists()
    info["renderer_version"] = "future"
    with pytest.raises(AppDomainError, match="版本"):
        prepare_prefab_app(info, content, tool_names={})
