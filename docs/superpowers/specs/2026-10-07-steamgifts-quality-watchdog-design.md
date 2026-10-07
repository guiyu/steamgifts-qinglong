# SteamGifts Quality Filter and Qinglong Watchdog Design

## Goal

Extend the existing local Qinglong deployment so the SteamGifts bot:

1. enters only games with reliable positive Steam reviews;
2. chooses eligible giveaways that spend as much of the current point balance as possible; and
3. remains operational through a host-level watchdog that detects and repairs container or Qinglong task failures.

The deployment remains local-only, preserves the existing hourly Qinglong schedule, and never stores credentials in Git or logs.

## Scope

### Included

- Replace plain `requests` traffic with a browser-fingerprint-compatible `curl_cffi` session so current Cloudflare clearance cookies remain usable.
- Filter candidates using Steam review totals.
- Optimize point allocation across qualified candidates.
- Record a secret-free structured heartbeat after each bot run.
- Install a macOS `launchd` watchdog that checks the container, Qinglong panel, task configuration, task freshness, and stuck processes every five minutes.
- Automatically recover safe local failures and notify on state transitions.
- Add automated tests, operator documentation, and reproducible installation commands.

### Excluded

- Automatically solving Cloudflare challenges or CAPTCHAs.
- Automatically obtaining or refreshing SteamGifts credentials without a browser session.
- Entering games without reliable Steam review data.
- Publishing the Qinglong panel outside `127.0.0.1`.
- Mounting the Docker socket into any container.
- External notification services.

## Existing Runtime Contract

- Project: `/Users/weiyi/workspaces/steamgifts-qinglong`
- Container: `steamgifts-qinglong`
- Panel: `http://127.0.0.1:5700`
- Task: `SteamGifts auto-entry`
- Schedule: `0 * * * *` in `Asia/Shanghai`
- Command:

  ```sh
  flock -n /tmp/steamgifts.lock bash -lc 'cd /ql/data/scripts/steam_gift && python3 sg.py'
  ```

- Credentials remain in Git-ignored `src/steam_gift/settings.cfg` with mode `0600`.

The new design preserves these names and paths so the persisted Qinglong task remains compatible.

## Architecture

The implementation has three components:

1. **Bot runner (`sg.py`)** — authenticates, discovers candidate giveaways, delegates Steam review qualification and point allocation, enters the selected giveaways, and writes a heartbeat.
2. **Selection module (`selection.py`)** — contains pure parsing, review qualification, and point-allocation logic that can be tested without network access.
3. **Host watchdog (`scripts/watchdog.py`)** — runs outside the container through `launchd`, checks the deployment and Qinglong task through local interfaces, applies bounded recovery actions, and reports state transitions.

The watchdog remains outside the Qinglong container because a task inside that container cannot detect or repair a stopped container.

## Browser-Compatible HTTP Client

The bot uses one `curl_cffi.requests.Session` configured with Chrome impersonation. The existing browser-derived `PHPSESSID`, `cf_clearance`, and matching User-Agent are passed to the session without being logged.

Authentication preflight continues to request the SteamGifts profile settings page and rejects:

- HTTP 403 or 429;
- Cloudflare challenge markers;
- redirects away from the authenticated profile; or
- a response without authenticated account markers.

An authentication or Cloudflare failure stops the run before any giveaway entry.

## Review Qualification

### Data source

For each unique giveaway, the bot extracts a Steam App ID from the giveaway detail page or its Steam Store link. It queries the public Steam Store review summary for that App ID. No Steam Web API key is required.

The response must provide numeric `total_positive` and `total_reviews` values. The positive percentage is calculated as:

```text
floor(total_positive * 100 / total_reviews)
```

### Eligibility rules

A giveaway is eligible only when all conditions are true:

- a Steam App ID is available;
- the review request succeeds and returns valid totals;
- `total_reviews >= 100`; and
- positive percentage is at least `80`.

Missing, malformed, rate-limited, or unavailable review data causes the giveaway to be skipped. The bot never treats missing review data as approval.

The thresholds are stored under `[settings]` as:

```ini
min_positive_percent=80
min_review_count=100
```

Review results are cached by App ID for the duration of one run so duplicate giveaways generate one Steam review request.

## Point Allocation

After discovery and review filtering, the bot deduplicates candidates by giveaway code and records each candidate's point cost, review percentage, and review count.

A 0/1 knapsack calculation selects the candidate set whose total cost is as close as possible to the current SteamGifts point balance without exceeding it. Tie-breaking is deterministic:

1. higher total points spent;
2. higher minimum review percentage within the set;
3. higher total review count; and
4. stable giveaway-code ordering.

The selected candidates are entered in descending point-cost order. When an entry fails without invalidating authentication, the bot removes that candidate, refreshes the current point balance, and recalculates from the remaining qualified candidates. It never substitutes an unqualified candidate merely to consume points.

## Heartbeat

