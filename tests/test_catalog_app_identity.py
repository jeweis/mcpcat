"""来源/代际标识不是认证快照，所有解析仅限规范字面格式。"""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.services.catalog_app_identity import (
    CatalogAppIdentity,
    decode_catalog_resource,
)


@pytest.mark.parametrize(
    "uri",
    [
        "ui://shared/app.html",
        "asset://same/data?query=1#fragment",
        "urn:fixture:中文",
        "custom+scheme://example.test/a%20b?q=%2F#fragment",
    ],
)
def test_origin_resource_preserves_original_bytes_and_scopes(uri):
    identity = CatalogAppIdentity(str(uuid4()), str(uuid4()))
    encoded = identity.resource(uri)
    assert decode_catalog_resource(encoded) == (identity, uri)
    assert identity.unwrap(encoded) == uri
    with pytest.raises(ValueError, match="changed"):
        replace(identity, source=str(uuid4())).unwrap(encoded)
    with pytest.raises(ValueError, match="changed"):
        replace(identity, generation=str(uuid4())).unwrap(encoded)


def test_callback_source_and_generation_cannot_collide_for_same_name():
    identity = CatalogAppIdentity(str(uuid4()), str(uuid4()))
    names = {
        identity.callback("save"),
        replace(identity, source=str(uuid4())).callback("save"),
        replace(identity, generation=str(uuid4())).callback("save"),
    }
    assert len(names) == 3 and all(len(name) <= 64 for name in names)


@pytest.mark.parametrize("suffix", ["=", "/", "?x=1", "#x", "%3D", "\n"])
def test_resource_noncanonical_or_extra_components_rejected(suffix):
    identity = CatalogAppIdentity(str(uuid4()), str(uuid4()))
    with pytest.raises(ValueError):
        decode_catalog_resource(identity.resource("ui://example/app.html") + suffix)


@pytest.mark.parametrize(
    "uri",
    [
        "relative/path",
        "file with space",
        "ui://example/\x00",
        "ui://example/" + "x" * 8193,
    ],
)
def test_invalid_or_oversized_uri_rejected(uri):
    with pytest.raises(ValueError):
        CatalogAppIdentity(str(uuid4()), str(uuid4())).resource(uri)
