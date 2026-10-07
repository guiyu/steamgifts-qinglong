#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable


TASK_NAME = "SteamGifts auto-entry"
TASK_SCHEDULE = "0 * * * *"
TASK_COMMAND = (
    "flock -n /tmp/steamgifts.lock bash -lc "
    "'cd /ql/data/scripts/steam_gift && python3 sg.py'"
)
STARTUP_GRACE = timedelta(minutes=5)
STUCK_AFTER = timedelta(minutes=50)
STALE_AFTER = timedelta(minutes=90)
SENSITIVE = re.compile(
    r"(?:cookie|token|php[a-z0-9_]*|clearance|https?://|[a-z0-9._%+-]+@[a-z0-9.-]+\.)",
    re.IGNORECASE,
)
DOCKER_BIN = os.environ.get("STEAMGIFTS_DOCKER_BIN", "docker")


class Action(str, Enum):
    NONE = "none"
    START_CONTAINER = "start_container"
    RESTART_CONTAINER = "restart_container"
    CREATE_TASK = "create_task"
    ENABLE_TASK = "enable_task"
    UPDATE_TASK = "update_task"
    RUN_TASK = "run_task"
    STOP_TASK = "stop_task"


@dataclass(frozen=True)
class Snapshot:
    docker_available: bool
    container_exists: bool
    container_running: bool
    container_health: str
    container_started_at: datetime | None
    panel_available: bool
    task_count: int
    task_id: int | str | None
    task_enabled: bool
    task_schedule: str
    task_command: str
    last_task_run_at: datetime | None
    process_started_at: datetime | None
    heartbeat_status: str | None
    heartbeat_finished_at: datetime | None
    consecutive_review_failures: int
    task_api_available: bool = True


@dataclass(frozen=True)
class Decision:
    state: str
    reason: str
    action: Action


def _decision(state: str, reason: str, action: Action = Action.NONE) -> Decision:
    return Decision(state=state, reason=reason, action=action)


def decide(snapshot: Snapshot, now: datetime) -> Decision:
    if not snapshot.docker_available:
        return _decision("degraded", "docker_unavailable")
    if not snapshot.container_exists or not snapshot.container_running:
        return _decision("degraded", "container_stopped", Action.START_CONTAINER)

    if snapshot.container_health == "starting":
        age = (
            now - snapshot.container_started_at
            if snapshot.container_started_at is not None
            else STARTUP_GRACE
        )
        if age <= STARTUP_GRACE:
            return _decision("starting", "container_startup_grace")
        return _decision(
            "degraded", "container_unhealthy", Action.RESTART_CONTAINER
        )
    if snapshot.container_health != "healthy":
        return _decision(
            "degraded", "container_unhealthy", Action.RESTART_CONTAINER
        )
    if not snapshot.panel_available:
        return _decision("degraded", "panel_unavailable", Action.RESTART_CONTAINER)

    if snapshot.heartbeat_status == "authentication_blocked":
        return _decision("degraded", "authentication_blocked")

    if not snapshot.task_api_available:
        return _decision("degraded", "task_api_unavailable")

    if snapshot.task_count == 0:
        return _decision("degraded", "task_missing", Action.CREATE_TASK)
    if snapshot.task_count != 1:
        return _decision("degraded", "duplicate_tasks")
    if not snapshot.task_enabled:
        return _decision("degraded", "task_disabled", Action.ENABLE_TASK)
    if (
        snapshot.task_schedule != TASK_SCHEDULE
        or snapshot.task_command != TASK_COMMAND
    ):
        return _decision("degraded", "task_drifted", Action.UPDATE_TASK)

    if (
        snapshot.process_started_at is not None
        and now - snapshot.process_started_at > STUCK_AFTER
    ):
        return _decision("degraded", "process_stuck", Action.STOP_TASK)

    if (
        snapshot.heartbeat_status == "site_error"
        and snapshot.consecutive_review_failures >= 3
    ):
        return _decision("degraded", "review_service_unavailable")

    freshness = [
        value
        for value in (snapshot.last_task_run_at, snapshot.heartbeat_finished_at)
        if value is not None
    ]
    last_activity = max(freshness) if freshness else None
    if (
        snapshot.process_started_at is None
        and (last_activity is None or now - last_activity > STALE_AFTER)
    ):
        return _decision("degraded", "task_stale", Action.RUN_TASK)

    return _decision("healthy", "ok")


