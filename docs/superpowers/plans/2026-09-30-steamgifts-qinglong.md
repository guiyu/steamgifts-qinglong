# SteamGifts Qinglong Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the supplied SteamGifts bot in a dedicated, localhost-only Qinglong container that performs at most one giveaway scan per hour.

**Architecture:** Build a small image from Qinglong 2.20.2 with the bot's Python dependencies and `flock`, persist Qinglong under `./data`, and bind-mount the versioned bot source at `/ql/data/scripts/steam_gift`. Convert the supplied infinite-loop script into a one-scan process and let Qinglong's hourly cron own repetition.

**Tech Stack:** Docker Compose, `whyour/qinglong:2.20.2`, Alpine Linux, Python 3, `requests`, Beautiful Soup 4, `flock`, Python `unittest`

**Spec:** `docs/superpowers/specs/2026-09-30-steamgifts-qinglong-design.md`

## Global Constraints

- Keep the old `mini4-server/qinglong_data_backup` unchanged and never start it.
- Publish the panel only as `127.0.0.1:5700:5700`.
- Use `whyour/qinglong:2.20.2`; do not use `latest`.
- Set the container timezone to `Asia/Shanghai`.
- Store the supplied authentication values only in ignored `src/steam_gift/settings.cfg` with mode `0600`; never print or commit them.
- Run the bot once per invocation; Qinglong repeats it with `0 * * * *`.
- Guard the command with `flock -n /tmp/steamgifts.lock`.
- Keep external notification providers unconfigured.
- Do not declare completion until a real authenticated preflight and controlled task run pass.

## Review Focus

- Secret leakage: Task 1 tests the ignore rules, and Task 3 verifies the live credential file is untracked, mode `0600`, and absent from tracked files.
- Accidental LAN exposure: Task 1 tests the literal loopback port binding, and Task 3 inspects Docker's published-port address.
- Infinite or overlapping execution: Task 2 tests that no module-level infinite loop remains, and Task 4 verifies `flock` rejects a concurrent invocation.
- Expired session or site challenge: Task 3 requires an authenticated profile preflight and keeps the task disabled on redirect or challenge.
- Container replacement losing dependencies or tasks: Task 3 tests imports in the built image, and Task 4 restarts the container and verifies the persisted hourly task.

---

### Task 1: Reproducible localhost-only Qinglong project

**Files:**
- Create: `compose.yaml`
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `.gitignore`
- Create: `README.md`
- Create: `tests/test_deployment_contract.py`

**Interfaces:**
- Consumes: the exact image, port, mount, timezone, and secret-handling decisions in the spec
- Produces: a Compose service named `qinglong`, image `local/steamgifts-qinglong:2.20.2`, and host paths used by all later tasks

- [ ] **Step 1: Write the failing deployment-contract tests**

  Add `DeploymentContractTests` using `unittest` and `pathlib`. Test that:

  - `Dockerfile` starts from `whyour/qinglong:2.20.2` and installs `util-linux`, `requests`, and `beautifulsoup4`.
  - `compose.yaml` contains `127.0.0.1:5700:5700`, `restart: unless-stopped`, `TZ: Asia/Shanghai`, `./data:/ql/data`, and `./src/steam_gift:/ql/data/scripts/steam_gift`.
  - `compose.yaml` does not contain `/var/run/docker.sock` or `latest`.
  - `.gitignore` ignores `/data/`, `/src/steam_gift/settings.cfg`, `/src/steam_gift/won.txt`, `/src/steam_gift/bad_giveaways.txt`, and Python caches.
  - `.dockerignore` excludes `data`, the live credential file, `.git`, and Python caches.

- [ ] **Step 2: Run the test and verify it fails because the deployment files do not exist**

  Run: `python3 -m unittest tests.test_deployment_contract -v`

  Expected: FAIL with `FileNotFoundError` for `Dockerfile` or `compose.yaml`.

- [ ] **Step 3: Add the minimal container and project files**

  Implement the exact Compose service from the Interfaces block. The Dockerfile adds `util-linux` with `apk` and the two Python packages with `pip3`. README documents local access, build/start/stop/log commands, the hourly single-run model, credential-file location, and non-destructive rollback.

