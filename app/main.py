"""
GTM lead gen backend.

Serves the three lead buckets over HTTP and runs the pipeline on a schedule.
Designed for Railway: all mutable state lives under DATA_DIR/OUT_DIR, which
should point at a mounted volume.

Endpoints
  GET  /                      HTML dashboard
  GET  /health                liveness + whether a run is in flight
  GET  /api/status            last run summary, counts, credit balance
  GET  /api/leads/{bucket}    JSON rows (icp | needs_review | non_icp)
  GET  /api/leads/{bucket}.csv  CSV download
  POST /api/run               trigger a run (requires X-API-Token)
  GET  /api/logs              recent run log filenames
  GET  /api/logs/{name}       one run log as plain text
"""
import csv, io, json, os, subprocess, sys, threading, datetime, pathlib

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import paths  # noqa: E402

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
APP_TOKEN = os.environ.get("APP_TOKEN") or ""
ALLOW_SPEND = os.environ.get("ALLOW_SPEND", "0") == "1"
SCHEDULE_CRON = os.environ.get("SCHEDULE_CRON", "0 9 * * 1")
BUCKETS = {"icp": "ICP", "needs_review": "NEEDS_REVIEW", "non_icp": "NON_ICP"}

app = FastAPI(title="GTM Lead Gen Engine", version="1.0")

_lock = threading.Lock()
_state = {"running": False, "last": None}
STATE_PATH = paths.DATA_DIR / "last_run.json"


def _load_state():
    if STATE_PATH.exists():
        try:
            _state["last"] = json.loads(STATE_PATH.read_text())
        except Exception:
            pass


def _save_state():
    paths.ensure()
    STATE_PATH.write_text(json.dumps(_state["last"], indent=2))


def _stage(name, args, log):
    """Run one pipeline stage, capturing output into the run log."""
    cmd = [sys.executable, str(SCRIPTS / name)] + args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                       cwd=str(SCRIPTS.parent), env={**os.environ})
    log.append(f"$ {' '.join(cmd[1:])}\n{p.stdout}{p.stderr}")
    return p.returncode


def run_pipeline(allow_spend=None, commit=True):
    """The whole chain. Returns a summary dict; never raises to the caller."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "a run is already in progress"}
    spend = ALLOW_SPEND if allow_spend is None else allow_spend
    started = datetime.datetime.now(datetime.timezone.utc)
    log, stages = [], {}
    _state["running"] = True
    try:
        paths.ensure()
        stages["harvest"] = _stage("harvest_yc.py", [], log)
        stages["enrich"] = _stage("enrich_yc.py", [], log)
        stages["stage_gate"] = _stage(
            "stage_gate.py", [] if spend else ["--dry-run"], log)
        stages["sequence"] = _stage(
            "build_sequence.py", ["--commit"] if commit else [], log)

        counts, new_icp = {}, 0
        for slug in BUCKETS:
            rows = read_bucket(slug)
            counts[slug] = len(rows)
            if slug == "icp":
                new_icp = sum(1 for r in rows if r.get("is_new_this_run") == "yes")

        finished = datetime.datetime.now(datetime.timezone.utc)
        summary = {
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_s": round((finished - started).total_seconds(), 1),
            "allow_spend": spend,
            "stage_exit_codes": stages,
            "counts": counts,
            "new_icp_contacts": new_icp,
            "ok": all(v == 0 for v in stages.values()),
        }
        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (paths.LOG_DIR / f"run-{started:%Y%m%d-%H%M%S}.log").write_text(
            json.dumps(summary, indent=2) + "\n\n" + "\n".join(log))
        _state["last"] = summary
        _save_state()
        _prune_logs()
        return summary
    finally:
        _state["running"] = False
        _lock.release()


def _prune_logs(keep=20):
    logs = sorted(paths.LOG_DIR.glob("run-*.log"), reverse=True)
    for p in logs[keep:]:
        p.unlink(missing_ok=True)


def read_bucket(slug):
    p = paths.OUT_DIR / paths.FINAL_FILES[BUCKETS[slug]]
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _balance():
    try:
        p = subprocess.run(["deepline", "billing", "balance", "--json"],
                           capture_output=True, text=True, timeout=120)
        return json.loads(p.stdout).get("balance")
    except Exception:
        return None


# ---------------------------------------------------------------- endpoints

@app.get("/health")
def health():
    return {"ok": True, "running": _state["running"],
            "data_dir": str(paths.DATA_DIR), "out_dir": str(paths.OUT_DIR),
            "deepline_configured": bool(os.environ.get("DEEPLINE_API_KEY")),
            "allow_spend": ALLOW_SPEND, "schedule": SCHEDULE_CRON}


@app.get("/api/status")
def status():
    return {"running": _state["running"], "last_run": _state["last"],
            "counts": {s: len(read_bucket(s)) for s in BUCKETS},
            "credits": _balance(), "schedule": SCHEDULE_CRON,
            "allow_spend": ALLOW_SPEND}


@app.get("/api/leads/{bucket}.csv")
def leads_csv(bucket: str):
    if bucket not in BUCKETS:
        raise HTTPException(404, f"unknown bucket; use one of {list(BUCKETS)}")
    p = paths.OUT_DIR / paths.FINAL_FILES[BUCKETS[bucket]]
    if not p.exists():
        raise HTTPException(404, "not generated yet - trigger a run first")
    return StreamingResponse(
        io.BytesIO(p.read_bytes()), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{p.name}"'})


@app.get("/api/leads/{bucket}")
def leads(bucket: str, new_only: bool = Query(False)):
    if bucket not in BUCKETS:
        raise HTTPException(404, f"unknown bucket; use one of {list(BUCKETS)}")
    rows = read_bucket(bucket)
    if new_only:
        rows = [r for r in rows if r.get("is_new_this_run") == "yes"]
    return {"bucket": bucket, "count": len(rows), "rows": rows}


@app.post("/api/run")
def trigger(x_api_token: str = Header(default=""),
            allow_spend: bool | None = Query(None)):
    if not APP_TOKEN:
        raise HTTPException(503, "APP_TOKEN is not set; refusing to expose an "
                                 "unauthenticated trigger that can spend credits")
    if x_api_token != APP_TOKEN:
        raise HTTPException(401, "bad or missing X-API-Token")
    if _state["running"]:
        raise HTTPException(409, "a run is already in progress")
    threading.Thread(target=run_pipeline, kwargs={"allow_spend": allow_spend},
                     daemon=True).start()
    return {"started": True, "allow_spend": ALLOW_SPEND if allow_spend is None else allow_spend}


@app.get("/api/logs")
def logs():
    return {"logs": [p.name for p in sorted(paths.LOG_DIR.glob("run-*.log"), reverse=True)]}


@app.get("/api/logs/{name}", response_class=PlainTextResponse)
def log_one(name: str):
    p = paths.LOG_DIR / name
    if ".." in name or "/" in name or not p.exists():
        raise HTTPException(404, "no such log")
    return p.read_text()


@app.get("/", response_class=HTMLResponse)
def dashboard():
    counts = {s: len(read_bucket(s)) for s in BUCKETS}
    icp = read_bucket("icp")
    last = _state["last"] or {}
    comp = {}
    for r in icp:
        comp.setdefault(r["company_name"], r)
    rows = "".join(
        f"<tr><td>{r['company_name']}</td><td>{r['yc_batch']}</td>"
        f"<td style='text-align:right'>{r['team_size']}</td><td>{r['funding_stage']}</td>"
        f"<td>{r['job_title_hiring_for'][:44]}</td>"
        f"<td><a href='{r['yc_proof_url']}' target='_blank' rel='noopener'>posting</a></td></tr>"
        for r in comp.values()) or "<tr><td colspan=6>no ICP rows yet</td></tr>"
    return f"""<!doctype html><meta charset=utf-8>
