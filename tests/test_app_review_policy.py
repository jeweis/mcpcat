"""审核 origin 不得变成 CSP、HTML 或 URL 注入，权限请求不是授予。"""

import pytest
from pydantic import ValidationError

from app.models.app_review import AppCSP, AppPermissions


@pytest.mark.parametrize(
    "origin",
    [
        'https://example.test"',
        "https://example.test'",
        "https://example.test<svg>",
        "https://example.test&x",
        "https://example.test%22",
        "https://example.test#",
        "https://example.test?",
        "https://example.test\x00",
        "https://example.test:",
        "https://example.test:0",
        "https://-example.test",
        "https://example..test",
        "https://例子.test",
        "https://example.test;script-src *",
        "https://[invalid]",
    ],
)
def test_unsafe_origin_rejected(origin: str) -> None:
    """四类官方字段使用完全相同的严格解析。"""
    for field in (
        "connectDomains",
        "resourceDomains",
        "frameDomains",
        "baseUriDomains",
    ):
        with pytest.raises(ValidationError):
            AppCSP.model_validate({field: [origin]})


def test_explicit_origins_and_empty_device_requests_preserved() -> None:
    """合法声明保留；不会通过验证器额外插入权限。"""
    origins = [
        "https://example.test",
        "https://xn--fsqu00a.test:8443",
        "https://[::1]:8443",
    ]
    assert AppCSP(connectDomains=origins).connect_domains == origins
    assert AppPermissions(camera={}).model_dump(by_alias=True, exclude_none=True) == {
        "camera": {}
    }