- [ ] **Step 4: Run the contract test and Compose parser**

  Run: `python3 -m unittest tests.test_deployment_contract -v`

  Expected: all tests PASS.

  Run: `docker compose config`

  Expected: exit 0; rendered port host IP is `127.0.0.1` and no Docker socket is mounted.

- [ ] **Step 5: Commit the project scaffold**

  ```bash
  git add Dockerfile compose.yaml .dockerignore .gitignore README.md tests/test_deployment_contract.py
  git commit -m "build: add isolated Qinglong container"
  ```

### Task 2: Versioned bot source with one scan per invocation

**Files:**
- Create: `src/steam_gift/sg.py`
- Create: `src/steam_gift/notify.py`
- Create: `src/steam_gift/notify.js`
- Create: `src/steam_gift/settings.cfg.example`
- Create: `src/steam_gift/search.txt`
- Create: `src/steam_gift/bad_giveaways_link.txt`
- Create: `src/steam_gift/black_list_games_name.txt`
- Create: `tests/test_bot_execution_model.py`

**Interfaces:**
- Consumes: the user-supplied ZIP and the Task 1 mount `/ql/data/scripts/steam_gift`
- Produces: canonical bot source whose module-level execution completes after one scan and a placeholder-only settings template

- [ ] **Step 1: Import the supplied static files and create the placeholder template**

  Copy `sg.py`, both notification files, and the three static list files from the supplied archive. Create `settings.cfg.example` with the supplied non-secret settings and placeholder values `YOUR_PHPSESSID` and `YOUR_USER_AGENT`. Do not import `won.txt` or create the live credential file yet.

- [ ] **Step 2: Write the failing execution-model tests**

  Add AST-based tests that assert:

  - `src/steam_gift/sg.py` has no module-level `while True` loop.
  - the pagination loops inside functions remain present.
  - parsing `settings.cfg.example` returns exactly `YOUR_PHPSESSID` and `YOUR_USER_AGENT` for its two authentication fields.
  - the source compiles with `python3 -m py_compile`.

- [ ] **Step 3: Run the tests and verify the imported script fails the one-run assertion**

  Run: `python3 -m unittest tests.test_bot_execution_model -v`

  Expected: FAIL because the supplied script contains a module-level infinite loop.

- [ ] **Step 4: Replace only the module-level infinite loop with one scan**

  Preserve the existing scan order, point threshold, notification call, parsing logic, and in-scan randomized delays. Execute the former loop body once, send its summary once, and exit without the old 30-to-60-minute sleep.

- [ ] **Step 5: Run focused and full tests**

  Run: `python3 -m unittest tests.test_bot_execution_model -v`

  Expected: all tests PASS.

  Run: `python3 -m unittest discover -s tests -v`

  Expected: all Task 1 and Task 2 tests PASS.

- [ ] **Step 6: Commit the bot source**

  ```bash
  git add src/steam_gift tests/test_bot_execution_model.py
  git commit -m "feat: run SteamGifts scan once per task"
  ```

### Task 3: Build, start, initialize, and authenticate safely

**Files:**
- Create, ignored: `src/steam_gift/settings.cfg`
- Create, ignored: `src/steam_gift/won.txt`
- Create at runtime, ignored: `data/`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1 Compose service, Task 2 settings template, and the authentication values already supplied by the user
- Produces: a running initialized Qinglong panel and a verified SteamGifts session, with no enabled bot task yet

- [ ] **Step 1: Build the image and start the service**

  Run: `docker compose build --pull`

  Expected: exit 0 and image `local/steamgifts-qinglong:2.20.2` exists.

  Run: `docker compose up -d`

  Expected: service `qinglong` is running.

- [ ] **Step 2: Verify network and runtime dependencies**

  Check `docker compose ps`, `docker inspect`, and `lsof` to prove the only published address is `127.0.0.1:5700`. Check `http://127.0.0.1:5700/` with GET. Inside the container, import `requests` and `bs4`, and resolve `flock` on `PATH`.

  Expected: every check succeeds; no `0.0.0.0:5700` or `[::]:5700` listener exists.

