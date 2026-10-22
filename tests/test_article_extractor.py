"""Tests for article page extraction fallbacks."""

from __future__ import annotations

import pytest

from src.collectors.article_extractor import extract_article_content, fetch_article_content


def test_extract_article_content_uses_json_ld_description_fallback() -> None:
    article = extract_article_content(
        """
        <html>
          <head>
            <script type="application/ld+json">
              {
                "@type": "NewsArticle",
                "headline": "OpenAI API model update",
                "description": "OpenAI introduced a model update for developers using the API."
              }
            </script>
          </head>
          <body>
            <main><h1>OpenAI API model update</h1></main>
          </body>
        </html>
        """
    )

    assert article.clean_title == "OpenAI API model update"
    assert article.excerpt == "OpenAI introduced a model update for developers using the API."
    assert article.content_quality == "excerpt"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9200/_cat/indices",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
    ],
)
def test_fetch_article_content_rejects_ssrf_urls(url: str) -> None:
    with pytest.raises(ValueError):
        fetch_article_content(url)
