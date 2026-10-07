# SteamGifts Qinglong deployment verification

Verified locally on 2026-09-30 at 17:55 CST. This record intentionally excludes credentials, account names, response bodies, and giveaway URLs.

## Container and panel

- Qinglong version: `2.20.2`
- Local image: `local/steamgifts-qinglong:2.20.2`
- Image ID: `sha256:12b4cc5cfeb2d9d435111f33c0a841cf680ab7d271a15ae78e078f993f28783e`
- Container: `steamgifts-qinglong`, running and healthy
- Published endpoint: `127.0.0.1:5700` only
- Panel GET after restart: HTTP `200`
- Administrator initialization after restart: retained
- Persistent state mount: `./data:/ql/data`

## Runtime dependencies and secrets

- `requests 2.34.2` imports successfully inside the running container.
- `beautifulsoup4 4.15.0` imports successfully inside the running container.
- `flock` resolves to `/usr/bin/flock`.
- The live settings file is mode `0600` and ignored by Git.
- The SteamGifts profile and wishlist preflight both returned HTTP `200`, did not present a Cloudflare challenge, and exposed the authenticated points element.
- A harmless overlap probe confirmed that a second `flock -n` invocation exits immediately while the lock is held.

## Qinglong task

- Name: `SteamGifts auto-entry`
- Schedule: `0 * * * *`
- Command: `flock -n /tmp/steamgifts.lock bash -lc 'cd /ql/data/scripts/steam_gift && python3 sg.py'`
- State after verification: enabled and idle
- Next schedule evaluation at verification time: 2026-09-30 18:00:00 CST

The command uses an explicit shell because Qinglong passes the first command token to its task wrapper. A leading `cd ... &&` chain caused the Python process to start from `/ql`; the verified command keeps both the working-directory change and the non-blocking lock inside the task wrapper's supported execution model.

## Controlled execution

The final controlled run completed naturally in 496 seconds while the scheduled task remained disabled:

- Maximum simultaneous `sg.py` processes observed: `1`
- Cookie validation messages: `1`
- Points reports: `1`
- Candidate giveaways examined: `6`
- Successful entries: `1`
- Candidates skipped for insufficient points: `5`
- Completion summaries: `1`
- Qinglong task-end markers: `1`
- Uncaught tracebacks: `0`
- Site/network parsing errors: `0`
- Remaining `sg.py` processes after completion: `0`

## Restart persistence

After `docker compose restart qinglong`:

- The container returned to `running/healthy`.
- The panel returned HTTP `200` and remained initialized.
- The task retained its exact name, schedule, command, and enabled state.
- The generated Qinglong crontab retained the enabled hourly entry.
- Python dependencies and `flock` remained available.
- No duplicate or orphaned `sg.py` process remained.

## 2026-10-07 quality and watchdog upgrade

This upgrade record excludes credentials, account identity, response bodies, and giveaway URLs.

- The bot uses a browser-impersonating HTTP session and keeps authentication fields only in the ignored local settings file.
- The live Cloudflare path requires the explicit `chrome150` fingerprint plus browser navigation headers; the authenticated profile and giveaway listing both returned HTTP `200` after this combination was applied.
- Eligibility is fixed at at least 80% positive reviews and 100 total Steam reviews; missing or malformed review data is skipped.
- Deterministic point allocation maximizes spend without exceeding the available balance and recalculates after a failed entry.
- Every terminal path writes an atomic mode-`0600` heartbeat with only counters, timestamps, status, reason code, and process ID.
- The host watchdog checks Docker, container health, the local-only panel, the canonical hourly task, stale runs, and stuck processes.
- Authentication blocking produces no restart or task-run action. Recovery actions are single-pass and rate-limited.
- A local check-only watchdog pass reported `healthy` with the exact task name, schedule, command, and enabled state.
- The macOS LaunchAgent template uses a 300-second interval, `RunAtLoad`, absolute runtime paths, and no credentials.
- The installed LaunchAgent reported a 300-second interval, seven completed runs, and last exit code `0`.
- The final automated verification passed all 61 tests.

The controlled live run completed naturally with exit code `0` and a terminal `success/completed` heartbeat:

- Points before: `400`
- Qualified candidates: `9`
- Selected candidates: `9`
- Successful entries: `9`
- Points after: `230`
- Residual `sg.py` processes: `0`
- Uncaught tracebacks: `0`

All qualified candidates found during the successful scans were entered. The random listing had one transient request failure and performed its existing 300-second backoff; an immediate read-only retry returned HTTP `200` with giveaway rows present, so the enabled hourly task can retry that source without operator action.
