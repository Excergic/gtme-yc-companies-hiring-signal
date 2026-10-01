#!/usr/bin/env python3
"""
Stage 4 - sequence builder. Zero credits (email enrichment is a separate step).

Takes the gated companies plus the free founder contacts and writes the
sequence-ready CSV. Two rules make the weekly cadence work:

  1. Only ICP rows are eligible. NEEDS_REVIEW and NON_ICP are written to
     separate files so nothing silently enters outreach.
  2. A persistent ledger (data/seen_contacts.csv) is keyed on
     company_domain + person_linkedin, so each weekly run appends only
     contacts never queued before.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import csv, sys, pathlib, datetime, argparse

from paths import (ROOT, WORK_DIR as WORK, OUT_DIR as OUT, LEDGER,
                   EMAIL_CACHE, FINAL_FILES as FINAL, ensure as _ensure)
# One schema shared by all three files so they can be diffed or concatenated.
SEQ_COLS = ["queued_on","classification","classification_reason",
            "company_name","company_domain","yc_batch","team_size",
            "funding_stage","stage_status","stage_confidence","stage_evidence",
            "job_title_hiring_for","early_stage_marker","job_posted_rel","yc_proof_url",
            "full_name","person_title","person_linkedin","person_twitter",
            "email","email_status","sequence_status","is_new_this_run"]


def key(domain, li, name):
    return f"{(domain or '').lower()}|{(li or name or '').lower().rstrip('/')}"


def load_ledger():
    if not LEDGER.exists():
        return set()
    return {key(r["company_domain"], r.get("person_linkedin",""), r.get("full_name",""))
            for r in csv.DictReader(LEDGER.open())}


def append_ledger(rows):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new = not LEDGER.exists()
    with LEDGER.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["queued_on","company_domain","full_name","person_linkedin"])
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in
                        ["queued_on","company_domain","full_name","person_linkedin"]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="write to the ledger; without it the run is a preview")
    a = ap.parse_args()
    _ensure()

    comp_src = sorted(WORK.glob("companies_stage_gated_*.csv")) or sorted(WORK.glob("companies_enriched_*.csv"))
    comp = {r["company_domain"]: r for r in csv.DictReader(comp_src[-1].open()) if r["company_domain"]}
    contacts = list(csv.DictReader(sorted(WORK.glob("contacts_*.csv"))[-1].open()))
    emails = {}
    if EMAIL_CACHE.exists():
        emails = {r["person_key"]: r for r in csv.DictReader(EMAIL_CACHE.open())}
    today = datetime.date.today().isoformat()
    seen = load_ledger()

    buckets = {"ICP": [], "NEEDS_REVIEW": [], "NON_ICP": []}
    dupes = 0
    for c in contacts:
        company = comp.get(c["company_domain"])
        if not company:
            continue
        cls = company["classification"]
        bucket = cls if cls in buckets else "NEEDS_REVIEW"
        ehit = emails.get(f"{c['full_name'].lower()}|{c['company_domain'].lower()}")
        row = {
            "queued_on": today,
            "classification": cls,
            "classification_reason": company.get("classification_reason",""),
            "company_name": company["company_name"],
            "company_domain": company["company_domain"],
            "yc_batch": company.get("yc_batch",""),
            "team_size": company.get("team_size",""),
            "funding_stage": company.get("funding_stage",""),
            "stage_status": company.get("stage_status",""),
            "stage_confidence": company.get("stage_confidence",""),
            "stage_evidence": company.get("stage_evidence",""),
            "job_title_hiring_for": company.get("job_title",""),
            "early_stage_marker": company.get("early_stage_marker",""),
            "job_posted_rel": company.get("job_posted_rel",""),
            "yc_proof_url": company.get("yc_proof_url",""),
            "full_name": c["full_name"],
            "person_title": c["person_title"],
            "person_linkedin": c["person_linkedin"],
            "person_twitter": c.get("person_twitter",""),
            "email": (ehit or {}).get("email",""),
            "email_status": (ehit or {}).get("email_status","NOT_ENRICHED"),
            "sequence_status": "READY" if bucket == "ICP" else "HELD",
            "is_new_this_run": "",
        }
        is_new = key(row["company_domain"], row["person_linkedin"], row["full_name"]) not in seen
        row["is_new_this_run"] = "yes" if is_new else "no"
        if not is_new:
            dupes += 1
        buckets[bucket].append(row)

    OUT.mkdir(parents=True, exist_ok=True)
    # always write all three, even when empty, so the deliverable is stable
    for name, fname in FINAL.items():
        rows_out = buckets[name]
        p = OUT / fname
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SEQ_COLS, extrasaction="ignore")
            w.writeheader(); w.writerows(rows_out)
        print(f"  {name:13} {len(rows_out):4} contacts -> out/{fname}")

    new_icp = [r for r in buckets["ICP"] if r["is_new_this_run"] == "yes"]
    print(f"\nICP contacts seen before (flagged no): {dupes}")
    print(f"ICP contacts NEW this run             : {len(new_icp)}")
    if a.commit and new_icp:
        append_ledger(new_icp)
        print(f"ledger updated: +{len(new_icp)} -> {LEDGER.relative_to(ROOT)}")
    elif new_icp:
        print("preview only - rerun with --commit to record these in the ledger")
    else:
        print("no new ICP contacts this run (files still hold the full current set)")
    print("\nNOTE: emails are NOT enriched here. Run scripts/enrich_emails.py, which "
          "reads out/icp.csv only, so no credit is spent on a held row.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