<title>GTM Lead Gen Engine</title>
<style>
 body{{font:14px/1.5 system-ui,-apple-system,sans-serif;margin:0;padding:24px 16px;
   background:#fafaf9;color:#1c1917;max-width:1000px}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#78716c;margin-bottom:20px}}
 .cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}}
 .card{{background:#fff;border:1px solid #e7e5e4;border-radius:8px;padding:12px 16px;min-width:120px}}
 .n{{font-size:24px;font-weight:600}} .l{{color:#78716c;font-size:12px;text-transform:uppercase}}
 table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e7e5e4;border-radius:8px}}
 th,td{{padding:8px 10px;border-bottom:1px solid #f5f5f4;text-align:left;font-size:13px}}
 th{{background:#f5f5f4;font-size:11px;text-transform:uppercase;color:#57534e}}
 code{{background:#f5f5f4;padding:1px 5px;border-radius:4px;font-size:12px}}
 a{{color:#0369a1}}
 @media (prefers-color-scheme:dark){{
  body{{background:#1c1917;color:#f5f5f4}} .card,table{{background:#292524;border-color:#44403c}}
  th{{background:#44403c;color:#d6d3d1}} td{{border-color:#3f3f46}} code{{background:#44403c}}
  .sub,.l{{color:#a8a29e}} a{{color:#38bdf8}} }}
</style>
<h1>GTM Lead Gen Engine</h1>
<div class=sub>YC companies hiring GTM-engineer roles, stage-verified.
 Schedule <code>{SCHEDULE_CRON}</code> &middot; spend {'on' if ALLOW_SPEND else 'off'}
 &middot; last run {last.get('finished_at','never')}</div>
<div class=cards>
 <div class=card><div class=n>{counts['icp']}</div><div class=l>ICP contacts</div></div>
 <div class=card><div class=n>{len(comp)}</div><div class=l>ICP companies</div></div>
 <div class=card><div class=n>{last.get('new_icp_contacts','-')}</div><div class=l>new last run</div></div>
 <div class=card><div class=n>{counts['needs_review']}</div><div class=l>needs review</div></div>
 <div class=card><div class=n>{counts['non_icp']}</div><div class=l>non-ICP</div></div>
</div>
<table><tr><th>company<th>batch<th>team<th>stage<th>hiring for<th>proof</tr>{rows}</table>
<p class=sub>CSV: <a href=/api/leads/icp.csv>icp</a> &middot;
 <a href=/api/leads/needs_review.csv>needs_review</a> &middot;
 <a href=/api/leads/non_icp.csv>non_icp</a> &middot;
 JSON at <code>/api/leads/icp</code> &middot; <a href=/api/status>status</a></p>"""


# ---------------------------------------------------------------- scheduler

@app.on_event("startup")
def start_scheduler():
    _load_state()
    paths.ensure()
    if os.environ.get("DISABLE_SCHEDULER") == "1":
        print("scheduler disabled by DISABLE_SCHEDULER=1", flush=True)
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("apscheduler not installed - no in-process schedule", flush=True)
        return
    sched = BackgroundScheduler(timezone=os.environ.get("TZ", "UTC"))
    sched.add_job(run_pipeline, CronTrigger.from_crontab(SCHEDULE_CRON),
                  id="weekly", max_instances=1, coalesce=True,
                  misfire_grace_time=3600)
    sched.start()
    print(f"scheduler started: {SCHEDULE_CRON} tz={os.environ.get('TZ','UTC')}", flush=True)
