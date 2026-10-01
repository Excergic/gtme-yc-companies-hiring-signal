# Deploying to Railway

The pipeline runs as one service: a FastAPI app that serves the three lead
buckets and runs the weekly job in-process via APScheduler.

## 1. Push the repo

Railway deploys from a Git remote. The repo is already initialised and
committed locally; create an empty GitHub repo and push:

```bash
git remote add origin https://github.com/Excergic/gtme-yc-companies-hiring-signal.git
git push -u origin main
```

## 2. Create the service

In Railway: **New Project -> Deploy from GitHub repo**, pick this repo.
`railway.toml` already selects the Dockerfile builder and sets the health check
to `/health`, so no build config is needed.

## 3. Add a volume - do not skip this

Railway container filesystems are ephemeral. Without a volume every deploy
wipes `stage_cache.csv`, and the next run either re-pays for funding lookups
already bought or parks known companies in `needs_review`.

**Service -> Variables -> Volumes -> New Volume**, mount path:

```
/data
```

The Dockerfile already points `DATA_DIR=/data/data` and `OUT_DIR=/data/out` at
it. On first boot `scripts/paths.py:seed_if_empty()` copies whatever is in
`seed/` into the volume, filling only *missing* files and never overwriting
live state.

**This repo is public, so only `seed/stage_cache.csv` is committed** - company
domains and funding evidence, no personal data. That is the expensive part, so
the service still starts without re-paying for funding lookups. The contact-level
CSVs (`icp.csv`, `needs_review.csv`, `non_icp.csv`, `seen_contacts.csv`,
`email_cache.csv`) carry founder names, LinkedIn URLs and enriched addresses and
are gitignored. Consequences on first deploy:

- `out/*.csv` are empty until the first run completes - about 40 seconds.
- The dedupe ledger starts empty, so the first run flags every ICP contact
  `is_new_this_run=yes`. That is correct for a first run.
- To carry your local contact data over instead, copy the files onto the volume
  directly (`railway run cp ...`) or just let the first run rebuild them.

## 4. Set environment variables

| Variable | Value | Notes |
|---|---|---|
| `APP_TOKEN` | a long random string | **Required** for `POST /api/run`. Without it that endpoint returns 503 rather than exposing an unauthenticated trigger that can spend credits. |
| `DEEPLINE_API_KEY` | copy from `~/.local/deepline/code-deepline-com/.env` on your Mac | Needed only for stages 3 and 5. Without it stages 1, 2 and 4 still run. |
| `SCHEDULE_CRON` | `0 9 * * 1` | Monday 09:00. Standard 5-field cron. |
| `TZ` | `Asia/Kolkata` | The cron is evaluated in this zone. |
| `ALLOW_SPEND` | `0` | `1` lets the weekly run spend on the funding gate. Leave at `0` until the Deepline balance is funded. |

Generate a token with `openssl rand -hex 32`.

## 5. Verify

```bash
curl https://<your-app>.up.railway.app/health
curl https://<your-app>.up.railway.app/api/status
```

`/health` echoes `deepline_configured`, `allow_spend` and the active schedule,
so you can confirm the variables landed. Open the root URL for the dashboard.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | HTML dashboard: counts, ICP companies, CSV links |
| GET | `/health` | liveness, config echo, whether a run is in flight |
| GET | `/api/status` | last run summary, bucket counts, credit balance |
| GET | `/api/leads/{bucket}` | JSON rows; `?new_only=true` for this week's additions |
| GET | `/api/leads/{bucket}.csv` | CSV download |
| POST | `/api/run` | trigger a run; needs `X-API-Token`; `?allow_spend=true` to override |
| GET | `/api/logs` | recent run logs |
| GET | `/api/logs/{name}` | one run log |

`{bucket}` is `icp`, `needs_review` or `non_icp`.

Trigger a run by hand:

```bash
curl -X POST -H "X-API-Token: $APP_TOKEN" \
  https://<your-app>.up.railway.app/api/run
```

## Scheduling notes

The schedule is **in-process APScheduler**, so it only fires while the service
is running. Two consequences on Railway:

- The service must not sleep. On plans that idle a service, use a **Railway Cron
  Schedule** on a second service instead (same image, command
  `python3 scripts/harvest_yc.py && python3 scripts/enrich_yc.py && python3
  scripts/stage_gate.py && python3 scripts/build_sequence.py --commit`), sharing
  the same volume.
- `misfire_grace_time` is 3600s and `coalesce` is on, so a restart within an
  hour of the scheduled time still runs the job once, not repeatedly.

Set `DISABLE_SCHEDULER=1` to serve the API without any schedule.

## What is verified

Verified locally: all five pipeline stages under volume-style `DATA_DIR`/
`OUT_DIR` overrides, cold-volume seeding, every endpoint including CSV download
and `X-API-Token` auth (401 without, 200 with), the dashboard render, and
APScheduler registering the cron with the timezone.

Verified in Docker, on **both** `linux/arm64` and `linux/amd64` (Railway's
architecture), from a build context identical to this repo:

- image builds clean, 541 MB; node 20.20.2, Python 3.11.2, deepline 0.3.225
- first boot on an empty volume seeds `stage_cache.csv` and nothing else
- a full run via `POST /api/run` finishes in ~33 s with every stage exiting 0
- state survives `docker restart`: the second run flags 0 new ICP contacts,
  proving the ledger persisted on the volume
- all three CSV endpoints, the dashboard and token auth respond correctly
- no personal data in any image layer (`.dockerignore` keeps the build context
  equal to the repo)

### A note on changing counts

Two runs ten minutes apart returned 19 and 17 ICP contacts. That is not
non-determinism: the Humaans "Founding US GTM Lead" posting was pulled from
YC's board between them. The pipeline reports the board as it is at fetch time,
so a filled or withdrawn role disappears from `icp.csv`. The dedupe ledger still
remembers the contact was queued, but the CSV does not mark the role as closed -
worth knowing if you are mid-outreach on a lead that vanishes.
