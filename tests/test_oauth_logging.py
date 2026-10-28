"""固定 Uvicorn access logger 格式不泄露 OAuth 回调 code/state。"""

import logging

import pytest

from app.core.oauth_logging import (
    OAuthCallbackAccessFilter,
    install_oauth_access_filter,
)


@pytest.mark.parametrize(
    "path",
    [
        "/api/oauth/callback/fixture",
        "/base/api/oauth/callback/fixture",
        "/api%2Foauth%2Fcallback%2Ffixture",
        "/api/auth/wecom/callback",
        "/base/api/auth/wecom/callback",
        "/api%2Fauth%2Fwecom%2Fcallback",
    ],
)
def test_callback_query_is_redacted(caplog, path):
    logger = logging.getLogger("uvicorn.access")
    original = list(logger.filters)
    try:
        install_oauth_access_filter()
        install_oauth_access_filter()
        assert (
            sum(isinstance(item, OAuthCallbackAccessFilter) for item in logger.filters)
            == 1
        )
        with caplog.at_level(logging.INFO, logger="uvicorn.access"):
            logger.info(
                '%s - "%s %s HTTP/%s" %d',
                "127.0.0.1",
                "GET",
                path
                + "?code=fixture-code&state=fixture-state&iss=https://issuer.fixture",
                "1.1",
                200,
            )
        assert "fixture-code" not in caplog.text and "fixture-state" not in caplog.text
        assert "[REDACTED]" in caplog.text and path in caplog.text
    finally:
        logger.filters[:] = original


def test_other_routes_and_unknown_record_shapes_unchanged():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "%s %s %s %s %s",
        ("client", "GET", "/api/health?verbose=1", "1.1", 200),
        None,
    )
    filter = OAuthCallbackAccessFilter()
    before = record.getMessage()
    assert filter.filter(record) and record.getMessage() == before
    record.args, record.msg = (), "service started"
    assert filter.filter(record) and record.getMessage() == "service started"
