"""Tests for in-app source management and per-source history."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.server import add_source, list_sources, remove_source, resolve_youtube_feed_url, update_source
from src.storage.json_storage import load_news_by_source, save_json


def _fake_urlopen(html: str) -> mock.MagicMock:
    """Build a fake urlopen() result usable as a context manager."""
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = html.encode("utf-8")
    return cm


EXISTING_JSON = {
    "categories": ["general_ai"],
    "sources": [
        {
            "id": "openai_news",
            "name": "OpenAI News",
            "company": "OpenAI",
            "url": "https://openai.com/news/rss.xml",
            "type": "rss",
            "category_hint": "general_ai",
            "enabled": True,
            "notes": "Nota da preservare.",
        }
    ],
}


class AddSourceTest(unittest.TestCase):
    def _json_file(self, tmp_dir: str) -> Path:
        path = Path(tmp_dir) / "sources.json"
        path.write_text(json.dumps(EXISTING_JSON), encoding="utf-8")
        return path

    def test_appends_source_and_preserves_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._json_file(tmp_dir)
            result = add_source(
                {"id": "nuova", "name": "Nuova Fonte", "url": "https://example.com/feed"}, path
            )
            self.assertTrue(result["ok"], result.get("error"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            ids = [s["id"] for s in payload["sources"]]
            self.assertEqual(ids, ["openai_news", "nuova"])
            # The original entry and its note survive untouched.
            self.assertEqual(payload["sources"][0]["notes"], "Nota da preservare.")
            new_source = payload["sources"][1]
            self.assertEqual(new_source["type"], "rss")
            self.assertEqual(new_source["allowed_domains"], ["example.com"])
            self.assertTrue(new_source["enabled"])

    def test_rejects_duplicate_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._json_file(tmp_dir)
            result = add_source(
                {"id": "openai_news", "url": "https://example.com/feed"}, path
            )
            self.assertFalse(result["ok"])
            self.assertIn("esiste", result["error"])

    def test_rejects_missing_or_bad_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._json_file(tmp_dir)
            self.assertFalse(add_source({"id": "x"}, path)["ok"])
            self.assertFalse(add_source({"id": "x", "url": "ftp://x"}, path)["ok"])

    def test_resolves_youtube_handle_url_before_adding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._json_file(tmp_dir)
            with mock.patch(
                "urllib.request.urlopen",
                return_value=_fake_urlopen('{"externalId":"UCabc123DEF456ghijk"}'),
            ):
                result = add_source(
                    {"id": "yt_new", "url": "https://www.youtube.com/@ProductHeroes"}, path
                )
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(
                result["source"]["url"],
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc123DEF456ghijk",
            )

    def test_rejects_ssrf_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._json_file(tmp_dir)
            for url in (
                "http://127.0.0.1:9200/_cat/indices",
                "http://169.254.169.254/latest/meta-data/",
                "http://10.0.0.5/internal",
            ):
                result = add_source({"id": "ssrf", "url": url}, path)
                self.assertFalse(result["ok"], url)
                self.assertIn("sicuro", result["error"])

    def test_rejects_file_scheme_disguised_as_youtube(self) -> None:
        # The exact bypass attempt from the audit: a file:// URL whose text
        # happens to contain "youtube.com/@..." somewhere in the fragment.
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._json_file(tmp_dir)
            result = add_source(
                {"id": "sneaky", "url": "file:///Users/x/.ssh/id_rsa#youtube.com/@a"}, path
            )
            self.assertFalse(result["ok"])


class ListSourcesTest(unittest.TestCase):
    def test_returns_all_sources_including_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sources.json"
            path.write_text(json.dumps(EXISTING_JSON), encoding="utf-8")
            sources = list_sources(path)
            self.assertEqual([s["id"] for s in sources], ["openai_news"])


class UpdateSourceTest(unittest.TestCase):
    def test_updates_fields_and_preserves_others(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sources.json"
            path.write_text(json.dumps(EXISTING_JSON), encoding="utf-8")

            result = update_source("openai_news", {"enabled": False, "notes": "Disattivata a mano."}, path)

            self.assertTrue(result["ok"], result.get("error"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            entry = payload["sources"][0]
            self.assertFalse(entry["enabled"])
            self.assertEqual(entry["notes"], "Disattivata a mano.")
            self.assertEqual(entry["url"], "https://openai.com/news/rss.xml")  # untouched

    def test_reresolves_youtube_url_when_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sources.json"
            path.write_text(json.dumps(EXISTING_JSON), encoding="utf-8")
            with mock.patch(
                "urllib.request.urlopen",
                return_value=_fake_urlopen('{"externalId":"UCzzz999YYY888xxxxx"}'),
            ):
                result = update_source(
                    "openai_news", {"url": "https://www.youtube.com/@SomeChannel"}, path
                )
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(
                result["source"]["url"],
                "https://www.youtube.com/feeds/videos.xml?channel_id=UCzzz999YYY888xxxxx",
            )

    def test_rejects_ssrf_and_non_http_urls_on_update(self) -> None:
        # update_source previously had no scheme/host validation at all.
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sources.json"
            path.write_text(json.dumps(EXISTING_JSON), encoding="utf-8")
            for bad_url in (
                "file:///Users/x/.ssh/id_rsa#youtube.com/@a",
                "http://127.0.0.1:6379/youtube.com/@a",
                "http://169.254.169.254/latest/meta-data/",
            ):
                result = update_source("openai_news", {"url": bad_url}, path)
                self.assertFalse(result["ok"], bad_url)
            # The original source is untouched by the rejected attempts.
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["sources"][0]["url"], "https://openai.com/news/rss.xml")

    def test_rejects_unknown_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sources.json"
            path.write_text(json.dumps(EXISTING_JSON), encoding="utf-8")
            result = update_source("nope", {"enabled": False}, path)
            self.assertFalse(result["ok"])


class RemoveSourceTest(unittest.TestCase):
    def test_removes_and_keeps_others(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sources.json"
            doc = {
                "categories": ["general_ai"],
                "sources": [
                    EXISTING_JSON["sources"][0],
                    {"id": "other", "name": "Other", "url": "https://other.com/feed", "type": "rss",
                     "category_hint": "general_ai", "enabled": True, "notes": ""},
                ],
            }
            path.write_text(json.dumps(doc), encoding="utf-8")

            result = remove_source("openai_news", path)

            self.assertTrue(result["ok"], result.get("error"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([s["id"] for s in payload["sources"]], ["other"])

    def test_rejects_unknown_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sources.json"
            path.write_text(json.dumps(EXISTING_JSON), encoding="utf-8")
            result = remove_source("nope", path)
            self.assertFalse(result["ok"])


class ResolveYoutubeFeedUrlTest(unittest.TestCase):
    def test_leaves_non_youtube_url_untouched(self) -> None:
        url, error = resolve_youtube_feed_url("https://example.com/feed")
        self.assertEqual(url, "https://example.com/feed")
        self.assertEqual(error, "")

    def test_leaves_existing_feed_url_untouched(self) -> None:
        feed = "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc123DEF456ghijk"
        url, error = resolve_youtube_feed_url(feed)
        self.assertEqual(url, feed)
        self.assertEqual(error, "")

    def test_resolves_channel_id_url_without_network_call(self) -> None:
        with mock.patch("urllib.request.urlopen") as urlopen:
            url, error = resolve_youtube_feed_url("https://www.youtube.com/channel/UCabc123DEF456ghijk")
        urlopen.assert_not_called()
        self.assertEqual(url, "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc123DEF456ghijk")
        self.assertEqual(error, "")

    def test_resolves_handle_url_via_page_fetch(self) -> None:
        with mock.patch(
            "urllib.request.urlopen",
            return_value=_fake_urlopen('blah "externalId":"UCabc123DEF456ghijk" blah'),
        ):
            url, error = resolve_youtube_feed_url("https://www.youtube.com/@ProductHeroes")
        self.assertEqual(url, "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc123DEF456ghijk")
        self.assertEqual(error, "")

    def test_returns_error_when_channel_id_not_found(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=_fake_urlopen("no channel id here")):
            url, error = resolve_youtube_feed_url("https://www.youtube.com/@ProductHeroes")
        self.assertNotEqual(error, "")

    def test_returns_error_when_fetch_fails(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=RuntimeError("network down")):
            url, error = resolve_youtube_feed_url("https://www.youtube.com/@ProductHeroes")
        self.assertNotEqual(error, "")

    def test_rejects_oversized_channel_page_response(self) -> None:
        from src.net_guard import MAX_RESPONSE_BYTES

        oversized_html = "x" * (MAX_RESPONSE_BYTES + 1000)
        with mock.patch("urllib.request.urlopen", return_value=_fake_urlopen(oversized_html)):
            url, error = resolve_youtube_feed_url("https://www.youtube.com/@ProductHeroes")
        self.assertNotEqual(error, "")
        self.assertIn("grande", error)


class NewsBySourceTest(unittest.TestCase):
    def test_aggregates_and_deduplicates_across_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "2026-07-01").mkdir()
            (base / "2026-07-02").mkdir()
            save_json(base / "2026-07-01" / "news.json", [
                {"url": "https://a.com/1", "title": "A1", "source_id": "src", "published_at": "2026-07-01"},
                {"url": "https://b.com/1", "title": "B1", "source_id": "other", "published_at": "2026-07-01"},
            ])
            save_json(base / "2026-07-02" / "news.json", [
                {"url": "https://a.com/1", "title": "A1 aggiornata", "source_id": "src", "published_at": "2026-07-01"},
                {"url": "https://a.com/2", "title": "A2", "source_id": "src", "published_at": "2026-07-02"},
            ])

            items = load_news_by_source("src", base)

            self.assertEqual([i["title"] for i in items], ["A2", "A1 aggiornata"])  # newest first, deduped

    def test_unknown_source_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.assertEqual(load_news_by_source("nope", Path(tmp_dir)), [])


class LatestNewsBySourceTest(unittest.TestCase):
    def test_returns_most_recent_date_per_source(self) -> None:
        from src.storage.json_storage import latest_news_by_source

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "2026-07-01").mkdir()
            (base / "2026-07-02").mkdir()
            save_json(base / "2026-07-01" / "news.json", [
                {"url": "https://a.com/1", "source_id": "src", "published_at": "2026-07-01"},
                {"url": "https://b.com/1", "source_id": "other", "published_at": "2026-06-20"},
            ])
            save_json(base / "2026-07-02" / "news.json", [
                {"url": "https://a.com/2", "source_id": "src", "published_at": "2026-07-05"},
            ])

            latest = latest_news_by_source(base)

            self.assertEqual(latest["src"], "2026-07-05")
            self.assertEqual(latest["other"], "2026-06-20")

    def test_handles_mixed_date_formats_and_missing_dates(self) -> None:
        from src.storage.json_storage import latest_news_by_source

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "2026-07-01").mkdir()
            save_json(base / "2026-07-01" / "news.json", [
                {"url": "https://a.com/1", "source_id": "src", "published_at": "2026-07-01"},
                # RFC format, more recent: string comparison would get this wrong.
                {"url": "https://a.com/2", "source_id": "src", "published_at": "Fri, 10 Jul 2026 00:00:00 GMT"},
                # No published_at: falls back to collected_at.
                {"url": "https://a.com/3", "source_id": "vuota", "published_at": "", "collected_at": "2026-07-03"},
            ])

            latest = latest_news_by_source(base)

            self.assertEqual(latest["src"], "2026-07-10")
            self.assertEqual(latest["vuota"], "2026-07-03")


if __name__ == "__main__":
    unittest.main()
