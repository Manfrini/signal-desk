"""Tests for the local analysis server."""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from src.server import AnalysisRunner


def _wait_status(runner: AnalysisRunner, target: str, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if runner.status == target:
            return True
        time.sleep(0.01)
    return False


class AnalysisRunnerTest(unittest.TestCase):
    def test_successful_run_sets_done_and_keeps_result(self) -> None:
        runner = AnalysisRunner(runner=lambda: {"ok": True})
        self.assertTrue(runner.start())
        self.assertTrue(_wait_status(runner, "done"))
        self.assertEqual(runner.last_result, {"ok": True})
        self.assertEqual(runner.snapshot()["status"], "done")

    def test_failing_run_sets_error(self) -> None:
        def boom() -> None:
            raise RuntimeError("kaboom")

        runner = AnalysisRunner(runner=boom)
        runner.start()
        self.assertTrue(_wait_status(runner, "error"))
        self.assertIn("kaboom", runner.snapshot()["error"])

    def test_concurrent_start_is_rejected_while_running(self) -> None:
        gate = threading.Event()
        runner = AnalysisRunner(runner=lambda: gate.wait(2))
        self.assertTrue(runner.start())
        self.assertTrue(_wait_status(runner, "running"))
        self.assertFalse(runner.start())  # already running
        gate.set()
        self.assertTrue(_wait_status(runner, "done"))


    def test_start_with_source_id_exposes_it_in_snapshot(self) -> None:
        runner = AnalysisRunner(runner=lambda: {"ok": True})
        self.assertTrue(runner.start(source_id="yt_ycombinator"))
        self.assertTrue(_wait_status(runner, "done"))
        self.assertEqual(runner.snapshot()["source_id"], "yt_ycombinator")

    def test_start_without_source_id_reports_empty_string(self) -> None:
        runner = AnalysisRunner(runner=lambda: {"ok": True})
        runner.start()
        self.assertTrue(_wait_status(runner, "done"))
        self.assertEqual(runner.snapshot()["source_id"], "")

    def test_default_runner_passes_source_id_to_daily_pipeline(self) -> None:
        runner = AnalysisRunner()
        with mock.patch("src.pipeline.daily_pipeline.DailyPipeline") as pipeline_cls:
            pipeline_cls.return_value.run.return_value = {"ok": True}
            runner.start(source_id="yt_ycombinator")
            self.assertTrue(_wait_status(runner, "done"))
        pipeline_cls.return_value.run.assert_called_once_with(dry_run=False, source_id="yt_ycombinator")


class ServeDesignAssetTest(unittest.TestCase):
    def _capture(self):
        calls = []

        def send(code, body, ctype):
            calls.append((code, ctype, body))

        return send, calls

    def test_serves_tokens_css_with_correct_content_type(self) -> None:
        from src.server import _serve_design_asset

        send, calls = self._capture()
        handled = _serve_design_asset(send, "/design/mm/tokens.css")
        self.assertTrue(handled)
        code, ctype, body = calls[-1]
        self.assertEqual(code, 200)
        self.assertIn("text/css", ctype)
        self.assertIn(b"--bg", body)

    def test_serves_logo_and_font(self) -> None:
        from src.server import _serve_design_asset

        send, calls = self._capture()
        self.assertTrue(_serve_design_asset(send, "/design/mm/logo.svg"))
        self.assertEqual(calls[-1][1], "image/svg+xml")
        self.assertTrue(_serve_design_asset(send, "/design/mm/fonts/InterVariable.woff2"))
        self.assertEqual(calls[-1][1], "font/woff2")

    def test_returns_404_for_missing_asset(self) -> None:
        from src.server import _serve_design_asset

        send, calls = self._capture()
        self.assertTrue(_serve_design_asset(send, "/design/mm/nope.css"))
        self.assertEqual(calls[-1][0], 404)

    def test_blocks_path_traversal(self) -> None:
        from src.server import _serve_design_asset

        send, calls = self._capture()
        self.assertTrue(_serve_design_asset(send, "/design/mm/../../src/server.py"))
        self.assertEqual(calls[-1][0], 403)

    def test_ignores_unrelated_paths(self) -> None:
        from src.server import _serve_design_asset

        send, calls = self._capture()
        handled = _serve_design_asset(send, "/api/status")
        self.assertFalse(handled)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
