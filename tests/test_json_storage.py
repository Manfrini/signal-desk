"""Tests for JSON storage helpers."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from datetime import date
from pathlib import Path

from src.storage import json_storage
from src.storage.json_storage import (
    load_json,
    load_video_transcripts,
    preview_processed_news_path,
    preview_raw_news_path,
    processed_news_path,
    raw_news_path,
    load_read_urls,
    mark_read_urls,
    save_json,
    save_video_transcript,
)


class JsonStorageTest(unittest.TestCase):
    def test_save_json_creates_nested_folders_and_loads_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "nested" / "news.json"
            payload = [{"title": "Novità AI", "url": "https://example.com"}]

            saved_path = save_json(target, payload)

            self.assertEqual(saved_path, target)
            self.assertTrue(target.exists())
            self.assertEqual(load_json(target), payload)
            self.assertIn("Novità AI", target.read_text(encoding="utf-8"))

    def test_save_json_overwrites_without_warning(self) -> None:
        # Overwriting is the normal, expected case on every run; it should not
        # log at WARNING (that used to drown out warnings that actually matter).
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "news.json"
            save_json(target, [{"title": "first"}])

            with self.assertLogs("src.storage.json_storage", level="INFO") as logs:
                save_json(target, [{"title": "second"}])

            self.assertFalse(
                any(record.levelname == "WARNING" for record in logs.records)
            )
            self.assertEqual(load_json(target), [{"title": "second"}])

    def test_save_json_is_atomic_no_leftover_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "news.json"
            save_json(target, [{"title": "first"}])
            save_json(target, [{"title": "second"}])

            leftovers = [p for p in Path(tmp_dir).iterdir() if p != target]
            self.assertEqual(leftovers, [])

    def test_load_json_returns_default_for_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing = Path(tmp_dir) / "missing.json"
            self.assertIsNone(load_json(missing))
            self.assertEqual(load_json(missing, default=[]), [])

    def test_load_json_quarantines_corrupt_file_and_returns_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "broken.json"
            target.write_text("{not valid json", encoding="utf-8")

            with self.assertLogs("src.storage.json_storage", level="ERROR"):
                result = load_json(target, default={})

            self.assertEqual(result, {})
            self.assertFalse(target.exists())
            quarantined = list(Path(tmp_dir).glob("broken.json.corrupt-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_text(encoding="utf-8"), "{not valid json")

    def test_news_paths_use_date_folder(self) -> None:
        selected_date = date(2026, 5, 5)

        self.assertEqual(
            raw_news_path(base_dir="data", date=selected_date),
            Path("data") / "2026-05-05" / "raw_news.json",
        )
        self.assertEqual(
            processed_news_path(base_dir="data", date=selected_date),
            Path("data") / "2026-05-05" / "news.json",
        )
        self.assertEqual(
            preview_raw_news_path(base_dir="data", date=selected_date),
            Path("data") / "2026-05-05" / "preview_raw_news.json",
        )
        self.assertEqual(
            preview_processed_news_path(base_dir="data", date=selected_date),
            Path("data") / "2026-05-05" / "preview_news.json",
        )

    def test_video_transcript_cache_round_trips_by_video_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "video_transcripts.json"

            self.assertEqual(load_video_transcripts(cache_path), {})

            save_video_transcript("abc123", {"text": "ciao mondo", "reason": ""}, cache_path)
            save_video_transcript("def456", {"text": "", "reason": "no_captions"}, cache_path)

            cached = load_video_transcripts(cache_path)
            self.assertEqual(cached["abc123"]["text"], "ciao mondo")
            self.assertEqual(cached["def456"]["reason"], "no_captions")

    def test_mark_read_urls_updates_read_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            read_state_path = Path(tmp_dir) / "read_urls.json"

            mark_read_urls(
                [
                    {"url": "https://example.com/a"},
                    {"url": "https://example.com/b"},
                ],
                read_state_path,
            )
            mark_read_urls(
                [
                    {"url": "https://example.com/a"},
                    {"url": "https://example.com/c"},
                ],
                read_state_path,
            )

            self.assertEqual(
                load_read_urls(read_state_path),
                {
                    "https://example.com/a",
                    "https://example.com/b",
                    "https://example.com/c",
                },
            )


class SavedArticlesTest(unittest.TestCase):
    def test_add_load_remove_saved_articles(self) -> None:
        from src.storage.json_storage import (
            add_saved_article,
            load_saved_articles,
            remove_saved_article,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "saved_articles.json"
            self.assertEqual(load_saved_articles(path), {})
            add_saved_article({"url": "https://a.com", "title": "A"}, path)
            add_saved_article({"url": "https://b.com", "title": "B"}, path)
            saved = load_saved_articles(path)
            self.assertEqual(set(saved.keys()), {"https://a.com", "https://b.com"})
            self.assertEqual(saved["https://a.com"]["title"], "A")
            self.assertIn("saved_at", saved["https://a.com"])
            remove_saved_article("https://a.com", path)
            self.assertEqual(set(load_saved_articles(path).keys()), {"https://b.com"})


class CollectionReportsTest(unittest.TestCase):
    def test_load_all_collection_reports_aggregates_dates(self) -> None:
        from src.storage.json_storage import load_all_collection_reports

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "2026-06-14").mkdir()
            (base / "2026-06-15").mkdir()
            save_json(base / "2026-06-14" / "collection_report.json", {"per_source": [{"source_id": "a"}]})
            save_json(base / "2026-06-15" / "collection_report.json", {"per_source": [{"source_id": "b"}]})
            reports = load_all_collection_reports(base)
            self.assertEqual(set(reports.keys()), {"2026-06-14", "2026-06-15"})
            self.assertEqual(reports["2026-06-15"]["per_source"][0]["source_id"], "b")


class LlmUsageLogTest(unittest.TestCase):
    def test_append_and_load_usage_log(self) -> None:
        from src.storage.json_storage import append_llm_usage_log, load_llm_usage_log

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "llm_usage_log.json"
            self.assertEqual(load_llm_usage_log(path), [])
            append_llm_usage_log({"date": "2026-06-10", "calls": 6, "total_cost_usd": 0.48}, path)
            append_llm_usage_log({"date": "2026-06-11", "calls": 1, "total_cost_usd": 0.08}, path)
            runs = load_llm_usage_log(path)
            self.assertEqual(len(runs), 2)
            self.assertEqual(runs[0]["date"], "2026-06-10")
            self.assertEqual(runs[1]["calls"], 1)


class ConcurrencyTest(unittest.TestCase):
    def test_concurrent_save_deep_dive_does_not_lose_updates(self) -> None:
        import threading

        from src.storage.json_storage import load_deep_dives, save_deep_dive

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "deep_dives.json"
            urls = [f"https://example.com/{i}" for i in range(20)]

            def worker(url: str) -> None:
                save_deep_dive(url, {"overview": url}, path)

            threads = [threading.Thread(target=worker, args=(url,)) for url in urls]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            dives = load_deep_dives(path)
            self.assertEqual(set(dives.keys()), set(urls))

    def test_concurrent_append_llm_usage_log_does_not_lose_records(self) -> None:
        import threading

        from src.storage.json_storage import append_llm_usage_log, load_llm_usage_log

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "llm_usage_log.json"

            def worker(i: int) -> None:
                append_llm_usage_log({"date": f"run-{i}", "calls": 1}, path)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            runs = load_llm_usage_log(path)
            self.assertEqual(len(runs), 20)
            self.assertEqual({r["date"] for r in runs}, {f"run-{i}" for i in range(20)})


class TodayUtcTest(unittest.TestCase):
    def test_matches_the_utc_date_not_necessarily_the_local_one(self) -> None:
        from datetime import datetime, timezone

        from src.storage.json_storage import today_utc

        self.assertEqual(today_utc(), datetime.now(timezone.utc).date())

    def test_date_folder_default_uses_today_utc(self) -> None:
        from src.storage.json_storage import date_folder, today_utc

        self.assertEqual(date_folder("data"), Path("data") / today_utc().isoformat())


if __name__ == "__main__":
    unittest.main()


class SeedMergeTest(unittest.TestCase):
    """New catalogue entries must reach existing installs.

    The YAML seed is migrated once; without an additive merge an install
    stays frozen at the catalogue it was created with, and sources added
    upstream later are invisible with no way to discover them.
    """

    def _seed(self, tmp: Path, ids, enabled=True) -> Path:
        import yaml

        seed = {"categories": ["general_ai"], "sources": [
            {"id": i, "name": i, "company": "X", "url": f"https://{i}.com/feed",
             "allowed_domains": [f"{i}.com"], "type": "rss", "category_hint": "general_ai",
             "source_group": "foundation_models", "signal_type": "official_news",
             "enabled": enabled, "notes": ""} for i in ids
        ]}
        path = tmp / "sources.yaml"
        path.write_text(yaml.safe_dump(seed, sort_keys=False), encoding="utf-8")
        return path

    def test_fresh_install_takes_the_seed_verbatim_including_enabled_flags(self) -> None:
        from src.storage.json_storage import load_sources_config

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            seed = self._seed(tmp, ["a", "b"], enabled=True)
            result = load_sources_config(tmp / "sources.json", seed)

            self.assertEqual([s["id"] for s in result["sources"]], ["a", "b"])
            self.assertTrue(all(s["enabled"] for s in result["sources"]))

    def test_existing_install_gains_new_seed_sources_disabled(self) -> None:
        from src.storage.json_storage import load_sources_config, save_json

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            current = tmp / "sources.json"
            save_json(current, {"categories": [], "sources": [
                {"id": "a", "name": "mine", "enabled": True, "notes": "hand edited"},
            ]})
            seed = self._seed(tmp, ["a", "b", "c"], enabled=True)

            with mock.patch.object(json_storage, "DEFAULT_SOURCES_PATH", current):
                result = load_sources_config(current, seed)

            by_id = {s["id"]: s for s in result["sources"]}
            self.assertEqual(set(by_id), {"a", "b", "c"})
            # Untouched: the user's own edit and enabled flag win.
            self.assertEqual(by_id["a"]["name"], "mine")
            self.assertEqual(by_id["a"]["notes"], "hand edited")
            self.assertTrue(by_id["a"]["enabled"])
            # New arrivals never start collecting on their own.
            self.assertFalse(by_id["b"]["enabled"])
            self.assertFalse(by_id["c"]["enabled"])

    def test_merge_is_persisted_so_it_happens_once(self) -> None:
        from src.storage.json_storage import load_json, load_sources_config, save_json

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            current = tmp / "sources.json"
            save_json(current, {"categories": [], "sources": [{"id": "a", "enabled": True}]})
            seed = self._seed(tmp, ["a", "b"])

            with mock.patch.object(json_storage, "DEFAULT_SOURCES_PATH", current):
                load_sources_config(current, seed)

            on_disk = load_json(current)
            self.assertEqual({s["id"] for s in on_disk["sources"]}, {"a", "b"})

    def test_no_new_ids_leaves_the_file_alone(self) -> None:
        from src.storage.json_storage import load_sources_config, save_json

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            current = tmp / "sources.json"
            save_json(current, {"categories": [], "sources": [{"id": "a", "enabled": True}]})
            seed = self._seed(tmp, ["a"])

            with mock.patch.object(json_storage, "DEFAULT_SOURCES_PATH", current):
                result = load_sources_config(current, seed)

            self.assertEqual(len(result["sources"]), 1)

    def test_explicit_non_default_path_is_never_merged_into(self) -> None:
        # Tests and candidate-verification runs point at their own file and
        # must get exactly that file back, not the shipped catalogue.
        from src.storage.json_storage import load_sources_config, save_json

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            other = tmp / "somewhere_else.json"
            save_json(other, {"categories": [], "sources": [{"id": "only_mine"}]})
            seed = self._seed(tmp, ["a", "b", "c"])

            result = load_sources_config(other, seed)

            self.assertEqual([s["id"] for s in result["sources"]], ["only_mine"])

    def test_user_removed_source_is_not_silently_resurrected_as_enabled(self) -> None:
        # A source the user deleted comes back (it is in the seed), but never
        # switched on behind their back.
        from src.storage.json_storage import load_sources_config, save_json

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            current = tmp / "sources.json"
            save_json(current, {"categories": [], "sources": []})
            seed = self._seed(tmp, ["a"], enabled=True)

            with mock.patch.object(json_storage, "DEFAULT_SOURCES_PATH", current):
                result = load_sources_config(current, seed)

            self.assertFalse(result["sources"][0]["enabled"])
