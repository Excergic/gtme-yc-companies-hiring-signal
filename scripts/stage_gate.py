#!/usr/bin/env python3
"""
Stage 3 - paid funding-stage gate. ~1 Deepline credit per company.

Only rows the free layer could not settle (classification=NEEDS_PAID_STAGE_CHECK)
are sent here, so the free stages do the volume and the paid call does the
deciding. Contract confirmed live against peopledatalabs_company_search:
  latest_funding_stage, funding_stages[], total_funding_raised,
  number_funding_rounds, employee_count, last_funding_date

Ambiguity rule: PDL returns latest_funding_stage="series_unknown" for companies
whose rounds were never labelled. That is NOT evidence of a late stage, so it
routes to NEEDS_REVIEW, never to ICP.

Usage:
  python3 scripts/stage_gate.py --dry-run       # show what it would call, free
  python3 scripts/stage_gate.py --limit 3       # spend on at most 3 companies
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import json, csv, sys, subprocess, argparse, pathlib, datetime

from paths import WORK_DIR as OUT, STAGE_CACHE as CACHE, load_config as _lc, ensure as _ensure
# Evidence only. The ICP verdict is NOT cached - it is recomputed from
# stage.accept on every run, so changing the target stage reclassifies for free.
CACHE_COLS = ["company_domain","funding_stage","funding_stages","total_funding_raised",
              "stage_status","stage_evidence","checked_on"]
CREDITS_PER_CALL = 1.0
# A Series B round is essentially never below this cumulative raise.
SERIES_B_FLOOR_USD = 20_000_000


def load_config():
    return _lc()


def pdl(domain):
    payload = {"sql": f"SELECT * FROM company WHERE website = '{domain}'", "size": 1}
    p = subprocess.run(
        ["deepline", "tools", "execute", "peopledatalabs_company_search",
         "--payload", json.dumps(payload), "--json"],
        capture_output=True, text=True, timeout=600,
        env={**__import__("os").environ, "DEEPLINE_SKIP_SELF_UPDATE": "1"})
    try:
        d = json.loads(p.stdout)
    except Exception:
        return None, f"unparsed CLI output: {p.stdout[:120] or p.stderr[:120]}"
    if d.get("error"):
        return None, f"{d['error'].get('code')}: {str(d['error'].get('message'))[:110]}"
    try:
        recs = d["toolResponse"]["rawV2"]["data"]["data"]
    except Exception:
        return None, "unexpected response shape"
    if not recs:
        return None, "no PDL record for this domain"
    return recs[0], None


def decide(rec, accept):
    return decide_from(rec.get("latest_funding_stage") or "",
                       rec.get("funding_stages") or [],
                       rec.get("total_funding_raised"), accept,
                       rec.get("number_funding_rounds"))


def decide_from(stage, stages, raised, accept, rounds=None):
    named = [s for s in stages if s in accept]
    # The LATEST stage governs. Funding history may only promote a company whose
    # latest stage is genuinely unlabelled - never one that sits below the floor.
    if stage in accept:
        return "ICP", f"latest_funding_stage={stage}, funding_stages={stages}", "HIGH"
    if stage in ("series_unknown", "", None) and named:
        return ("ICP",
                f"latest stage unlabelled but history contains {named}; "
                f"funding_stages={stages}", "MEDIUM")
    if stage and stage not in accept and named:
        return ("NON_ICP",
                f"latest_funding_stage={stage} is outside the target set even though "
                f"history contains {named}; latest stage governs", "HIGH")
    if stage in ("series_unknown", "", None) and isinstance(raised, (int, float)):
        if raised >= SERIES_B_FLOOR_USD:
            return ("NEEDS_REVIEW",
                    f"stage unlabelled but ${raised:,.0f} raised over "
                    f"{rounds} rounds - above the "
                    f"${SERIES_B_FLOOR_USD:,} Series B floor; confirm manually", "MEDIUM")
        return ("NON_ICP",
                f"stage unlabelled and only ${raised:,.0f} raised over "
                f"{rounds} rounds - below the late-stage floor", "HIGH")
    return "NON_ICP", f"latest_funding_stage={stage or '-'}, funding_stages={stages}", "HIGH"


def load_cache():
    if not CACHE.exists():
        return {}
    return {r["company_domain"]: r for r in csv.DictReader(CACHE.open())}


def save_cache(rows):
    """Persist every row that now carries real stage evidence."""
    cache = load_cache()
    for r in rows:
        if r.get("stage_status") in ("EVIDENCED", "EVIDENCED_PDL") and r.get("company_domain"):
            prev = cache.get(r["company_domain"], {})
            cache[r["company_domain"]] = {
                "company_domain": r["company_domain"],
                "funding_stage": r.get("funding_stage") or prev.get("funding_stage",""),
                "funding_stages": r.get("_funding_stages") or prev.get("funding_stages",""),
                "total_funding_raised": r.get("_total_raised") or prev.get("total_funding_raised",""),
                "stage_status": r.get("stage_status",""),
                "stage_evidence": r.get("stage_evidence",""),
                "checked_on": r.get("checked_on") or datetime.date.today().isoformat(),
            }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CACHE_COLS)
        w.writeheader()
        for v in cache.values():
            w.writerow({k: v.get(k,"") for k in CACHE_COLS})
    return len(cache)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    _ensure()
    accept = set(load_config()["stage"]["accept"])
    src = sorted(OUT.glob("companies_enriched_*.csv"))[-1]
    rows = list(csv.DictReader(src.open()))

    # a funding stage, once evidenced, is durable - replay it instead of re-paying
    cache = load_cache()
    replayed = 0
    for r in rows:
        hit = cache.get(r["company_domain"])
        if hit and r["classification"] == "NEEDS_PAID_STAGE_CHECK":
            r["funding_stage"] = hit["funding_stage"]
            r["stage_status"] = hit["stage_status"]
            r["stage_evidence"] = hit["stage_evidence"]
            stages = [x for x in (hit.get("funding_stages") or "").split("|") if x]
            raised = float(hit["total_funding_raised"] or 0)
            # carry evidence forward so a replay-only run cannot blank the cache
            r["_funding_stages"] = hit.get("funding_stages","")
            r["_total_raised"] = hit.get("total_funding_raised","")
            cls, why, conf = decide_from(hit["funding_stage"], stages, raised, accept)
            r["classification"], r["classification_reason"], r["stage_confidence"] = cls, why, conf
            replayed += 1
    if replayed:
        print(f"replayed {replayed} company stages from {CACHE.name} (0 credits)")

    todo = [r for r in rows if r["classification"] == "NEEDS_PAID_STAGE_CHECK" and r["company_domain"]]
    if a.limit:
        todo = todo[:a.limit]

    print(f"source: {src.name}")
    print(f"queued for paid check: {len(todo)}   "
          f"est. cost: {len(todo)*CREDITS_PER_CALL:.1f} credits\n")
    if a.dry_run:
        for r in todo:
            print(f"  would call PDL: {r['company_name']:22} {r['company_domain']}")
        print("dry run - no paid calls; the free cache replay is still written below")

    by_domain, billed = {}, 0
    for r in (() if a.dry_run else todo):
        rec, err = pdl(r["company_domain"])
        if err:
            r["stage_status"] = "LOOKUP_FAILED"
            r["classification"] = "NEEDS_REVIEW"
            r["classification_reason"] = f"paid stage lookup failed - {err}"
            print(f"  !! {r['company_name']:22} {err}")
            continue
        cls, why, conf = decide(rec, accept)
        r["_funding_stages"] = "|".join(rec.get("funding_stages") or [])
        r["_total_raised"] = str(rec.get("total_funding_raised") or "")
        r["funding_stage"] = rec.get("latest_funding_stage") or ""
        r["stage_status"] = "EVIDENCED_PDL"
        r["stage_evidence"] = (
            f"PDL: stage={rec.get('latest_funding_stage')}, "
            f"stages={rec.get('funding_stages')}, "
            f"raised=${(rec.get('total_funding_raised') or 0):,.0f}, "
            f"rounds={rec.get('number_funding_rounds')}, "
            f"last={rec.get('last_funding_date')}, employees={rec.get('employee_count')}")
        r["classification"] = cls
        r["classification_reason"] = why
        r["stage_confidence"] = conf
        by_domain[r["company_domain"]] = r
        billed += 1
        print(f"  {cls:13} {conf:7} {r['company_name'][:20]:22} {why[:76]}")

    stamp = datetime.date.today().isoformat()
    path = OUT / f"companies_stage_gated_{stamp}.csv"
    cols = list(rows[0].keys())
    for extra in ("stage_confidence",):
        if extra not in cols:
            cols.append(extra)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r.setdefault("stage_confidence", "")
            w.writerow(r)

    n = save_cache(rows)
    print(f"\nstage cache: {n} companies durable in {CACHE.name}")

    from collections import Counter
    print("=== classification after gate ===")
    for k, v in Counter(r["classification"] for r in rows).most_common():
        print(f"  {k:24} {v}")
    print(f"\nwrote {path.name}")
    if a.dry_run:
        print(f"credits spent this step: 0 (dry run; {len(todo)} would have been called)")
    else:
        print(f"credits spent this step: ~{billed*CREDITS_PER_CALL:.1f} "
              f"({billed} billed of {len(todo)} attempted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
