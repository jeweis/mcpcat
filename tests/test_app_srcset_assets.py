"""srcset 保留标准候选语义，所有候选经过相同制品安全检查。"""

import pytest

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_html_assembly import assemble_app_html
from app.services.app_srcset_assets import assemble_app_srcset


def test_candidates_keep_width_density_sizes_and_original_bytes():
    files = {"assets/low.png": b"low", "assets/high.png": b"high"}
    before = dict(files)
    resolver = AppAssetResolver(files, [])
    html = '<picture><source srcset="../assets/low.png 400w, ../assets/high.png 800w" sizes="(max-width: 40em) 100vw, 50vw"><img src="../assets/low.png" srcset="../assets/low.png 1x, ../assets/high.png 2x"></picture>'
    result = assemble_app_html(html, resolver=resolver, entrypoint="pages/index.html")
    assert (
        "data:image/png;base64,bG93 400w, data:image/png;base64,aGlnaA== 800w" in result
    )
    assert "data:image/png;base64,bG93 1x, data:image/png;base64,aGlnaA== 2x" in result
    assert 'sizes="(max-width: 40em) 100vw, 50vw"' in result
    assert files == before


def test_data_uri_internal_commas_are_not_candidate_separators():
    source = "data:image/svg+xml,%3Csvg,%3E 1x, high.png 2x"
    result = assemble_app_srcset(
        source, resolver=AppAssetResolver({"high.png": b"x"}, []), base="index.html"
    )
    assert result == "data:image/svg+xml,%3Csvg,%3E 1x, data:image/png;base64,eA== 2x"
    assert (
        assemble_app_srcset(
            "low.png, high.png 2x",
            resolver=AppAssetResolver({"low.png": b"a", "high.png": b"b"}, []),
            base="index.html",
        )
        == "data:image/png;base64,YQ==, data:image/png;base64,Yg== 2x"
    )


@pytest.mark.parametrize(
    "value",
    [
        "a.png -1x",
        "a.png NaNx",
        "a.png 1e999x",
        "a.png 0w",
        "a.png 1h",
        "a.png 1x 2x",
        "a.png 100w 1x",
        "a.png calc(2)x",
        "a.png 2q",
        "../a.png 1x",
        "https://unreviewed.example/a.png 1x",
        "missing.png 1x",
    ],
)
def test_invalid_or_unsafe_candidates_fail_closed(value):
    with pytest.raises(ValueError):
        assemble_app_srcset(
            value, resolver=AppAssetResolver({"a.png": b"a"}, []), base="index.html"
        )


def test_reviewed_urls_preload_and_limits():
    url = "https://assets.example/a.png"
    resolver = AppAssetResolver({}, ["https://assets.example"])
    assert (
        assemble_app_srcset(url + " 1.5x", resolver=resolver, base="index.html")
        == url + " 1.5x"
    )
    result = assemble_app_html(
        f'<link rel="preload" as="image" imagesrcset="{url} 400w" imagesizes="50vw">',
        resolver=resolver,
        entrypoint="index.html",
    )
    assert 'imagesizes="50vw"' in result
    with pytest.raises(ValueError, match="数量"):
        assemble_app_srcset(
            ", ".join([url + " 1x"] * 513), resolver=resolver, base="index.html"
        )
