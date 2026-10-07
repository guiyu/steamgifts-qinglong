from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


TERMINAL_STATUSES = frozenset(
    {
        "success",
        "no_eligible_giveaways",
        "authentication_blocked",
        "site_error",
        "internal_error",
    }
)
SENSITIVE = re.compile(
    r"(?:cookie|token|php[a-z0-9_]*|clearance|username|email|https?://|[a-z0-9._%+-]+@[a-z0-9.-]+\.)",
    re.IGNORECASE,
)


def _is_iso_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    return parsed.tzinfo is not None


@dataclass(frozen=True)
class Heartbeat:
    schema_version: int
    started_at: str
    finished_at: str
    status: str
    reason: str
    eligible_count: int
    selected_count: int
    entered_count: int
    points_before: int
    points_after: int
    process_id: int

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported heartbeat schema")
        if self.status not in TERMINAL_STATUSES:
            raise ValueError("invalid terminal status")
        if not _is_iso_timestamp(self.started_at) or not _is_iso_timestamp(
            self.finished_at
        ):
            raise ValueError("timestamps must be timezone-aware ISO-8601 values")
        counters = (
            self.eligible_count,
            self.selected_count,
            self.entered_count,
            self.points_before,
            self.points_after,
            self.process_id,
        )
        if any(type(value) is not int or value < 0 for value in counters):
            raise ValueError("heartbeat counters must be non-negative integers")


def write_heartbeat(path: Path, heartbeat: Heartbeat) -> None:
    path = Path(path)
    payload = asdict(heartbeat)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if SENSITIVE.search(serialized):
        raise ValueError("heartbeat contains sensitive data")

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(serialized)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
