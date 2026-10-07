import sys
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from watchdog import (
    Action,
    Decision,
    QinglongClient,
    Snapshot,
    TASK_NAME,
    collect_snapshot,
    decide,
    load_qinglong_token,
    perform_action,
    record_result,
    watchdog_lock,
)


NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
COMMAND = "flock -n /tmp/steamgifts.lock bash -lc 'cd /ql/data/scripts/steam_gift && python3 sg.py'"


def healthy_snapshot(**overrides):
    snapshot = Snapshot(
        docker_available=True,
        container_exists=True,
        container_running=True,
        container_health="healthy",
        container_started_at=NOW - timedelta(hours=2),
        panel_available=True,
        task_count=1,
        task_id=1,
        task_enabled=True,
        task_schedule="0 * * * *",
        task_command=COMMAND,
        last_task_run_at=NOW - timedelta(minutes=30),
        process_started_at=None,
        heartbeat_status="success",
        heartbeat_finished_at=NOW - timedelta(minutes=30),
        consecutive_review_failures=0,
    )
    return replace(snapshot, **overrides)


class DecisionPolicyTests(unittest.TestCase):
    def assert_decision(self, snapshot, action, state="degraded", reason=None):
        decision = decide(snapshot, NOW)
        self.assertEqual(decision.action, action)
        self.assertEqual(decision.state, state)
        if reason is not None:
            self.assertEqual(decision.reason, reason)

    def test_docker_unavailable_is_reported_without_blind_recovery(self):
        self.assert_decision(
            healthy_snapshot(docker_available=False),
            Action.NONE,
            reason="docker_unavailable",
        )

    def test_missing_or_stopped_container_is_started(self):
        for snapshot in (
            healthy_snapshot(container_exists=False, container_running=False),
            healthy_snapshot(container_running=False),
        ):
            with self.subTest(snapshot=snapshot):
                self.assert_decision(snapshot, Action.START_CONTAINER)

    def test_startup_grace_waits_without_restarting(self):
        self.assert_decision(
            healthy_snapshot(
                container_health="starting",
                container_started_at=NOW - timedelta(minutes=2),
            ),
            Action.NONE,
            state="starting",
            reason="container_startup_grace",
        )

    def test_unhealthy_container_or_panel_is_restarted(self):
        for snapshot in (
            healthy_snapshot(container_health="unhealthy"),
            healthy_snapshot(panel_available=False),
        ):
            with self.subTest(snapshot=snapshot):
                self.assert_decision(snapshot, Action.RESTART_CONTAINER)

    def test_task_missing_disabled_or_drifted_is_repaired(self):
        cases = (
            (healthy_snapshot(task_count=0, task_id=None), Action.CREATE_TASK),
            (healthy_snapshot(task_enabled=False), Action.ENABLE_TASK),
            (healthy_snapshot(task_schedule="*/5 * * * *"), Action.UPDATE_TASK),
            (healthy_snapshot(task_command="python3 wrong.py"), Action.UPDATE_TASK),
        )
        for snapshot, action in cases:
            with self.subTest(action=action):
                self.assert_decision(snapshot, action)

    def test_duplicate_tasks_are_not_mutated(self):
        self.assert_decision(
            healthy_snapshot(task_count=2),
            Action.NONE,
            reason="duplicate_tasks",
        )

    def test_stale_task_runs_when_no_process_is_active(self):
        self.assert_decision(
            healthy_snapshot(
                last_task_run_at=NOW - timedelta(hours=2),
                heartbeat_finished_at=NOW - timedelta(hours=2),
            ),
            Action.RUN_TASK,
            reason="task_stale",
        )

    def test_process_older_than_fifty_minutes_is_stopped(self):
        self.assert_decision(
            healthy_snapshot(process_started_at=NOW - timedelta(minutes=51)),
            Action.STOP_TASK,
            reason="process_stuck",
        )

    def test_recent_success_is_healthy(self):
        self.assert_decision(
            healthy_snapshot(), Action.NONE, state="healthy", reason="ok"
        )

    def test_authentication_block_never_restarts_or_reruns(self):
        self.assert_decision(
            healthy_snapshot(
                heartbeat_status="authentication_blocked",
                heartbeat_finished_at=NOW - timedelta(hours=3),
                last_task_run_at=NOW - timedelta(hours=3),
            ),
            Action.NONE,
            reason="authentication_blocked",
        )

    def test_review_outage_degrades_only_after_repeated_failures(self):
        first = decide(
            healthy_snapshot(
                heartbeat_status="site_error",
                consecutive_review_failures=1,
            ),
            NOW,
        )
        repeated = decide(
            healthy_snapshot(
                heartbeat_status="site_error",
                consecutive_review_failures=3,
            ),
            NOW,
        )

        self.assertEqual((first.state, first.action), ("healthy", Action.NONE))
        self.assertEqual(
            (repeated.state, repeated.action, repeated.reason),
            ("degraded", Action.NONE, "review_service_unavailable"),
        )

    def test_recovery_snapshot_returns_to_healthy(self):
        degraded = decide(healthy_snapshot(panel_available=False), NOW)
        recovered = decide(healthy_snapshot(), NOW)

        self.assertEqual(degraded.state, "degraded")
        self.assertEqual(recovered.state, "healthy")


