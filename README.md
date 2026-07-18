# Media Manager

Self-hosted web app for cleaning up a movie library: match folders against TMDB, pick the audio/subtitle tracks worth keeping, then shrink oversized BDRemux files with a verified HandBrake encode — or just remux/accept the ones that are already fine.

Single FastAPI + SQLite backend, single static HTML page, no build step.

## Workflow

1. **Scan** — walks the library, ffprobes every main video file, records codec/bitrate/resolution/tracks, matches titles against TMDB (manual re-match supported).
2. **Advice** — each movie gets a `SHRINK` or `FINE` verdict from bitrate vs resolution (>25 Mbps 4K, >15 Mbps 1080p, >8 Mbps SD). Advisory only.
3. **Tracks** — choose which audio/subtitle tracks to keep, their order, language tags, default/forced flags.
4. **Process** — one of:
   - **Quick Remux** (mkvmerge, minutes): drop tracks, fix metadata, no quality change.
   - **Heavy Encode** (HandBrake x265 10-bit, hours): shrink the file; 30s mid-movie samples at any RF let you eyeball quality and projected size first.
   - **Accept as-is**: file is already fine, mark it clean with no job.
5. **Verify + finalize** — output is ffprobed and compared against the requested track layout and source duration before the original can be deleted; everything is renamed to `Title (Year)`.

## Jobs

- One job at a time (they saturate the CPU); the rest queue.
- Jobs run in tmux sessions wrapped in systemd user scopes, so CPU quota is adjustable live.
- **Power switch** in the header: `auto` throttles during configurable work hours and runs full speed otherwise; `full` / `throttle` override manually. Changes apply to running jobs instantly.
- **Crash recovery**: on startup, jobs left `running` by a power loss are finished (if their log shows completion) or marked failed with partial output deleted.

## Running

### Host

```sh
cp run.sh.example run.sh   # fill in TMDB_API_KEY, adjust paths
chmod 700 run.sh
./run.sh                   # uv run uvicorn on :8500
```

### Docker / CasaOS

```sh
TMDB_API_KEY=... docker compose up -d
```

`docker-compose.yml` carries `x-casaos` metadata for CasaOS app management. The media volume must be mounted at the **same path** inside the container (the DB stores absolute paths). In-container jobs run without systemd scopes (`MM_NO_SYSTEMD=1`); cap CPU with the compose `cpus:` limit.

> Dolby Vision: many distro HandBrake builds lack libdovi and silently strip the DV RPU. Point `HANDBRAKE_CLI` at a DV-capable build (e.g. the CLI inside the `fr.handbrake.ghb` flatpak) before heavy-encoding DV titles.

### Config (env)

| Var | Default | Purpose |
|---|---|---|
| `TMDB_API_KEY` | — | TMDB matching (required) |
| `MEDIA_ROOT` | `/media/hdd1/Movies` | library root |
| `HANDBRAKE_CLI` | `HandBrakeCLI` | encoder binary (may be a command with args) |
| `MM_DB_PATH` / `MM_LOG_DIR` | alongside app | state location |
| `MM_WORK_HOURS` | `9-23` | default throttle window (editable in UI) |
| `MM_WORK_QUOTA` / `MM_FREE_QUOTA` | `300%` / `600%` | CPU quota in/out of work hours |
| `MM_NO_SYSTEMD` | unset | set to run jobs without systemd user scopes (Docker) |

## Mobile

The page ships an apple-touch icon and PWA meta tags — add it to the iOS home screen for an app-like experience.
