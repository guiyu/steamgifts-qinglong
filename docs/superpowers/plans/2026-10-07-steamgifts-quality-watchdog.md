# SteamGifts Quality Filter and Qinglong Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enter only reliably well-reviewed Steam games, spend the available SteamGifts point balance as fully as qualified candidates allow, and keep the local Qinglong deployment operational with a macOS watchdog.

**Architecture:** Split review lookup and point selection into focused Python modules, refactor the bot into a one-run `main()` that emits a structured heartbeat, and add a host-side watchdog driven by `launchd`. The watchdog reads only local state, controls Qinglong through its localhost `/open/crons` API, and reserves automatic retries for failures that a restart or task repair can actually fix.

**Tech Stack:** Python 3.11, `curl_cffi`, Beautiful Soup 4, Python `unittest`, Docker Compose, Qinglong 2.20.2 local API, macOS `launchd`, `osascript`

**Spec:** `docs/superpowers/specs/2026-10-07-steamgifts-quality-watchdog-design.md`

## Global Constraints

- Keep the panel bound only to `127.0.0.1:5700`.
- Preserve task name `SteamGifts auto-entry`, schedule `0 * * * *`, timezone `Asia/Shanghai`, and the existing `flock` command.
- Accept only games with at least 100 Steam reviews and at least 80% positive reviews.
- Skip missing, malformed, or unavailable Steam review data.
- Never enter an unqualified giveaway merely to spend points.
- Keep `settings.cfg`, Qinglong tokens, heartbeat state, watchdog state, and watchdog logs out of Git and user-visible logs.
- Do not mount the Docker socket into a container.
- Do not solve Cloudflare challenges or CAPTCHAs automatically.
- Notify only on watchdog state transitions; never include credentials, raw page bodies, or giveaway URLs.
- Implement all behavior changes test-first and keep each task independently committable.

## Review Focus

- A Steam Store URL with query parameters or an unsupported `/sub/` URL must produce the correct App ID or a safe skip; covered in Task 2 parsing tests.
- Two candidate sets with equal point spend must resolve deterministically using review quality and code ordering; covered in Task 2 tie-break tests.
- Steam review HTTP 200 responses with `success != 1`, missing fields, strings, or zero reviews must be skipped; covered in Task 3 response-validation tests.
- A stale heartbeat caused by `authentication_blocked` must never trigger repeated task runs; covered in Task 5 recovery-policy tests.
- A launchd environment with a minimal `PATH`, spaces in the project path, or an expired Qinglong token must fail safely or refresh through Qinglong's local API path without leaking values; covered in Task 5 runtime-adapter and Task 6 installer tests.

---

## File Structure

- `src/steam_gift/selection.py` — immutable candidate/review types, eligibility rules, and deterministic 0/1 point allocation.
- `src/steam_gift/reviews.py` — Steam App ID extraction, public review-summary retrieval, validation, and per-run cache.
- `src/steam_gift/heartbeat.py` — heartbeat schema and atomic JSON writer.
- `src/steam_gift/sg.py` — one-run orchestration, candidate collection, selected-entry execution, and heartbeat lifecycle.
- `scripts/watchdog.py` — host health collection, pure recovery decision, localhost Qinglong API actions, state transitions, notifications, and CLI.
- `deploy/macos/com.guiyu.steamgifts-qinglong-watchdog.plist.template` — credential-free LaunchAgent template.
- `scripts/install-watchdog.sh` — resolve absolute runtime paths, render/load the LaunchAgent, and run the first check.
- `tests/test_selection.py` — pure selection and tie-break behavior.
- `tests/test_reviews.py` — App ID parsing, response validation, caching, and failure behavior.
- `tests/test_heartbeat.py` — schema and atomic/redacted heartbeat output.
- `tests/test_watchdog.py` — snapshot-to-action policy, rate limiting, and redaction.
- Existing deployment/runtime tests — dependency and ignored-runtime contracts.

### Task 1: Finalize Browser-Compatible SteamGifts Transport

**Files:**
- Modify: `Dockerfile`
- Modify: `src/steam_gift/sg.py:1-14`
- Modify: `tests/test_bot_execution_model.py`
- Modify: `tests/test_container_runtime.py`

