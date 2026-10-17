"""App 派生入口、附件、固定版本和规范编码隔离。"""

import pytest

from app.services.app_resource_uri import AppResourceURI

INSTANCE = "00000000-0000-4000-8000-000000000001"
ENDPOINT = "00000000-0000-4000-8000-000000000002"


def test_entry_and_original_files_cannot_collide_and_version_is_fixed():
    entry = AppResourceURI(INSTANCE, ENDPOINT, 1, "1.0.0")
    attachment = AppResourceURI(INSTANCE, ENDPOINT, 1, "1.0.0", "entry")
    assert entry.encode() != attachment.encode()
    assert attachment.encode().startswith("app-file://")
    assert AppResourceURI.parse(entry.encode()) == entry
    assert AppResourceURI.parse(attachment.encode()) == attachment
    assert AppResourceURI(INSTANCE, ENDPOINT, 1, "1.1.0").encode() != entry.encode()


def test_build_version_and_unicode_attachment_round_trip():
    value = AppResourceURI(INSTANCE, ENDPOINT, 1, "1.0.0+build", "images/用户 图标.png")
    assert "%2B" in value.encode()
    assert AppResourceURI.parse(value.encode()) == value


@pytest.mark.parametrize(
    "suffix",
    [
        "entry?key=secret",
        "entry#fragment",
        "entry/extra",
        "files/../config.json",
        "files/%2e%2e/config.json",
        "files//etc/passwd",
        "files/a%2Fb.txt",
        "files/a%5cb.txt",
        "files/%00",
        "files/%FF",
        "unknown",
    ],
)
def test_unsafe_ambiguous_and_noncanonical_paths_rejected(suffix):
    scheme = "app-file" if suffix.startswith("files/") else "ui"
    with pytest.raises(ValueError):
        AppResourceURI.parse(
            f"{scheme}://mcpcat/{INSTANCE}/{ENDPOINT}/1/1.0.0/{suffix}"
        )


@pytest.mark.parametrize("value", ["01", "0", "-1", "latest"])
def test_identity_number_does_not_accept_aliases(value):
    with pytest.raises(ValueError):
        AppResourceURI.parse(f"ui://mcpcat/{INSTANCE}/{ENDPOINT}/{value}/1.0.0/entry")
