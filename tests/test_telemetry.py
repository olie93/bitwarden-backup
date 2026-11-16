"""Exercise real job transitions with an in-memory Redis substitute."""

# ruff: noqa: PT009 -- unittest assertions keep this suite dependency-free.

import contextlib
import importlib
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeRedis:
    """Substitute only the Redis methods used by job-state updates."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """Return a stored JSON record."""
        return self.values.get(key)

    def setex(self, key: str, _ttl: int, value: str) -> None:
        """Store the JSON record without a clock dependency."""
        self.values[key] = value


class CompletionTelemetryTests(unittest.TestCase):
    """Verify completion signals reflect persisted state without private data."""

    def setUp(self) -> None:
        """Load the job manager with isolated infrastructure substitutes."""
        self.redis = FakeRedis()
        cache = types.ModuleType("api.cache")
        cache.__dict__["get_redis_client"] = lambda: self.redis
        config = types.ModuleType("api.config")
        config.__dict__["get_stream_token_ttl_seconds"] = lambda: 60
        with patch.dict(sys.modules, {"api.cache": cache, "api.config": config}):
            sys.modules.pop("api.jobs", None)
            self.jobs = importlib.import_module("api.jobs")
        self.manager = self.jobs.BackupJobManager()
        self.redis.values["backup_job:fixture"] = json.dumps({
            "id": "fixture", "status": "running", "started_at": "2026-11-11T03:00:00+00:00",
            "completed_at": None, "progress": 0, "error": "DO_NOT_EMIT_SECRET",
            "result": {"vault": "DO_NOT_EMIT_SECRET"},
        })

    def test_terminal_results_and_duplicate_updates(self) -> None:
        """Emit once per terminal transition and preserve its timestamp."""
        for status in (self.jobs.JobStatus.COMPLETED, self.jobs.JobStatus.FAILED, self.jobs.JobStatus.CANCELLED):
            with self.subTest(status=status):
                job = self.manager.get_job("fixture")
                job["status"] = "running"
                self.redis.setex("backup_job:fixture", 1, json.dumps(job))
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.manager.update_job("fixture", self.jobs.JobUpdate(status=status))
                    self.manager.update_job("fixture", self.jobs.JobUpdate(status=status))
                records = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["status"], status.value)
                self.assertEqual(set(records[0]), {"event", "status", "completed_at"})
                self.assertNotIn("DO_NOT_EMIT_SECRET", output.getvalue())
                self.assertEqual(records[0]["completed_at"], self.manager.get_job("fixture")["completed_at"])

    def test_nonterminal_updates_do_not_claim_success(self) -> None:
        """Keep progress and running state out of terminal telemetry."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.manager.update_job("fixture", self.jobs.JobUpdate(progress=50))
            self.manager.update_job("fixture", self.jobs.JobUpdate(status=self.jobs.JobStatus.RUNNING))
        self.assertEqual(output.getvalue(), "")

    def test_broken_output_cannot_change_persisted_result(self) -> None:
        """Keep a valid completed job even when stdout is broken."""
        with patch("builtins.print", side_effect=BrokenPipeError):
            update = self.jobs.JobUpdate(status=self.jobs.JobStatus.COMPLETED)
            self.assertTrue(self.manager.update_job("fixture", update))
        self.assertEqual(self.manager.get_job("fixture")["status"], "completed")


if __name__ == "__main__":
    unittest.main()
