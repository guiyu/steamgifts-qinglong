import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "src" / "steam_gift"
sys.path.insert(0, str(SOURCE))

from heartbeat import Heartbeat, write_heartbeat


class HeartbeatTests(unittest.TestCase):
    def heartbeat(self, **overrides):
        values = {
            "schema_version": 1,
            "started_at": "2026-10-07T12:00:00+00:00",
            "finished_at": "2026-10-07T12:01:00+00:00",
            "status": "success",
            "reason": "completed",
            "eligible_count": 2,
            "selected_count": 2,
            "entered_count": 1,
            "points_before": 10,
            "points_after": 4,
            "process_id": 123,
        }
        values.update(overrides)
        return Heartbeat(**values)

    def test_writes_complete_schema_atomically_with_private_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "bot-heartbeat.json"
            path.parent.mkdir()
            path.write_text('{"old": true}', encoding="utf-8")

            with mock.patch("heartbeat.os.replace", wraps=os.replace) as replace:
                write_heartbeat(path, self.heartbeat())

            replace.assert_called_once()
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(payload),
                {
                    "schema_version",
                    "started_at",
                    "finished_at",
                    "status",
                    "reason",
                    "eligible_count",
                    "selected_count",
                    "entered_count",
                    "points_before",
                    "points_after",
                    "process_id",
                },
            )
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob(".bot-heartbeat.json.*")), [])

    def test_accepts_only_documented_terminal_statuses(self):
        allowed = (
            "success",
            "no_eligible_giveaways",
            "authentication_blocked",
            "site_error",
            "internal_error",
        )
        for status_name in allowed:
            with self.subTest(status=status_name):
                self.heartbeat(status=status_name)

        with self.assertRaises(ValueError):
            self.heartbeat(status="running")

    def test_rejects_non_iso_timestamps_and_non_integer_counters(self):
        with self.assertRaises(ValueError):
            self.heartbeat(started_at="not-a-time")
        with self.assertRaises(ValueError):
            self.heartbeat(entered_count="1")

    def test_refuses_sensitive_keys_urls_and_identity_values(self):
        sensitive_reasons = (
            "cookie expired",
            "token refresh failed",
            "PHPSESSID invalid",
            "cf_clearance missing",
            "https://www.steamgifts.com/giveaway/example",
            "username=someone",
            "email=user@example.com",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bot-heartbeat.json"
            for reason in sensitive_reasons:
                with self.subTest(reason=reason), self.assertRaises(ValueError):
                    write_heartbeat(path, self.heartbeat(reason=reason))
                self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