def _http_json(method, url, headers, body, timeout):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class QinglongClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        http_call: Callable[..., dict[str, Any]] = _http_json,
    ):
        if base_url.rstrip("/") != "http://127.0.0.1:5700":
            raise ValueError("Qinglong API must remain localhost-only")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._http_call = http_call

    def request(self, method: str, path: str, body=None):
        if not path.startswith("/open/crons"):
            raise ValueError("unsupported Qinglong API path")
        return self._http_call(
            method,
            self.base_url + path,
            {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            body,
            15,
        )

    def list_tasks(self):
        result = self.request("GET", "/open/crons")
        data = result.get("data", [])
        if isinstance(data, dict):
            return data.get("data", data.get("records", []))
        return data if isinstance(data, list) else []

    def create_task(self):
        return self.request(
            "POST",
            "/open/crons",
            {"name": TASK_NAME, "command": TASK_COMMAND, "schedule": TASK_SCHEDULE},
        )

    def enable_task(self, task_id):
        return self.request("PUT", "/open/crons/enable", [task_id])

    def update_task(self, task_id):
        return self.request(
            "PUT",
            "/open/crons",
            {
                "id": task_id,
                "name": TASK_NAME,
                "command": TASK_COMMAND,
                "schedule": TASK_SCHEDULE,
            },
        )

    def run_task(self, task_id):
        return self.request("PUT", "/open/crons/run", [task_id])

    def stop_task(self, task_id):
        return self.request("PUT", "/open/crons/stop", [task_id])


def _token_expiration(payload: dict[str, Any]) -> int:
    value = payload.get("expiration", payload.get("expire", 0))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def load_qinglong_token(project_dir: Path, command_runner, now: datetime) -> str:
    project_dir = Path(project_dir)
    token_path = project_dir / "data" / "config" / "token.json"

    def read_payload():
        return json.loads(token_path.read_text(encoding="utf-8"))

    payload = read_payload()
    if _token_expiration(payload) <= int(now.timestamp()) + 60:
        command_runner(
            [
                DOCKER_BIN,
                "exec",
                "steamgifts-qinglong",
                "bash",
                "-lc",
                "source /ql/shell/share.sh; source /ql/shell/api.sh",
            ],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        payload = read_payload()
    token = payload.get("token", payload.get("value"))
    if not isinstance(token, str) or not token:
        raise RuntimeError("Qinglong token is unavailable")
    return token


def perform_action(
    decision: Decision,
    snapshot: Snapshot,
    project_dir: Path,
    command_runner,
    qinglong: QinglongClient | None,
) -> None:
    if decision.action == Action.NONE:
        return
    if decision.action == Action.START_CONTAINER:
        command_runner(
            [DOCKER_BIN, "compose", "up", "-d"],
            cwd=Path(project_dir),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return
    if decision.action == Action.RESTART_CONTAINER:
        command_runner(
            [DOCKER_BIN, "compose", "restart", "qinglong"],
            cwd=Path(project_dir),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return
    if qinglong is None:
        raise RuntimeError("Qinglong client required for task recovery")
    if decision.action == Action.CREATE_TASK:
        qinglong.create_task()
    elif decision.action == Action.ENABLE_TASK:
        qinglong.enable_task(snapshot.task_id)
    elif decision.action == Action.UPDATE_TASK:
        qinglong.update_task(snapshot.task_id)
    elif decision.action == Action.RUN_TASK:
        qinglong.run_task(snapshot.task_id)
    elif decision.action == Action.STOP_TASK:
        qinglong.stop_task(snapshot.task_id)


@contextlib.contextmanager
def watchdog_lock(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open("a+", encoding="utf-8")
    os.chmod(path, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        file.close()


def _safe(value: str) -> str:
    return "redacted" if SENSITIVE.search(value) else value


def _write_json_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def record_result(
    project_dir: Path,
    decision: Decision,
    now: datetime,
    notifier: Callable[[str], None] | None,
    consecutive_review_failures: int = 0,
    heartbeat_finished_at: datetime | None = None,
) -> None:
    runtime_dir = Path(project_dir) / "data" / "watchdog"
    state_path = runtime_dir / "state.json"
    log_path = runtime_dir / "watchdog.log"
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous = {}

    state = decision.state
    reason = _safe(decision.reason)
    action = decision.action.value
    transition = previous.get("state") != state or previous.get("reason") != reason
    payload = {
        "schema_version": 1,
        "checked_at": now.isoformat(),
        "state": state,
        "reason": reason,
        "action": action,
        "consecutive_review_failures": consecutive_review_failures,
        "last_heartbeat_finished_at": (
            heartbeat_finished_at.isoformat()
            if heartbeat_finished_at is not None
            else None
        ),
    }
    if action != Action.NONE.value:
        payload["last_action"] = action
        payload["last_action_at"] = now.isoformat()
    else:
        payload["last_action"] = previous.get("last_action")
        payload["last_action_at"] = previous.get("last_action_at")
    _write_json_private(state_path, payload)

    runtime_dir.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as file:
        file.write(
            f"{now.isoformat()} state={state} reason={reason} action={action}\n"
        )
    os.chmod(log_path, 0o600)

    if transition and notifier is not None:
        notifier(f"SteamGifts watchdog: {state} ({reason})")


def _parse_datetime(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str) and value.isdigit():
        return _parse_datetime(int(value))
    text = str(value).replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+(?=[+-]\d\d:\d\d$)", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _empty_snapshot(**overrides) -> Snapshot:
    values = {
        "docker_available": False,
        "container_exists": False,
        "container_running": False,
        "container_health": "missing",
        "container_started_at": None,
        "panel_available": False,
        "task_count": 0,
        "task_id": None,
        "task_enabled": False,
        "task_schedule": "",
        "task_command": "",
        "last_task_run_at": None,
        "process_started_at": None,
        "heartbeat_status": None,
        "heartbeat_finished_at": None,
        "consecutive_review_failures": 0,
        "task_api_available": False,
    }
    values.update(overrides)
    return Snapshot(**values)


def _panel_probe(url: str, timeout: int) -> bool:
    if url != "http://127.0.0.1:5700/":
        raise ValueError("panel probe must remain localhost-only")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _read_heartbeat(project_dir: Path):
    path = Path(project_dir) / "data" / "watchdog" / "bot-heartbeat.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, None
    return payload.get("status"), _parse_datetime(payload.get("finished_at"))


def _review_failure_count(
    project_dir: Path,
    heartbeat_status: str | None,
    heartbeat_finished_at: datetime | None,
) -> int:
    if heartbeat_status != "site_error":
        return 0
    state_path = Path(project_dir) / "data" / "watchdog" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        state = {}
    last_seen = _parse_datetime(state.get("last_heartbeat_finished_at"))
    previous_count = int(state.get("consecutive_review_failures", 0) or 0)
    if heartbeat_finished_at is not None and heartbeat_finished_at != last_seen:
        return previous_count + 1
    return max(previous_count, 1)


def _task_last_run(task: dict[str, Any]) -> datetime | None:
    for key in (
        "lastExecutionTime",
        "last_execution_time",
        "lastRunAt",
    ):
        parsed = _parse_datetime(task.get(key))
        if parsed is not None:
            return parsed
    return None


def collect_snapshot(
    project_dir: Path,
    command_runner=subprocess.run,
    http_call=_http_json,
    panel_probe=_panel_probe,
    clock=lambda: datetime.now(timezone.utc),
):
    project_dir = Path(project_dir)
    now = clock()
    docker = command_runner(
        [DOCKER_BIN, "info", "--format", "{{json .ServerVersion}}"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if getattr(docker, "returncode", 1) != 0:
        return _empty_snapshot(), None

    inspect = command_runner(
        [DOCKER_BIN, "inspect", "steamgifts-qinglong", "--format", "{{json .State}}"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if getattr(inspect, "returncode", 1) != 0:
        return _empty_snapshot(docker_available=True), None
    try:
        container_state = json.loads(inspect.stdout)
    except (AttributeError, json.JSONDecodeError):
        return _empty_snapshot(docker_available=True, container_exists=True), None

    running = bool(container_state.get("Running"))
    started_at = _parse_datetime(container_state.get("StartedAt"))
    health = container_state.get("Health", {}).get("Status")
    if not health:
        health = "healthy" if running else "stopped"
    base = {
        "docker_available": True,
        "container_exists": True,
        "container_running": running,
        "container_health": health,
        "container_started_at": started_at,
    }
    if not running:
        return _empty_snapshot(**base), None

    panel_available = panel_probe("http://127.0.0.1:5700/", 10)
    heartbeat_status, heartbeat_finished_at = _read_heartbeat(project_dir)
    failures = _review_failure_count(
        project_dir, heartbeat_status, heartbeat_finished_at
    )
    base.update(
        {
            "panel_available": panel_available,
            "heartbeat_status": heartbeat_status,
            "heartbeat_finished_at": heartbeat_finished_at,
            "consecutive_review_failures": failures,
        }
    )
    if not panel_available:
        return _empty_snapshot(**base), None

    try:
        token = load_qinglong_token(project_dir, command_runner, now)
        client = QinglongClient("http://127.0.0.1:5700", token, http_call)
        tasks = [task for task in client.list_tasks() if task.get("name") == TASK_NAME]
    except Exception:
        return _empty_snapshot(**base, task_api_available=False), None

    task = tasks[0] if len(tasks) == 1 else {}
    task_id = task.get("id", task.get("_id"))
    task_enabled = not bool(task.get("isDisabled", task.get("is_disabled", False)))

    process = command_runner(
        [
            DOCKER_BIN,
            "exec",
            "steamgifts-qinglong",
            "sh",
            "-lc",
            "ps -eo etimes,args",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    elapsed = []
    if getattr(process, "returncode", 1) == 0:
        for line in process.stdout.splitlines():
            fields = line.strip().split(maxsplit=1)
            if len(fields) == 2 and fields[0].isdigit() and "python3 sg.py" in fields[1]:
                elapsed.append(int(fields[0]))
    process_started_at = now - timedelta(seconds=max(elapsed)) if elapsed else None

    snapshot = _empty_snapshot(
        **base,
        task_count=len(tasks),
        task_id=task_id,
        task_enabled=task_enabled,
        task_schedule=str(task.get("schedule", "")),
        task_command=str(task.get("command", "")),
        last_task_run_at=_task_last_run(task),
        process_started_at=process_started_at,
        task_api_available=True,
    )
    return snapshot, client


def _action_rate_limited(project_dir: Path, decision: Decision, now: datetime) -> bool:
    if decision.action == Action.NONE:
        return False
    path = Path(project_dir) / "data" / "watchdog" / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    last_at = _parse_datetime(state.get("last_action_at"))
    return (
        state.get("last_action") == decision.action.value
        and last_at is not None
        and now - last_at < timedelta(minutes=15)
    )


def _notify_macos(message: str) -> None:
    safe = message.replace('"', "'")
    subprocess.run(
        ["osascript", "-e", f'display notification "{safe}" with title "SteamGifts"'],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def run_once(
    project_dir: Path,
    check_only: bool = False,
    no_notify: bool = False,
    command_runner=subprocess.run,
    http_call=_http_json,
    panel_probe=_panel_probe,
    clock=lambda: datetime.now(timezone.utc),
    notifier=_notify_macos,
) -> Decision:
    now = clock()
    snapshot, client = collect_snapshot(
        project_dir,
        command_runner=command_runner,
        http_call=http_call,
        panel_probe=panel_probe,
        clock=lambda: now,
    )
    decision = decide(snapshot, now)
    effective = decision
    if check_only or _action_rate_limited(project_dir, decision, now):
        effective = Decision(decision.state, decision.reason, Action.NONE)
    else:
        perform_action(
            decision,
            snapshot,
            Path(project_dir),
            command_runner,
            client,
        )
    record_result(
        Path(project_dir),
        effective,
        now,
        None if no_notify else notifier,
        consecutive_review_failures=snapshot.consecutive_review_failures,
        heartbeat_finished_at=snapshot.heartbeat_finished_at,
    )
    return effective


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SteamGifts Qinglong watchdog")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args(argv)
    project_dir = args.project_dir.resolve()
    lock_path = project_dir / "data" / "watchdog" / "watchdog.lock"
    with watchdog_lock(lock_path) as acquired:
        if not acquired:
            return 0
        decision = run_once(
            project_dir,
            check_only=args.check_only,
            no_notify=args.no_notify,
        )
    print(
        f"state={decision.state} reason={_safe(decision.reason)} "
        f"action={decision.action.value}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
