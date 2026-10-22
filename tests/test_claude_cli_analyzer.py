"""Tests for the Claude CLI insight analyzer."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from src.processors import claude_cli_analyzer
from src.processors.claude_cli_analyzer import (
    ClaudeCliAnalyzer,
    ClaudeCliError,
    _build_batch_prompt,
    _parse_cli_result,
    filter_items_for_analysis,
)


class BuildBatchPromptTest(unittest.TestCase):
    def test_includes_goal_and_goal_field_when_goal_set(self) -> None:
        batch = [{"title": "A", "url": "https://a.com"}]
        prompt = _build_batch_prompt(batch, goal="Build hands-off AI")
        self.assertIn("Build hands-off AI", prompt)
        self.assertIn("relevance_to_goal", prompt)

    def test_omits_goal_field_when_no_goal(self) -> None:
        batch = [{"title": "A", "url": "https://a.com"}]
        prompt = _build_batch_prompt(batch)
        self.assertNotIn("relevance_to_goal", prompt)


def _wrapper(payload: object, *, is_error: bool = False) -> str:
    """Build a CLI wrapper like the real `--json-schema` output.

    The validated payload lands in `structured_output`; `result` holds the
    model's free-text turn.
    """
    return json.dumps(
        {
            "type": "result",
            "is_error": is_error,
            "result": "free text turn",
            "structured_output": payload,
        }
    )


def _legacy_result_wrapper(payload: object, *, as_string: bool = True) -> str:
    """Wrapper without structured_output, payload in `result` (fallback)."""
    result = json.dumps(payload) if as_string else payload
    return json.dumps({"type": "result", "is_error": False, "result": result})


class ParseCliResultTest(unittest.TestCase):
    def test_parses_structured_output(self) -> None:
        payload = {"items": [{"index": 0, "summary": "ok"}]}
        parsed = _parse_cli_result(_wrapper(payload))
        self.assertEqual(parsed[0]["summary"], "ok")

    def test_fallback_to_result_json_string(self) -> None:
        payload = {"items": [{"index": 0, "summary": "ok"}]}
        parsed = _parse_cli_result(_legacy_result_wrapper(payload))
        self.assertEqual(parsed[0]["summary"], "ok")

    def test_fallback_to_result_object(self) -> None:
        payload = {"items": [{"index": 0, "summary": "ok"}]}
        parsed = _parse_cli_result(_legacy_result_wrapper(payload, as_string=False))
        self.assertEqual(parsed[0]["index"], 0)

    def test_raises_on_cli_error_flag(self) -> None:
        with self.assertRaises(ClaudeCliError):
            _parse_cli_result(_wrapper({"items": []}, is_error=True))

    def test_raises_on_non_json(self) -> None:
        with self.assertRaises(ClaudeCliError):
            _parse_cli_result("not json at all")


class RunStructuredTest(unittest.TestCase):
    def test_parses_json_string_result_for_schema_file_provider(self) -> None:
        """Codex puts schema-constrained JSON in the final text event."""
        runner = ClaudeCliAnalyzer(provider="codex")
        raw = _legacy_result_wrapper({"overview": "ok"})
        with mock.patch.object(runner, "_invoke_with_retries", return_value=raw):
            self.assertEqual(runner.run_structured("prompt", {"type": "object"}), {"overview": "ok"})


class FilterItemsForAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)

    def test_keeps_recent_uncached_items(self) -> None:
        items = [{"url": "https://a.com", "published_at": "2026-06-10"}]
        selected = filter_items_for_analysis(items, set(), now=self.now)
        self.assertEqual(len(selected), 1)

    def test_skips_cached_items(self) -> None:
        items = [{"url": "https://a.com", "published_at": "2026-06-10"}]
        selected = filter_items_for_analysis(items, {"https://a.com"}, now=self.now)
        self.assertEqual(selected, [])

    def test_keeps_items_from_earlier_in_the_week(self) -> None:
        items = [{"url": "https://a.com", "published_at": "2026-06-07"}]
        selected = filter_items_for_analysis(items, set(), now=self.now)
        self.assertEqual(len(selected), 1)

    def test_skips_items_older_than_a_week(self) -> None:
        items = [{"url": "https://a.com", "published_at": "2026-06-02"}]
        selected = filter_items_for_analysis(items, set(), now=self.now)
        self.assertEqual(selected, [])

    def test_honours_a_narrower_window(self) -> None:
        items = [{"url": "https://a.com", "published_at": "2026-06-07"}]
        selected = filter_items_for_analysis(items, set(), now=self.now, max_age_hours=24)
        self.assertEqual(selected, [])

    def test_skips_items_without_url(self) -> None:
        items = [{"url": "", "published_at": "2026-06-10"}]
        selected = filter_items_for_analysis(items, set(), now=self.now)
        self.assertEqual(selected, [])

    def test_keeps_item_with_unparseable_date(self) -> None:
        items = [{"url": "https://a.com", "published_at": ""}]
        selected = filter_items_for_analysis(items, set(), now=self.now)
        self.assertEqual(len(selected), 1)


class AnalyzeItemsTest(unittest.TestCase):
    def _completed(self, stdout: str, returncode: int = 0, stderr: str = "") -> mock.Mock:
        completed = mock.Mock()
        completed.stdout = stdout
        completed.stderr = stderr
        completed.returncode = returncode
        return completed

    def test_maps_insights_to_urls(self) -> None:
        batch = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://b.com", "title": "B"},
        ]
        payload = {
            "items": [
                {
                    "index": 0,
                    "summary": "Riassunto A",
                    "why_it_matters": "Conta A",
                    "possible_action": "Azione A",
                    "relevance": 5,
                },
                {
                    "index": 1,
                    "summary": "Riassunto B",
                    "why_it_matters": "Conta B",
                    "possible_action": "Azione B",
                    "relevance": 3,
                },
            ]
        }
        analyzer = ClaudeCliAnalyzer(provider="claude", batch_size=10)
        with mock.patch.object(
            claude_cli_analyzer.subprocess,
            "run",
            return_value=self._completed(_wrapper(payload)),
        ):
            insights = analyzer.analyze_items(batch)

        self.assertEqual(insights["https://a.com"]["summary"], "Riassunto A")
        self.assertEqual(insights["https://a.com"]["relevance"], 5)
        self.assertEqual(insights["https://b.com"]["possible_action"], "Azione B")
        self.assertEqual(insights["https://a.com"]["source"], "claude_cli")

    def test_failed_batch_is_skipped_not_raised(self) -> None:
        batch = [{"url": "https://a.com", "title": "A"}]
        analyzer = ClaudeCliAnalyzer(batch_size=10, max_retries=0)
        with mock.patch.object(
            claude_cli_analyzer.subprocess,
            "run",
            return_value=self._completed("", returncode=1, stderr="boom"),
        ):
            insights = analyzer.analyze_items(batch)
        self.assertEqual(insights, {})

    def test_usage_includes_cache_tokens(self) -> None:
        analyzer = ClaudeCliAnalyzer()
        wrapper = json.dumps(
            {
                "type": "result",
                "is_error": False,
                "total_cost_usd": 0.13,
                "usage": {
                    "input_tokens": 4,
                    "cache_creation_input_tokens": 6000,
                    "cache_read_input_tokens": 12000,
                    "output_tokens": 1300,
                },
            }
        )
        analyzer._accumulate_usage(wrapper)
        summary = analyzer.usage_summary()
        self.assertEqual(summary["calls"], 1)
        self.assertEqual(summary["input_tokens"], 18004)  # 4 + 6000 + 12000
        self.assertEqual(summary["output_tokens"], 1300)
        self.assertAlmostEqual(summary["total_cost_usd"], 0.13)

    def test_relevance_is_clamped(self) -> None:
        batch = [{"url": "https://a.com", "title": "A"}]
        payload = {"items": [{"index": 0, "summary": "s", "relevance": 99}]}
        analyzer = ClaudeCliAnalyzer(provider="claude", batch_size=10)
        with mock.patch.object(
            claude_cli_analyzer.subprocess,
            "run",
            return_value=self._completed(_wrapper(payload)),
        ):
            insights = analyzer.analyze_items(batch)
        self.assertEqual(insights["https://a.com"]["relevance"], 5)


if __name__ == "__main__":
    unittest.main()


class ConfiguredModelTest(unittest.TestCase):
    """The model chosen in /api/llm-settings must actually reach the CLI.

    Regression: llm_model was persisted and echoed back as active, but no
    call site ever passed model= to ClaudeCliAnalyzer, so build_command
    always fell back to the provider's default -- setting a model was
    silently a no-op.
    """

    def test_unset_model_falls_back_to_the_configured_override(self) -> None:
        with mock.patch.object(claude_cli_analyzer, "_configured_model", return_value="opus"):
            analyzer = ClaudeCliAnalyzer()
        self.assertEqual(analyzer.model, "opus")

    def test_explicit_model_argument_still_wins(self) -> None:
        with mock.patch.object(claude_cli_analyzer, "_configured_model", return_value="opus"):
            analyzer = ClaudeCliAnalyzer(model="haiku")
        self.assertEqual(analyzer.model, "haiku")

    def test_configured_model_reaches_the_built_command(self) -> None:
        batch = [{"url": "https://a.com", "title": "A"}]
        payload = {"items": [{"index": 0, "summary": "s"}]}
        with mock.patch.object(claude_cli_analyzer, "_configured_model", return_value="opus"):
            analyzer = ClaudeCliAnalyzer(provider="claude", batch_size=10)
            completed = mock.Mock()
            completed.stdout = _wrapper(payload)
            completed.stderr = ""
            completed.returncode = 0
            with mock.patch.object(
                claude_cli_analyzer.subprocess, "run", return_value=completed
            ) as run_mock:
                analyzer.analyze_items(batch)

        command = run_mock.call_args[0][0]
        self.assertIn("--model", command)
        self.assertEqual(command[command.index("--model") + 1], "opus")