class FakeRunner:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.callback:
            self.callback(args, kwargs)


class RuntimeAdapterTests(unittest.TestCase):
    def test_collects_container_task_process_and_heartbeat_from_injected_adapters(self):
        class Result:
            def __init__(self, returncode=0, stdout=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = ""

        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            if args[:2] == ["docker", "info"]:
                return Result(stdout='"27.0"')
            if args[:2] == ["docker", "inspect"]:
                return Result(
                    stdout=json.dumps(
                        {
                            "Running": True,
                            "StartedAt": "2026-10-07T09:00:00.123456789Z",
                            "Health": {"Status": "healthy"},
                        }
                    )
                )
            if args[:2] == ["docker", "exec"]:
                return Result(stdout="3060 python3 sg.py\n")
            raise AssertionError(args)

        def http_call(_method, _url, _headers, _body, _timeout):
            return {
                "data": [
                    {
                        "id": 7,
                        "name": TASK_NAME,
                        "schedule": "0 * * * *",
                        "command": COMMAND,
                        "isDisabled": 0,
                        "last_execution_time": int((NOW - timedelta(minutes=20)).timestamp()),
                    }
                ]
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            token_path = project / "data" / "config" / "token.json"
            token_path.parent.mkdir(parents=True)
            token_path.write_text(
                json.dumps(
                    {"value": "secret", "expiration": int(NOW.timestamp()) + 3600}
                ),
                encoding="utf-8",
            )
            heartbeat_path = project / "data" / "watchdog" / "bot-heartbeat.json"
            heartbeat_path.parent.mkdir(parents=True)
            heartbeat_path.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "finished_at": (NOW - timedelta(minutes=10)).isoformat(),
                    }
                ),
                encoding="utf-8",
            )

            snapshot, client = collect_snapshot(
                project,
                command_runner=runner,
                http_call=http_call,
                panel_probe=lambda url, _timeout: url == "http://127.0.0.1:5700/",
                clock=lambda: NOW,
            )

        self.assertIsNotNone(client)
        self.assertTrue(snapshot.container_running)
        self.assertEqual(snapshot.task_id, 7)
        self.assertEqual(snapshot.heartbeat_status, "success")
        self.assertEqual(snapshot.process_started_at, NOW - timedelta(seconds=3060))
        self.assertEqual(decide(snapshot, NOW).action, Action.STOP_TASK)
        self.assertEqual(len(calls), 3)

    def test_start_and_restart_use_exact_compose_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            for action, expected in (
                (Action.START_CONTAINER, ["docker", "compose", "up", "-d"]),
                (
                    Action.RESTART_CONTAINER,
                    ["docker", "compose", "restart", "qinglong"],
                ),
            ):
                runner = FakeRunner()
                perform_action(
                    Decision("degraded", "test", action),
                    healthy_snapshot(),
                    project,
                    runner,
                    qinglong=None,
                )
                self.assertEqual(len(runner.calls), 1)
                self.assertEqual(runner.calls[0][0], expected)
                self.assertEqual(runner.calls[0][1]["cwd"], project)

    def test_qinglong_client_is_localhost_only_and_uses_bearer_header(self):
        calls = []

        def http_call(method, url, headers, body, timeout):
            calls.append((method, url, headers, body, timeout))
            return {"data": [{"id": 1, "name": TASK_NAME}]}

        client = QinglongClient("http://127.0.0.1:5700", "secret-value", http_call)
        tasks = client.list_tasks()

        self.assertEqual(tasks[0]["id"], 1)
        self.assertEqual(calls[0][0:2], ("GET", "http://127.0.0.1:5700/open/crons"))
        self.assertEqual(calls[0][2]["Authorization"], "Bearer secret-value")
        self.assertNotIn("secret-value", repr(tasks))
        with self.assertRaises(ValueError):
            QinglongClient("https://example.com", "secret-value", http_call)

    def test_expired_token_refreshes_through_qinglong_script_without_logging_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            token_path = project / "data" / "config" / "token.json"
            token_path.parent.mkdir(parents=True)
            token_path.write_text(
                json.dumps({"token": "expired-secret", "expiration": 1}),
                encoding="utf-8",
            )

            def refresh(_args, _kwargs):
                token_path.write_text(
                    json.dumps(
                        {
                            "value": "fresh-secret",
                            "expiration": int(NOW.timestamp()) + 3600,
                        }
                    ),
                    encoding="utf-8",
                )

            runner = FakeRunner(refresh)
            token = load_qinglong_token(project, runner, NOW)

            self.assertEqual(token, "fresh-secret")
            self.assertEqual(
                runner.calls[0][0],
                [
                    "docker",
                    "exec",
                    "steamgifts-qinglong",
                    "bash",
                    "-lc",
                    "source /ql/shell/share.sh; source /ql/shell/api.sh",
                ],
            )
            self.assertNotIn("expired-secret", repr(runner.calls))
            self.assertNotIn("fresh-secret", repr(runner.calls))

    def test_task_action_performs_only_one_api_mutation(self):
        calls = []

        def http_call(method, url, headers, body, timeout):
            calls.append((method, url, body))
            return {"data": True}

        with tempfile.TemporaryDirectory() as temp_dir:
            client = QinglongClient("http://127.0.0.1:5700", "secret", http_call)
            perform_action(
                Decision("degraded", "task_stale", Action.RUN_TASK),
                healthy_snapshot(task_id=7),
                Path(temp_dir),
                FakeRunner(),
                client,
            )

        self.assertEqual(calls, [("PUT", "http://127.0.0.1:5700/open/crons/run", [7])])

    def test_nonblocking_lock_prevents_five_minute_overlap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "watchdog.lock"
            with watchdog_lock(path) as first:
                with watchdog_lock(path) as second:
                    self.assertTrue(first)
                    self.assertFalse(second)

    def test_notifications_only_on_transitions_and_state_is_redacted(self):
        notifications = []
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            decision = Decision("degraded", "panel_unavailable", Action.RESTART_CONTAINER)
            record_result(project, decision, NOW, notifications.append)
            record_result(project, decision, NOW + timedelta(minutes=5), notifications.append)
            record_result(
                project,
                Decision("healthy", "ok", Action.NONE),
                NOW + timedelta(minutes=10),
                notifications.append,
            )

            self.assertEqual(len(notifications), 2)
            state = (project / "data" / "watchdog" / "state.json").read_text()
            log = (project / "data" / "watchdog" / "watchdog.log").read_text()
            self.assertNotIn("secret", state + log)

            record_result(
                project,
                Decision("degraded", "token=secret-value", Action.NONE),
                NOW + timedelta(minutes=15),
                notifications.append,
            )
            combined = state + log + (
                project / "data" / "watchdog" / "state.json"
            ).read_text() + (
                project / "data" / "watchdog" / "watchdog.log"
            ).read_text()
            self.assertNotIn("secret-value", combined)


if __name__ == "__main__":
    unittest.main()
