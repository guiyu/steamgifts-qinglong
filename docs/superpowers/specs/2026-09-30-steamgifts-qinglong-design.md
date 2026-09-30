# SteamGifts Qinglong Deployment Design

Date: 2026-09-30

## Purpose

Deploy the supplied SteamGifts auto-entry bot in a dedicated local Qinglong container. The deployment must be reproducible, isolated from the existing Qinglong backup, accessible only from the local Mac, and safe from accidental secret disclosure or duplicate bot processes.

## Confirmed decisions

- Create a new project at `/Users/weiyi/workspaces/steamgifts-qinglong`.
- Do not start, modify, or import the old Qinglong backup under `mini4-server`.
- Bind the Qinglong panel only to `127.0.0.1:5700`.
- Persist Qinglong state in `./data`, mounted at `/ql/data`.
- Pin Qinglong to `whyour/qinglong:2.20.2` instead of using a floating tag.
- Run one SteamGifts scan per Qinglong invocation.
- Schedule the task hourly with `0 * * * *` in the `Asia/Shanghai` timezone.
- Do not configure external notifications initially.
- Keep the supplied giveaway filters and the 10-point threshold unless the user changes them later.

## Architecture

The project contains a Docker Compose service named `qinglong`. A small Dockerfile extends the pinned Qinglong image and installs only the runtime dependencies required by the supplied bot:

- `requests`
- `beautifulsoup4`
- `util-linux`, which provides `flock`

Compose uses `restart: unless-stopped`, sets `TZ=Asia/Shanghai`, maps `127.0.0.1:5700` to container port 5700, and mounts `./data` at `/ql/data`. The project does not expose the Docker socket and does not publish the panel to the LAN.

The supplied bot directory is copied to `data/scripts/steam_gift` on the host. This keeps Qinglong's script editor and logs usable while retaining the files across container replacement.

## Project layout

```text
steamgifts-qinglong/
├── compose.yaml
├── Dockerfile
├── .dockerignore
├── .gitignore
├── README.md
├── docs/superpowers/specs/
└── data/                         # persistent, ignored by Git
    └── scripts/steam_gift/
        ├── sg.py
        ├── notify.py
        ├── notify.js
        ├── settings.cfg          # contains credentials; mode 0600
        ├── search.txt
        ├── won.txt
        ├── bad_giveaways_link.txt
        └── black_list_games_name.txt
```

## Bot execution model

The supplied `sg.py` currently loops forever and sleeps for a random 30 to 60 minutes between scans. It will be changed surgically so that one invocation performs one complete scan and then exits. The existing in-scan randomized delays are retained so that requests are not emitted in a burst.

Qinglong will create one task with:

- Name: `SteamGifts auto-entry`
- Schedule: `0 * * * *`
- Command: `cd /ql/data/scripts/steam_gift && flock -n /tmp/steamgifts.lock python3 sg.py`

The lock prevents overlapping processes if a scan takes longer than one hour. A skipped overlapping trigger is acceptable; the next hourly trigger will try again. Container restarts do not immediately force a run but the task resumes at the next hourly boundary.

## Credentials and secret handling

The bot needs exactly two SteamGifts authentication fields: `PHPSESSID` and the matching browser `User-Agent`.

- Values are stored only in `data/scripts/steam_gift/settings.cfg`.
- The file is set to mode `0600`.
- `data/` is excluded from Git and Docker build context.
- Values are never copied into Compose, the Dockerfile, README, design documents, commands that print them, or task logs.
- The previously discovered cookie from the old Qinglong backup is not reused.
- The Qinglong administrator password is entered by the user in the local panel and is not requested in chat.

Before enabling the hourly task, an authenticated `GET` request to the SteamGifts profile settings page verifies that the session remains on the authenticated page and is not redirected to the home or sign-in page. This validation does not enter a giveaway.

## Configuration behavior

The initial non-secret bot settings remain aligned with the supplied archive:

- banner giveaways enabled
- group giveaways enabled
- wishlist giveaways enabled
- recommended giveaways enabled
- search-list giveaways enabled
- random-list giveaways enabled
- minimum remaining-point threshold set to 10

No external push token is configured. Console output remains available in Qinglong logs. The unused or ineffective legacy settings are not expanded into new features.

## Deployment flow

1. Create the project files and extract the supplied bot into the ignored persistent data directory.
2. Apply the one-run modification to `sg.py` and add focused tests for that behavior.
3. Build and start the dedicated Qinglong container.
4. Verify the panel is reachable only through `127.0.0.1:5700`.
5. Let the user complete Qinglong's local administrator initialization.
6. Store the SteamGifts fields in `settings.cfg` without printing them and restrict file permissions.
7. Validate dependency imports and the authenticated SteamGifts session.
8. Create the disabled hourly task through Qinglong's supported UI or API, not by directly editing its database.
9. Run one controlled task execution, which may enter eligible giveaways and consume SteamGifts points.
10. Inspect logs and process state, then enable the hourly schedule.
11. Restart the container and verify the panel recovers and the task remains scheduled.

## Error handling

- If image build or startup fails, leave the persistent data intact and inspect the Compose logs.
- If authentication validation redirects away from the profile settings page, keep the task disabled and request a new cookie.
- If SteamGifts presents a challenge or changes its HTML, stop before enabling automation and report the incompatibility.
- If dependency import fails, repair the image rather than installing packages only into a running container.
- If the controlled task reports a site or parsing error, do not treat the deployment as complete.
- If the task is already locked, record it as an overlap skip rather than starting a second bot.

## Verification criteria

The deployment is complete only when all of the following are true:

- `docker compose config` succeeds.
- The custom image builds successfully for the local architecture.
- The container is running with `restart: unless-stopped`.
- Port 5700 is bound only on `127.0.0.1`.
- The Qinglong panel responds successfully.
- `requests`, `bs4`, and `flock` are available inside the container.
- The bot source compiles with `python3 -m py_compile`.
- A test proves that one bot invocation does not enter the former infinite loop.
- The credential file has mode `0600` and is ignored by Git.
- The SteamGifts session validation succeeds without performing an entry.
- One controlled task run produces a Qinglong log and exits without leaving a duplicate process.
- The task is enabled with the hourly schedule.
- The panel and persisted task recover after a container restart.

## Rollback

`docker compose down` stops and removes the dedicated container while retaining `./data`. No old Qinglong data is touched. Persistent data is deleted only after a separate explicit user request.

## Known risk

Automated SteamGifts participation can lead to account restrictions or bans. The deployment cannot eliminate that service-policy risk. The supplied script also depends on SteamGifts HTML structure and may require maintenance if the site changes.
