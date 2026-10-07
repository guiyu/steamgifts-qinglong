#!/bin/bash
set -euo pipefail

LABEL="com.guiyu.steamgifts-qinglong-watchdog"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
PROJECT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"
PYTHON_BIN="${WATCHDOG_PYTHON_BIN:-/usr/bin/python3}"
DOCKER_BIN="${WATCHDOG_DOCKER_BIN:-$(command -v docker)}"
TEMPLATE="$PROJECT_DIR/deploy/macos/$LABEL.plist.template"
WATCHDOG_SCRIPT="$PROJECT_DIR/scripts/watchdog.py"
RUNTIME_DIR="$PROJECT_DIR/data/watchdog"
DESTINATION_DIR="${WATCHDOG_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
DESTINATION="$DESTINATION_DIR/$LABEL.plist"

case "$PYTHON_BIN" in
  /*) ;;
  *) echo "Python path must be absolute" >&2; exit 1 ;;
esac
case "$DOCKER_BIN" in
  /*) ;;
  *) echo "Docker path must be absolute" >&2; exit 1 ;;
esac

mkdir -p "$RUNTIME_DIR" "$DESTINATION_DIR"
chmod 700 "$RUNTIME_DIR"

STEAMGIFTS_DOCKER_BIN="$DOCKER_BIN" \
  "$PYTHON_BIN" "$WATCHDOG_SCRIPT" \
  --project-dir "$PROJECT_DIR" --check-only --no-notify

"$PYTHON_BIN" -c '
from pathlib import Path
from sys import argv
from xml.sax.saxutils import escape

template, destination, python_bin, docker_bin, project_dir, watchdog_script, stdout_log, stderr_log = argv[1:]
values = {
    "__PYTHON_BIN__": python_bin,
    "__DOCKER_BIN__": docker_bin,
    "__DOCKER_DIR__": str(Path(docker_bin).parent),
    "__PROJECT_DIR__": project_dir,
    "__WATCHDOG_SCRIPT__": watchdog_script,
    "__STDOUT_LOG__": stdout_log,
    "__STDERR_LOG__": stderr_log,
}
rendered = Path(template).read_text(encoding="utf-8")
for key, value in values.items():
    rendered = rendered.replace(key, escape(value, {"\"": "&quot;", "'"'"'": "&apos;"}))
Path(destination).write_text(rendered, encoding="utf-8")
' "$TEMPLATE" "$DESTINATION" "$PYTHON_BIN" "$DOCKER_BIN" "$PROJECT_DIR" \
  "$WATCHDOG_SCRIPT" "$RUNTIME_DIR/launchd.stdout.log" \
  "$RUNTIME_DIR/launchd.stderr.log"
chmod 600 "$DESTINATION"

if [[ "${WATCHDOG_INSTALL_DRY_RUN:-0}" == "1" ]]; then
  echo "Rendered watchdog LaunchAgent: $DESTINATION"
  exit 0
fi

plutil -lint "$DESTINATION"
launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$DESTINATION"
launchctl kickstart -k "gui/$UID/$LABEL"
echo "Installed watchdog LaunchAgent: $DESTINATION"