**Interfaces:**
- Produces: module-level HTTP client supporting existing `.get()` and `.post()` calls through `curl_cffi.requests.Session(impersonate="chrome")`.

- [ ] **Step 1: Preserve the observed RED evidence**

Confirm the existing test `test_uses_browser_impersonation_client` rejects importing plain `requests`, and the runtime test imports `curl_cffi`. These tests were already observed failing before the implementation edits in the current working tree.

- [ ] **Step 2: Verify the minimal implementation**

Run: `python3 -m unittest discover -s tests -p 'test_bot_execution_model.py' -v`

Expected: all bot execution tests pass.

- [ ] **Step 3: Rebuild the image and verify the runtime dependency**

Run: `docker compose build && python3 -m unittest discover -s tests -p 'test_container_runtime.py' -v`

Expected: the image builds and the runtime dependency test passes.

- [ ] **Step 4: Run a read-only authenticated preflight**

Use the local ignored settings with `curl_cffi`, request the profile page, and print only status/challenge/authentication booleans.

Expected: HTTP 200, no challenge marker, authenticated marker present.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile src/steam_gift/sg.py tests/test_bot_execution_model.py tests/test_container_runtime.py
git commit -m "fix: impersonate browser for SteamGifts"
```

### Task 2: Add Deterministic Quality and Point Selection

**Files:**
- Create: `src/steam_gift/selection.py`
- Create: `tests/test_selection.py`

**Interfaces:**
- Produces: `ReviewSummary(total_positive: int, total_reviews: int)`, `Candidate(code: str, url: str, points: int, app_id: int, review: ReviewSummary)`, `positive_percent(summary: ReviewSummary) -> int`, `is_qualified(summary: ReviewSummary, min_percent: int, min_reviews: int) -> bool`, and `select_candidates(candidates: Sequence[Candidate], budget: int) -> list[Candidate]`.

- [ ] **Step 1: Write failing threshold and malformed-input tests**

Add tests that accept 80/100 exactly; reject 79/100, 80/99, zero reviews, negative totals, positive totals above total reviews, and non-positive point costs.

- [ ] **Step 2: Run tests to verify RED**

Run: `python3 -m unittest tests/test_selection.py -v`

Expected: import failure because `selection.py` does not exist.

- [ ] **Step 3: Implement types and qualification functions**

Use frozen dataclasses. Invalid summaries return `False` from `is_qualified`; invalid candidates raise `ValueError` during construction.

- [ ] **Step 4: Verify threshold tests pass**

Run: `python3 -m unittest tests/test_selection.py -v`

Expected: threshold tests pass.

- [ ] **Step 5: Write failing allocation and tie-break tests**

Cover exact spend, best under-budget spend, duplicate giveaway codes, candidates above budget, equal-spend preference for higher minimum positive percentage, then higher total review count, then stable code order.

- [ ] **Step 6: Run tests to verify RED**

Expected: allocation tests fail because `select_candidates` is absent or incomplete.

- [ ] **Step 7: Implement deterministic 0/1 allocation**

Deduplicate by giveaway code before dynamic programming. Return candidates sorted by descending points and then code, without mutating the input sequence.

- [ ] **Step 8: Run tests and commit**

Run: `python3 -m unittest tests/test_selection.py -v`

Expected: all selection tests pass.

```bash
git add src/steam_gift/selection.py tests/test_selection.py
git commit -m "feat: optimize qualified giveaway selection"
```

### Task 3: Add Steam Review Lookup and Candidate Discovery

**Files:**
- Create: `src/steam_gift/reviews.py`
- Create: `tests/test_reviews.py`
- Modify: `src/steam_gift/sg.py:42-114,269-345`
- Modify: `src/steam_gift/settings.cfg.example`
- Modify: `tests/test_bot_execution_model.py`

**Interfaces:**
- Consumes: `ReviewSummary`, `Candidate`, `is_qualified`, and `select_candidates` from Task 2.
- Produces: `extract_app_id(href: str) -> int | None`, `parse_review_summary(payload: object) -> ReviewSummary | None`, and `SteamReviewCatalog(session, timeout=30).get(app_id: int) -> ReviewSummary | None` with one lookup per App ID per run.

- [ ] **Step 1: Write failing URL, payload, and cache tests**

Cover `/app/123`, `/app/123?utm_source=SteamGifts`, malformed URLs, `/sub/123`, successful summaries, `success != 1`, missing/nonnumeric fields, zero reviews, request exceptions, and repeated App IDs.

- [ ] **Step 2: Run tests to verify RED**

Run: `python3 -m unittest tests/test_reviews.py -v`

Expected: import failure because `reviews.py` does not exist.

- [ ] **Step 3: Implement review extraction and cached lookup**

Call `https://store.steampowered.com/appreviews/{app_id}` with literal parameters `json=1`, `language=all`, `purchase_type=all`, and `filter=summary`. Return `None` on any unavailable or invalid result.

