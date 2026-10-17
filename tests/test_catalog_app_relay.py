"""上游原文、派生壳和分层 CSP；测试不执行上传 HTML/JS。"""

import base64
import hashlib
import json
from uuid import uuid4

import pytest
from test_app_module_graph import ModuleTags

from app.services.catalog_app_identity import CatalogAppIdentity
from app.services.catalog_app_relay import relay_document, relay_metadata


def test_relay_keeps_upstream_bytes_and_other_metadata_exact():
    identity = CatalogAppIdentity(str(uuid4()), str(uuid4()))
    original = '<!doctype html><p>中文</p><script>const text="raw";</script>'.encode()
    metadata = {
        "ui": {
            "csp": {
                "resourceDomains": ["https://assets.example.test"],
                "connectDomains": [],
                "frameDomains": ["https://frames.example.test"],
            },
            "permissions": {"clipboardWrite": {}},
            "unknown": [1],
        },
        "fixture/unknown": {"preserved": True},
    }
    snapshot = json.loads(json.dumps(metadata))
    result = relay_document(
        identity, original, metadata, {"save": identity.callback("save")}
    )
    assert result.original_sha256 == hashlib.sha256(original).hexdigest()
    assert metadata == snapshot
    assert result.metadata["fixture/unknown"] == metadata["fixture/unknown"]
    assert result.metadata["ui"]["csp"]["resourceDomains"] == [
        "https://assets.example.test"
    ]
    assert result.metadata["ui"]["csp"]["frameDomains"] == [
        "https://frames.example.test",
        "about:",
    ]
    config = json.loads(
        result.html.split('id="mcpcat-source-config">', 1)[1].split("</script>", 1)[0]
    )
    assert base64.b64decode(config["html"]) == original
    assert (
        config["innerPolicy"]
        == "frame-src https://frames.example.test; object-src 'none'"
    )
    assert config["allow"] == "clipboard-write *"
    assert "<p>中文</p>" not in result.html


def test_default_inner_csp_does_not_inherit_new_outer_frame_capability():
    result, policy, allow = relay_metadata({})
    assert result == {"ui": {"csp": {"frameDomains": ["about:"]}}}
    assert policy == "frame-src 'none'; object-src 'none'"
    assert allow == ""


@pytest.mark.parametrize(
    "domains",
    [
        ["https://approved.example; frame-src *"],
        ["https://with space.example"],
        ["'unsafe-inline'"],
        [False],
        "https://wrong-type.example",
    ],
)
def test_invalid_frame_policy_cannot_inject_csp(domains):
    with pytest.raises(ValueError):
        relay_metadata({"ui": {"csp": {"frameDomains": domains}}})


def test_untrusted_tool_names_cannot_end_outer_script_or_execute_in_builder(tmp_path):
    identity = CatalogAppIdentity(str(uuid4()), str(uuid4()))
    name = '</script><script>alert("not outer code")</script>'
    original = f"<p>fixture {tmp_path / 'never-executed'}</p>".encode()
    result = relay_document(identity, original, {}, {name: identity.callback(name)})
    assert name not in result.html
    parsed = ModuleTags()
    parsed.feed(result.html)
    assert len(parsed.tags) == 2
    assert not (tmp_path / "never-executed").exists()