- [ ] **Step 3: Pause for local Qinglong administrator initialization**

  Open `http://127.0.0.1:5700/` for the user. The user creates the panel administrator credentials locally and confirms they are signed in. Do not request or record the password.

- [ ] **Step 4: Install the live SteamGifts configuration without disclosure**

  Create `src/steam_gift/settings.cfg` from the example using the authentication values already supplied by the user, without printing the resulting file or values. Create `won.txt` containing `0`. Set `settings.cfg` to mode `0600`.

- [ ] **Step 5: Verify secret hygiene**

  Run checks that assert the live settings file is ignored by Git, has mode `0600`, and that its credential values do not occur in any path returned by `git ls-files`. Report only booleans and filenames, never the values.

  Expected: ignored `true`, mode `0600`, tracked-secret matches `0`.

- [ ] **Step 6: Perform a non-mutating authenticated preflight**

  From inside the container, read the live config and issue only `GET https://www.steamgifts.com/account/settings/profile`. Print status, final host/path, challenge detection, and an authenticated-page boolean; do not print cookies, headers, username, or page content.

  Expected: HTTP 200, final path remains `/account/settings/profile`, no challenge, authenticated-page boolean `true`. Stop here if any condition fails.

- [ ] **Step 7: Record setup commands and commit documentation only**

  Add the verified build, health, and safe credential-update procedures to README without values.

  ```bash
  git add README.md
  git commit -m "docs: add Qinglong setup and credential checks"
  ```

### Task 4: Create the hourly task and verify real operation

**Files:**
- Create: `docs/verification.md`

**Interfaces:**
- Consumes: the authenticated panel and bot runtime from Task 3
- Produces: enabled Qinglong task `SteamGifts auto-entry` with schedule `0 * * * *` and a redacted verification record

- [ ] **Step 1: Verify the lock rejects overlap without contacting SteamGifts**

  Hold `/tmp/steamgifts.lock` in the container with a harmless sleep, then invoke `flock -n /tmp/steamgifts.lock true` in a second process.

  Expected: the second command exits nonzero immediately.

- [ ] **Step 2: Create the disabled Qinglong task through the supported UI or API**

  Create exactly:

  - Name: `SteamGifts auto-entry`
  - Schedule: `0 * * * *`
  - Command: `cd /ql/data/scripts/steam_gift && flock -n /tmp/steamgifts.lock python3 sg.py`

  Keep the task disabled until the controlled run passes. Do not edit Qinglong's SQLite database directly.

- [ ] **Step 3: Run one controlled task and inspect its complete log**

  Start the task manually. This authorized functional check may enter eligible giveaways and consume points. Wait for completion while checking that at most one `sg.py` process exists.

  Expected: the log confirms a valid cookie, reports points or an empty eligible set, performs no uncaught traceback, sends its console summary once, and the process exits.

- [ ] **Step 4: Enable and verify the hourly schedule**

  Enable the task and re-open its details.

  Expected: enabled state, schedule exactly `0 * * * *`, command exactly as specified, and next-run time aligned to the next local hour.

- [ ] **Step 5: Restart the dedicated container and verify persistence**

  Run: `docker compose restart qinglong`

  Expected: panel returns successfully, administrator initialization remains intact, the task remains enabled with its exact schedule and command, dependencies still import, and there is no duplicate `sg.py` process.

- [ ] **Step 6: Write the redacted verification record**

  Record the image ID/digest, container status, loopback binding, dependency checks, session-preflight result, task name/schedule, controlled-run outcome, restart result, and verification timestamp. Exclude credentials, cookies, raw response bodies, username, and giveaway URLs.

- [ ] **Step 7: Run final verification and commit**

  Run: `python3 -m unittest discover -s tests -v`

  Expected: all tests PASS.

  Run: `docker compose config --quiet`

  Expected: exit 0.

  Run: `git status --short`

  Expected: only ignored runtime state is absent from output; no uncommitted tracked changes remain after the commit.

  ```bash
  git add docs/verification.md
  git commit -m "docs: record verified SteamGifts deployment"
  ```