- [ ] **Step 4: Verify review tests pass**

Run: `python3 -m unittest tests/test_reviews.py -v`

Expected: all review tests pass.

- [ ] **Step 5: Write failing bot discovery tests**

Extend the HTML fixture so one listing row contains a giveaway code, `(3P)`, and a Steam `/app/` link. Assert the bot collects the candidate, skips a low-review candidate, sends no `entry_insert` before selection, and passes `min_positive_percent=80` and `min_review_count=100` from settings.

- [ ] **Step 6: Run bot tests to verify RED**

Expected: tests fail because the current `get_game_links` enters immediately and does not query reviews.

- [ ] **Step 7: Refactor discovery and entry orchestration**

Make listing traversal collect deduplicated candidate metadata before entry. After all configured sources are scanned, qualify candidates, call `select_candidates`, enter the selected candidates, and recompute selection from remaining qualified candidates when an entry fails without invalidating authentication.

- [ ] **Step 8: Add exact settings defaults**

Add `min_positive_percent=80` and `min_review_count=100` to `settings.cfg.example`; update the local ignored `settings.cfg` without displaying its cookie values.

- [ ] **Step 9: Run focused and full tests, then commit**

Run: `python3 -m unittest tests/test_reviews.py tests/test_selection.py tests/test_bot_execution_model.py -v`

Expected: all focused tests pass.

```bash
git add src/steam_gift/reviews.py src/steam_gift/sg.py src/steam_gift/settings.cfg.example tests/test_reviews.py tests/test_bot_execution_model.py
git commit -m "feat: enter only well-reviewed Steam games"
```

### Task 4: Add Structured Bot Heartbeats

**Files:**
- Create: `src/steam_gift/heartbeat.py`
- Create: `tests/test_heartbeat.py`
- Modify: `src/steam_gift/sg.py`
- Modify: `.gitignore`
- Modify: `tests/test_deployment_contract.py`

**Interfaces:**
- Produces: `Heartbeat` dataclass with the spec fields and `write_heartbeat(path: Path, heartbeat: Heartbeat) -> None`; `sg.main() -> int` writes one terminal heartbeat and returns the process exit code.

- [ ] **Step 1: Write failing heartbeat tests**

Assert atomic replacement, schema version, allowed statuses, ISO-8601 timestamps, integer counters, mode `0600`, and absence of keys or serialized values containing `cookie`, `token`, `php`, `clearance`, giveaway URLs, username, or email.

- [ ] **Step 2: Run tests to verify RED**

Run: `python3 -m unittest tests/test_heartbeat.py -v`

Expected: import failure because `heartbeat.py` does not exist.

- [ ] **Step 3: Implement heartbeat writer**

Write to a temporary file in the destination directory, `fsync`, chmod `0600`, and `os.replace` it into place.

- [ ] **Step 4: Verify heartbeat unit tests pass**

Run: `python3 -m unittest tests/test_heartbeat.py -v`

Expected: all heartbeat tests pass.

- [ ] **Step 5: Write failing bot status tests**

Run the bot with an injected heartbeat path and fixtures for success, no eligible candidates, Cloudflare/authentication failure, review-site failure, and unexpected exception. Assert the terminal status and that unexpected exceptions still return non-zero.

- [ ] **Step 6: Refactor the script into `main()` and integrate terminal statuses**

