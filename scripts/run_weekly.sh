#!/usr/bin/env bash
# Weekly run. Safe to invoke from cron/launchd, which give almost no environment.
#
# Free stages always run. The paid stage gate runs only when ALLOW_SPEND=1.
# Exit codes: 0 ok, 10 project volume missing, 11 deepline missing, 12 already running.
set -uo pipefail

# --- cron/launchd give a minimal PATH: pin the tools we need -----------------
export PATH="/Users/exergic/.nvm/versions/node/v22.6.0/bin:/Users/exergic/.pyenv/shims:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${HOME:-/Users/exergic}"

PROJECT="/Volumes/SeagateSSD/Projects/gtm-lead-gen-engine"
LOG_DIR="$PROJECT/data/logs"
LOCK="$PROJECT/data/.run.lock"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# --- guard 1: the project lives on an external volume ------------------------
if [[ ! -d "$PROJECT" ]]; then
  # cannot log into the project, so say it where launchd will capture it
  echo "$(ts) FATAL project volume not mounted: $PROJECT" >&2
  exit 10
fi

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run-$(date -u +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
cd "$PROJECT"

# --- guard 2: do not overlap with a run still in flight ---------------------
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(ts) SKIP a run is already in progress ($LOCK)"
  exit 12
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

echo "=== GTM lead gen - weekly run $(ts) ==="
echo "log: $LOG"

# --- guard 3: deepline must be reachable ------------------------------------
if ! command -v deepline >/dev/null 2>&1; then
  echo "$(ts) FATAL deepline not on PATH - stages 1-2 would run but the gate cannot"
  exit 11
fi
echo "deepline: $(command -v deepline) ($(deepline --version 2>/dev/null))"

echo; echo "--- stage 1: YC discovery (free) ---"
python3 scripts/harvest_yc.py || echo "stage 1 FAILED (exit $?)"

echo; echo "--- stage 2: YC profile enrichment (free) ---"
python3 scripts/enrich_yc.py || echo "stage 2 FAILED (exit $?)"

echo; echo "--- stage 3: funding-stage gate ---"
if [[ "${ALLOW_SPEND:-0}" == "1" ]]; then
  python3 scripts/stage_gate.py ${STAGE_LIMIT:+--limit "$STAGE_LIMIT"} || echo "stage 3 FAILED (exit $?)"
else
  python3 scripts/stage_gate.py --dry-run || true
  echo "ALLOW_SPEND is not 1 - paid gate skipped; new companies stay NEEDS_PAID_STAGE_CHECK"
fi

echo; echo "--- stage 4: the three final CSVs (free) ---"
python3 scripts/build_sequence.py ${COMMIT:+--commit} || echo "stage 4 FAILED (exit $?)"

echo; echo "--- new ICP contacts this run ---"
python3 - <<'PY' || true
import csv, pathlib
p = pathlib.Path("out/icp.csv")
if not p.exists():
    print("  out/icp.csv missing"); raise SystemExit
rows = list(csv.DictReader(p.open()))
new = [r for r in rows if r.get("is_new_this_run") == "yes"]
print(f"  {len(new)} new of {len(rows)} total ICP contacts")
for r in new:
    print(f"    {r['company_name'][:22]:24} {r['full_name'][:22]:24} {r['funding_stage']}")
PY

echo; echo "--- balance ---"
deepline billing balance --json 2>/dev/null \
  | python3 -c "import sys,json;print('  credits left:',json.load(sys.stdin).get('balance'))" \
  || echo "  balance unavailable"

# keep the last 12 logs
ls -1t "$LOG_DIR"/run-*.log 2>/dev/null | tail -n +13 | xargs -I{} rm -f {} 2>/dev/null || true
echo "=== done $(ts) ==="