Every run atomically writes `data/watchdog/bot-heartbeat.json` through the mounted project data directory. The heartbeat contains no cookie, token, username, email address, response body, or giveaway URL.

Fields:

- `schema_version`
- `started_at`
- `finished_at`
- `status`
- `reason`
- `eligible_count`
- `selected_count`
- `entered_count`
- `points_before`
- `points_after`
- `process_id`

Allowed terminal statuses:

- `success`
- `no_eligible_giveaways`
- `authentication_blocked`
- `site_error`
- `internal_error`

The heartbeat is written on normal completion and known failures. Unexpected exceptions are caught at the top level, recorded as `internal_error`, and then re-raised so Qinglong retains a non-zero task result.

## Watchdog

### Scheduling and isolation

A LaunchAgent runs `scripts/watchdog.py` every 300 seconds. A non-blocking host file lock prevents overlapping watchdog instances. Runtime state and logs live under Git-ignored `data/watchdog/`.

The LaunchAgent receives the absolute project directory as an argument. It does not contain credentials, Qinglong tokens, or user-specific browser data.

### Checks

Each watchdog pass checks:

1. Docker is reachable.
2. The `steamgifts-qinglong` container exists and is running.
3. The container health status is healthy or still within its startup grace period.
4. `GET http://127.0.0.1:5700/` returns HTTP 200.
5. The Qinglong token file exists and is not logged.
6. The local Qinglong `/open/crons` API returns exactly one task named `SteamGifts auto-entry`.
7. The task is enabled and matches the required schedule and command.
8. No `python3 sg.py` process has run for more than 50 minutes.
9. The task or heartbeat shows an execution within the previous 90 minutes after the initial deployment grace period.

### Recovery policy

| Failure | Recovery |
| --- | --- |
| Container missing or stopped | Run `docker compose up -d` from the project directory. |
| Panel unavailable after container start | Restart the Qinglong service once, then recheck. |
| Task missing | Recreate it through the local Qinglong `/open/crons` API using the required name, schedule, and command. |
| Task disabled | Enable it through `/open/crons/enable`. |
| Task schedule or command drift | Update it through `/open/crons`, preserving the single canonical task. |
| Task stale and no bot process is running | Trigger one task run through `/open/crons/run`. |
| Bot process older than 50 minutes | Stop the Qinglong task through `/open/crons/stop`; the next watchdog pass may trigger a fresh run. |
| Authentication or Cloudflare blocked | Do not restart or repeatedly rerun; notify once and wait for credential repair. |
| Steam review service unavailable | Keep the bot healthy but enter nothing; notify only after repeated consecutive failures. |

Recovery actions are rate-limited in `data/watchdog/state.json`. The watchdog performs at most one container restart and one task action per pass.

### Notifications

The watchdog sends a macOS notification only when the overall state changes:

- healthy to degraded;
- degraded to healthy; or
- degraded reason changes materially.

Notifications contain the component and a short action message, never credentials or raw response data. Local log entries use the same redacted messages.

## Security

- `settings.cfg`, Qinglong data, heartbeat, watchdog state, and watchdog logs remain ignored by Git.
- Files containing credentials or Qinglong tokens are never printed, attached to notifications, or included in exceptions.
- The watchdog reads the existing Qinglong token only to call the localhost `/open` API.
- The panel stays bound to `127.0.0.1`.
- No Docker socket is mounted into a container.
- Browser Cookie extraction is an operator action, not an unattended feature.

## Testing

### Selection tests

- accepts exactly 80% positive with 100 reviews;
- rejects below either threshold;
- rejects missing or malformed review data;
- caches one result per App ID;
- deduplicates giveaway codes;
- selects the maximum-spend combination without exceeding the balance;
- applies deterministic tie-breaking; and
- recalculates after an entry failure.

### Bot tests

- uses the browser-impersonation client;
- stops before entry on authentication or Cloudflare failure;
- writes each terminal heartbeat status without secrets; and
- preserves one-scan execution.

### Watchdog tests

- detects each unhealthy state from controlled command and API fixtures;
- chooses only the recovery action mapped to that state;
- suppresses recovery for authentication failures;
- rate-limits repeated actions and notifications;
- redacts sensitive values; and
- avoids overlapping runs.

### Deployment verification

- rebuild the image and run the complete test suite;
- recreate the Qinglong container and confirm it becomes healthy;
- verify the authenticated profile returns HTTP 200 through `curl_cffi`;
- install and load the LaunchAgent;
- run one watchdog pass in check-only mode, then one safe recovery fixture;
- run one controlled bot task and inspect its heartbeat;
- confirm the hourly task remains enabled; and
- confirm no secret or runtime file is tracked by Git.

## Source Control Delivery

Implementation and documentation are committed to `main`, pushed to `git@github.com:guiyu/steamgifts-qinglong.git`, and verified by comparing local `HEAD` with `origin/main`. Live credentials and runtime data are excluded from every commit.