Keep import-time behavior side-effect free. Default the heartbeat path to `/ql/data/watchdog/bot-heartbeat.json`, allow `STEAMGIFTS_HEARTBEAT_PATH` for tests, and call `raise SystemExit(main())` under `if __name__ == "__main__"`.

- [ ] **Step 7: Ignore runtime state and run tests**

Add `/data/watchdog/` to `.gitignore` and its representative files to the deployment-contract ignored-path test.

Run: `python3 -m unittest discover -s tests -v`

Expected: complete suite passes.

- [ ] **Step 8: Commit**

```bash
git add .gitignore src/steam_gift/heartbeat.py src/steam_gift/sg.py tests/test_heartbeat.py tests/test_bot_execution_model.py tests/test_deployment_contract.py
git commit -m "feat: record SteamGifts run heartbeat"
```

### Task 5: Implement the Host Watchdog Policy

**Files:**
- Create: `scripts/watchdog.py`
- Create: `tests/test_watchdog.py`

**Interfaces:**
- Consumes: `data/watchdog/bot-heartbeat.json`, `data/config/token.json`, Docker Compose, localhost panel, and Qinglong `/open/crons` endpoints.
- Produces: `Snapshot`, `Decision`, `decide(snapshot: Snapshot, now: datetime) -> Decision`, and CLI `watchdog.py --project-dir PATH [--check-only] [--no-notify]`.

- [ ] **Step 1: Write failing decision-policy tests**

Cover Docker unavailable; container missing/stopped; startup grace; unhealthy container; panel unavailable; task missing/disabled/drifted/stale; process older than 50 minutes; recent success; `authentication_blocked`; repeated Steam review outage; and recovery from degraded to healthy.

- [ ] **Step 2: Run tests to verify RED**

Run: `python3 -m unittest tests/test_watchdog.py -v`

Expected: import failure because `scripts/watchdog.py` does not exist.

- [ ] **Step 3: Implement pure state and decision types**

Use one action enum: `NONE`, `START_CONTAINER`, `RESTART_CONTAINER`, `CREATE_TASK`, `ENABLE_TASK`, `UPDATE_TASK`, `RUN_TASK`, or `STOP_TASK`. Authentication blocks always decide `NONE` with degraded state.

- [ ] **Step 4: Verify policy tests pass**

Run the watchdog tests; expect all pure decision cases to pass.

- [ ] **Step 5: Write failing runtime-adapter tests**

Inject command, HTTP, clock, and notification callables. Assert exact Docker arguments, localhost-only URLs, Bearer header construction without logging the token, token-expiration handling, one action per pass, five-minute overlap lock, transition-only notification, and redacted state/log output.

- [ ] **Step 6: Implement collection and bounded actions**

Read the Qinglong task through `/open/crons`, and use `/open/crons`, `/enable`, `/run`, and `/stop` for task repair. Resolve task identity by exact name and reject duplicates instead of mutating multiple rows. If `data/config/token.json` is expired, first run Qinglong's existing `/ql/shell/api.sh` inside the container to refresh the file, then reread it; never print the token or place it in watchdog state.

- [ ] **Step 7: Implement state, logging, locking, and notifications**

Store state at `data/watchdog/state.json`, log to `data/watchdog/watchdog.log`, lock at `data/watchdog/watchdog.lock`, and call `osascript` only on a state transition unless `--no-notify` is set.

- [ ] **Step 8: Run tests and commit**

Run: `python3 -m unittest tests/test_watchdog.py -v`

Expected: all watchdog tests pass without Docker or network access.

```bash
git add scripts/watchdog.py tests/test_watchdog.py
git commit -m "feat: add Qinglong host watchdog"
```

### Task 6: Install the macOS LaunchAgent and Document Operations

**Files:**
- Create: `deploy/macos/com.guiyu.steamgifts-qinglong-watchdog.plist.template`
- Create: `scripts/install-watchdog.sh`
- Create: `tests/test_watchdog_installer.py`
- Modify: `README.md`
- Modify: `docs/verification.md`
- Modify: `tests/test_deployment_contract.py`

**Interfaces:**
- Consumes: watchdog CLI from Task 5.
- Produces: LaunchAgent label `com.guiyu.steamgifts-qinglong-watchdog`, 300-second interval, run-at-load behavior, and an installer that renders absolute Python, Docker, project, and log paths.

