"""Tests for the github.com/trending page collector."""

from __future__ import annotations

from unittest.mock import Mock, patch

from src.collectors.base import SourceConfig
from src.collectors.github_trending_collector import GithubTrendingPageCollector

_SAMPLE_HTML = """
<html><body>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/opengeos/GeoLibre">opengeos / GeoLibre</a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">A lightweight, cloud-native GIS platform.</p>
  <span itemprop="programmingLanguage">TypeScript</span>
  <a href="/opengeos/GeoLibre/stargazers">3,678</a>
  <span class="d-inline-block float-sm-right">607 stars today</span>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/foo/bar">foo / bar</a></h2>
  <a href="/foo/bar/stargazers">120</a>
  <span class="d-inline-block float-sm-right">3 stars today</span>
</article>
<article class="Box-row">
  <div>No heading link here, malformed row.</div>
</article>
</body></html>
"""


def trending_source() -> SourceConfig:
    return SourceConfig(
        id="github_trending_ai",
        name="GitHub Trending",
        company="GitHub",
        url="https://github.com/trending",
        type="github_trending",
        category_hint="coding_ai",
        enabled=True,
        allowed_domains=("github.com",),
        source_group="open_source_community",
        signal_type="open_source_trend",
    )


def _mock_response(html: str) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.text = html
    response.encoding = "utf-8"
    response.apparent_encoding = "utf-8"
    response.iter_content.return_value = [html.encode("utf-8")]
    return response


def test_extracts_expected_repos_with_correct_fields() -> None:
    collector = GithubTrendingPageCollector()
    with patch(
        "src.net_guard.requests.get",
        return_value=_mock_response(_SAMPLE_HTML),
    ):
        items = collector.collect(trending_source())

    # The malformed row (no heading link) is skipped.
    assert len(items) == 2
    first = items[0]
    assert first["trend_subject"] == "opengeos/GeoLibre"
    assert first["url"] == "https://github.com/opengeos/GeoLibre"
    assert first["github_language"] == "TypeScript"
    assert first["github_stars"] == "3678"
    assert first["github_stars_today"] == "607"
    assert first["signal_type"] == "open_source_trend"
    assert "GIS platform" in first["excerpt"]


def test_trend_score_grows_with_stars_today() -> None:
    collector = GithubTrendingPageCollector()
    with patch(
        "src.net_guard.requests.get",
        return_value=_mock_response(_SAMPLE_HTML),
    ):
        items = collector.collect(trending_source())

    high_growth, low_growth = items[0], items[1]
    assert int(high_growth["trend_score"]) > int(low_growth["trend_score"])


def test_missing_description_and_language_fall_back_gracefully() -> None:
    collector = GithubTrendingPageCollector()
    with patch(
        "src.net_guard.requests.get",
        return_value=_mock_response(_SAMPLE_HTML),
    ):
        items = collector.collect(trending_source())

    second = items[1]
    assert second["github_language"] == ""
    assert "foo/bar" in second["excerpt"]


def test_row_without_valid_link_is_skipped_without_raising() -> None:
    collector = GithubTrendingPageCollector()
    html = '<article class="Box-row"><div>nothing useful</div></article>'
    with patch(
        "src.net_guard.requests.get",
        return_value=_mock_response(html),
    ):
        items = collector.collect(trending_source())

    assert items == []
