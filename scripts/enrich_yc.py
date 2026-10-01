#!/usr/bin/env python3
"""
Stage 2 - YC company profile enrichment. Zero Deepline credits.

Each /companies/<slug> page is server-rendered and carries website, team_size,
company LinkedIn, founders (name + title + LinkedIn) and newsItems. That covers
domain resolution, a headcount stage proxy, contacts and funding evidence
without a paid provider.

Funding stage is only asserted when a news headline states the round in words.
Otherwise stage_status stays UNVERIFIED and the row is never called ICP - the
headcount band is recorded as a proxy, not as proof.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import re, html, json, csv, sys, time, pathlib, urllib.request, datetime

from paths import RAW_DIR, WORK_DIR as OUT, load_config as _lc, ensure as _ensure
RAW = RAW_DIR / "companies"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122 Safari/537.36"

ROUND_PATTERNS = [
    ("series_j", r"series\s*j\b"), ("series_i", r"series\s*i\b"),
    ("series_h", r"series\s*h\b"), ("series_g", r"series\s*g\b"),
    ("series_f", r"series\s*f\b"), ("series_e", r"series\s*e\b"),
    ("series_d", r"series\s*d\b"), ("series_c", r"series\s*c\b"),
    ("series_b", r"series\s*b\b"), ("series_a", r"series\s*a\b"),
    ("seed",    r"\bseed\b"),      ("pre_seed", r"pre.?seed"),
]
STAGE_ORDER = {k: i for i, k in enumerate(
    ["pre_seed","seed","series_a","series_b","series_c","series_d",
     "series_e","series_f","series_g","series_h","series_i","series_j"])}


def load_config():
    return _lc()


def fetch_profile(slug, cache_hours=24):
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{slug.replace('/','_')}.html"
    if path.exists() and (time.time() - path.stat().st_mtime) < cache_hours * 3600:
        return path.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(
        f"https://www.ycombinator.com/companies/{slug}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        body = r.read().decode("utf-8", errors="replace")
    path.write_text(body, encoding="utf-8")
    time.sleep(0.6)   # be a polite client on a free public source
    return body


def payload(h):
    m = re.search(r'data-page="(.*?)"\s*>', h, re.S)
    return json.loads(html.unescape(m.group(1)))["props"] if m else {}


def domain_of(url):
    if not url:
        return ""
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", url).split("/")[0]).lower()


def latest_round(news):
    """Highest round named in any headline, with the headline as the receipt."""
    best, receipt = None, None
    for n in news or []:
        t = (n.get("title") or "")
        for key, pat in ROUND_PATTERNS:
            if re.search(pat, t, re.I):
                if best is None or STAGE_ORDER.get(key, -1) > STAGE_ORDER.get(best, -1):
                    best, receipt = key, n
                break
    return best, receipt


def headcount_band(n):
    if not n:
        return "unknown"
    n = int(n)
    if n <= 10:  return "1-10 (pre-seed/seed)"
    if n <= 25:  return "11-25 (seed/A)"
    if n <= 60:  return "26-60 (A/B)"
    if n <= 150: return "61-150 (B/C)"
    return "150+ (C+)"


def main():
    _ensure()
    cfg = load_config()
    accept = set(cfg["stage"]["accept"])
    src = sorted(OUT.glob("yc_gtm_candidates_*.csv"))[-1]
    cands = list(csv.DictReader(src.open()))
    print(f"source: {src.name}  ({len(cands)} candidates)\n")

    companies, contacts = [], []
    for r in cands:
        slug = (r["company_url"] or "").replace("/companies/", "").strip("/")
        if not slug:
            continue
        try:
            p = payload(fetch_profile(slug))
        except Exception as e:
            print(f"  !! {r['company_name']}: fetch failed {type(e).__name__}")
            continue
        c = p.get("company") or {}
        news = p.get("newsItems") or []
        rnd, receipt = latest_round(news)
        team = c.get("team_size")

        if rnd:
            stage_status = "EVIDENCED"
            stage_ev = f"{receipt['title']} ({receipt.get('date','')}) {receipt.get('url','')}"
        else:
            stage_status = "UNVERIFIED"
            stage_ev = ""

        if stage_status == "EVIDENCED":
            icp = "ICP" if rnd in accept else "NON_ICP"
            why = (f"last round named in YC news = {rnd}; "
                   f"{'in' if rnd in accept else 'not in'} target stage set")
        else:
            icp = "NEEDS_PAID_STAGE_CHECK"
            why = ("no round stated in YC news; headcount band is a proxy only, "
                   "not proof - route to a paid firmographic lookup")

        # Classification depends only on evidenced stage vs stage.accept.
        # Title markers are prioritisation columns, never gates.

        companies.append({
            "company_name": c.get("name") or r["company_name"],
            "company_domain": domain_of(c.get("website")),
            "website": c.get("website") or "",
            "yc_batch": c.get("batch") or r["yc_batch"],
            "yc_status": c.get("ycdc_status") or "",
            "team_size": team if team is not None else "",
            "headcount_band": headcount_band(team),
            "company_linkedin": c.get("linkedin_url") or "",
            "job_title": r["job_title"],
            "title_match": r["title_match"],
            "early_stage_marker": r["early_stage_marker"],
            "job_posted_rel": r["job_posted_rel"],
            "yc_proof_url": r["yc_proof_url"],
            "funding_stage": rnd or "",
            "stage_status": stage_status,
            "stage_evidence": stage_ev,
            "news_item_count": len(news),
            "open_roles_total": len(p.get("jobPostings") or []),
            "classification": icp,
            "classification_reason": why,
            "tags": "|".join(c.get("tags") or []),
            "harvested_at": datetime.date.today().isoformat(),
        })

        for f in (c.get("founders") or []):
            contacts.append({
                "company_name": c.get("name") or r["company_name"],
                "company_domain": domain_of(c.get("website")),
                "classification": icp,
                "full_name": f.get("full_name") or "",
                "person_title": f.get("title") or "",
                "person_linkedin": f.get("linkedin_url") or "",
                "person_twitter": f.get("twitter_url") or "",
                "has_email_on_yc": f.get("has_email"),
                "email": "",
                "email_miss_reason": "not enriched (paid email route not yet run)",
                "job_title_hiring_for": r["job_title"],
                "yc_proof_url": r["yc_proof_url"],
                "funding_stage": rnd or "",
                "stage_status": stage_status,
                "harvested_at": datetime.date.today().isoformat(),
            })

    stamp = datetime.date.today().isoformat()
    cpath = OUT / f"companies_enriched_{stamp}.csv"
    ppath = OUT / f"contacts_{stamp}.csv"
    for path, rows in ((cpath, companies), (ppath, contacts)):
        if not rows:
            continue
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)

    from collections import Counter
    print("=== classification ===")
    for k, v in Counter(c["classification"] for c in companies).most_common():
        print(f"  {k:24} {v}")
    print("\n=== stage evidence ===")
    for k, v in Counter(c["stage_status"] for c in companies).most_common():
        print(f"  {k:24} {v}")
    print(f"\ncompanies: {len(companies)} -> {cpath.name}")
    print(f"contacts : {len(contacts)} -> {ppath.name}")
    print("credits spent: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