- [ ] **Step 1: Write failing template and installer tests**

Assert the label, `StartInterval=300`, `RunAtLoad=true`, absolute program arguments, safe quoting for spaces, credential absence, check-only first run, and a configurable test destination that never touches the real LaunchAgents directory.

- [ ] **Step 2: Run tests to verify RED**

Run: `python3 -m unittest tests/test_watchdog_installer.py -v`

Expected: failure because the template and installer do not exist.

- [ ] **Step 3: Implement template and installer**

Resolve `/usr/bin/python3`, the current Docker binary, and the project directory at install time. Render to `~/Library/LaunchAgents/com.guiyu.steamgifts-qinglong-watchdog.plist`, validate with `plutil -lint`, bootstrap with `launchctl bootstrap gui/$UID`, and kickstart the label.

- [ ] **Step 4: Document quality rules, recovery matrix, and maintenance**

Update README with the 80%/100-review rule, point optimization, credential-refresh behavior, watchdog installation/status/log paths, and safe manual checks. Record final evidence in `docs/verification.md` without usernames, URLs, tokens, or cookies.

- [ ] **Step 5: Run contract and full tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: complete suite passes.

- [ ] **Step 6: Commit**

```bash
git add deploy/macos/com.guiyu.steamgifts-qinglong-watchdog.plist.template scripts/install-watchdog.sh tests/test_watchdog_installer.py README.md docs/verification.md tests/test_deployment_contract.py
git commit -m "docs: install and operate Qinglong watchdog"
```

### Task 7: Deploy, Prove, and Publish

**Files:**
- Modify: local ignored `src/steam_gift/settings.cfg`
- Create/modify: ignored `data/watchdog/*`
- Modify: `docs/verification.md` only if runtime facts differ from Task 6's prepared record

**Interfaces:**
- Consumes: all previous tasks.
- Produces: healthy local deployment, loaded LaunchAgent, one verified controlled bot run, clean Git state, and matching local/remote `main` hashes.

- [ ] **Step 1: Run final static and unit verification**

Run:

```bash
git diff --check
python3 -m unittest discover -s tests -v
```

Expected: no diff errors and all tests pass.

- [ ] **Step 2: Rebuild and recreate Qinglong**

Run: `docker compose build && docker compose up -d --force-recreate`

Expected: container reaches healthy state and the panel returns HTTP 200.

- [ ] **Step 3: Verify authentication and task contract without entry**

Run a redacted profile preflight through `curl_cffi`; query `/open/crons` and assert exactly one enabled hourly task with the exact command.

- [ ] **Step 4: Install and exercise the watchdog**

Run the installer, verify `launchctl print gui/$UID/com.guiyu.steamgifts-qinglong-watchdog`, execute one `--check-only --no-notify` pass, and confirm a healthy state file without credentials.

- [ ] **Step 5: Run one controlled real bot task**

Use the exact `flock` command. Follow the process through exit, verify the heartbeat terminal status, confirm every entered candidate met 80%/100 reviews, and confirm the task left no residual `sg.py` process.

- [ ] **Step 6: Verify automatic continuity**

Confirm the hourly task remains enabled after container recreation and the LaunchAgent remains loaded. Do not deliberately invalidate credentials or delete persistent data; use fixtures/check-only mode for destructive recovery branches.

- [ ] **Step 7: Verify secret exclusion**

Check Git-tracked paths and staged diffs for `settings.cfg`, `data/`, token files, heartbeat files, watchdog logs, and known secret field values without printing those values.

- [ ] **Step 8: Commit final verification changes**

```bash
git add docs/verification.md
git commit -m "docs: verify quality filter and watchdog"
```

Skip this commit if the file is unchanged.

- [ ] **Step 9: Run completion verification**

Run the complete test suite again, `git status --short --branch`, `git log --oneline --decorate -10`, and compare `git rev-parse HEAD` with the to-be-pushed commit.

- [ ] **Step 10: Push and verify GitHub**

Run: `git push origin main`, fetch `origin/main`, and compare local and remote hashes.

Expected: the push succeeds, `main` tracks `origin/main`, hashes match, and the working tree is clean except ignored runtime state.
