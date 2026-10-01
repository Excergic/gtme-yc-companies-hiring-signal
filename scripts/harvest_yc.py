#!/usr/bin/env python3
"""
Stage 1 - YC discovery. Zero Deepline credits.

YC's /jobs/role/<slug> pages are server-rendered: the whole posting list sits in
a data-page="..." JSON blob. That makes YC membership, batch, title and posting
date free and authoritative. Funding stage is NOT in that payload, so this stage
never decides ICP on its own - it emits stage_status=UNVERIFIED and leaves the
Series B+ gate to stage 2.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import re, html, json, csv, sys, time, pathlib, urllib.request, datetime

from paths import RAW_DIR as RAW, WORK_DIR as OUT, load_config as _lc, ensure as _ensure
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122 Safari/537.36"


def load_config():
    return _lc()


def fetch(slug, cache_hours=6):
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"role-{slug}.html"
    if path.exists() and (time.time() - path.stat().st_mtime) < cache_hours * 3600:
        return path.read_text(encoding="utf-8", errors="replace"), "cache"
    req = urllib.request.Request(
        f"https://www.ycombinator.com/jobs/role/{slug}", headers={"User-Agent": UA}
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        body = r.read().decode("utf-8", errors="replace")
    path.write_text(body, encoding="utf-8")
    return body, "live"


def postings(h):
    m = re.search(r'data-page="(.*?)"\s*>', h, re.S)
    if not m:
        return []
    return json.loads(html.unescape(m.group(1)))["props"].get("jobPostings", [])


def batch_year(batch):
    m = re.search(r"(\d{2})$", str(batch or ""))
    return 2000 + int(m.group(1)) if m else None


def main():
    _ensure()
    cfg = load_config()
    t = cfg["target"]
    strict = [re.compile(p, re.I) for p in t["title_patterns_strict"]]
    adj = [re.compile(p, re.I) for p in t["title_patterns_adjacent"]]
    excl = [re.compile(p, re.I) for p in t["title_patterns_exclude"]]
    marker = [re.compile(p, re.I) for p in t["title_patterns_early_stage_marker"]]

    jobs, fetched = {}, []
    for slug in cfg["sources"]["yc_job_roles"]:
        try:
            h, origin = fetch(slug)
            ps = postings(h)
            fetched.append((slug, len(ps), origin, "ok"))
            for j in ps:
                jobs[j["id"]] = j
        except Exception as e:
            fetched.append((slug, 0, "-", f"error: {type(e).__name__}"))

    rows, excluded = [], []
    for j in jobs.values():
        title = j.get("title") or ""
        s_hit = [p.pattern for p in strict if p.search(title)]
        a_hit = [p.pattern for p in adj if p.search(title)]
        if not (s_hit or a_hit):
            continue
        company = j.get("companyName") or ""
        # YC itself is not a prospect
        if company.strip().lower() in ("y combinator", "ycombinator"):
            continue
        if any(p.search(title) for p in excl):
            excluded.append((company, title))
            continue
        mk_hit = [p.pattern for p in marker if p.search(title)]
        rows.append({
            "company_name": company,
            "company_domain": "",
            "domain_miss_reason": "not resolved in stage 1 (free YC payload carries no domain)",
            "yc_batch": j.get("companyBatchName") or "",
            "yc_batch_year": batch_year(j.get("companyBatchName")) or "",
            "yc_proof_url": "https://www.ycombinator.com" + (j.get("url") or ""),
            "job_title": title,
            "title_match": "strict" if s_hit else "adjacent",
            "title_match_patterns": "|".join(s_hit or a_hit),
            "early_stage_marker": "|".join(mk_hit),
            "job_type": j.get("type") or "",
            "job_role_bucket": j.get("role") or "",
            "job_location": j.get("location") or "",
            "job_posted_rel": j.get("createdAt") or "",
            "job_last_active": j.get("lastActive") or "",
            "salary_range": j.get("salaryRange") or "",
            "company_one_liner": (j.get("companyOneLiner") or "")[:180],
            "company_url": j.get("companyUrl") or "",
            # stage is unknowable from the free payload - never guessed
            "funding_stage": "",
            "stage_status": "UNVERIFIED",
            "stage_miss_reason": "YC directory is client-side Algolia; stage needs a paid firmographic route (stage 2)",
            "classification": "PENDING_STAGE_GATE",
            "classification_reason": (
                "hiring signal present and YC membership proven; "
                "stage gate not yet run so ICP cannot be asserted"
            ),
            "harvested_at": datetime.date.today().isoformat(),
        })

    rows.sort(key=lambda r: (r["title_match"] != "strict", str(r["yc_batch_year"])))
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.date.today().isoformat()
    path = OUT / f"yc_gtm_candidates_{stamp}.csv"
    cols = list(rows[0].keys()) if rows else ["company_name"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    print("=== fetch ledger (role page -> postings) ===")
    for slug, n, origin, status in fetched:
        print(f"  {slug:18} {n:4} postings  [{origin}] {status}")
    print(f"\nunique postings scanned : {len(jobs)}")
    print(f"GTM-ish candidates      : {len(rows)}"
          f"  (strict={sum(1 for r in rows if r['title_match']=='strict')},"
          f" adjacent={sum(1 for r in rows if r['title_match']=='adjacent')})")
    print(f"first-GTM-hire marker  : {sum(1 for r in rows if r['early_stage_marker'])}")
    print(f"excluded (intern roles): {len(excluded)}")
    for c, t in excluded:
        print(f"    - {c}: {t}")
    print(f"\nwrote {path}")
    print("credits spent: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
