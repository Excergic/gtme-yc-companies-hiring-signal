#!/usr/bin/env python3
"""
Stage 5 - email enrichment for ICP contacts only. ~0.20 credits per contact.

icypeas_email_search (0.14/result) finds the address, bounceban_verify_bulk
(0.06/result) verifies it. Runs only against sequence_icp_*.csv, so no credit is
ever spent on a held or non-ICP row. Results are cached in data/email_cache.csv
and replayed free.

Usage:
  python3 scripts/enrich_emails.py --dry-run
  python3 scripts/enrich_emails.py --limit 3      # bound the spend
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import json, csv, sys, os, subprocess, argparse, pathlib, datetime

from paths import OUT_DIR as OUT, EMAIL_CACHE as CACHE, ensure as _ensure
CACHE_COLS = ["person_key", "email", "email_status", "source", "checked_on"]
COST = 0.14 + 0.06


def dl(tool, payload):
    p = subprocess.run(
        ["deepline", "tools", "execute", tool, "--payload", json.dumps(payload), "--json"],
        capture_output=True, text=True, timeout=900,
        env={**os.environ, "DEEPLINE_SKIP_SELF_UPDATE": "1"})
    try:
        d = json.loads(p.stdout)
    except Exception:
        return None, f"unparsed: {(p.stdout or p.stderr)[:100]}"
    if d.get("error"):
        return None, f"{d['error'].get('code')}: {str(d['error'].get('message'))[:100]}"
    return d, None


def dig(o, keys):
    """Depth-first search for the first dict carrying any of `keys`."""
    if isinstance(o, dict):
        if any(k in o for k in keys):
            return o
        for v in o.values():
            r = dig(v, keys)
            if r:
                return r
    if isinstance(o, list):
        for v in o:
            r = dig(v, keys)
            if r:
                return r
    return None


def load_cache():
    if not CACHE.exists():
        return {}
    return {r["person_key"]: r for r in csv.DictReader(CACHE.open())}


def save_cache(cache):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CACHE_COLS)
        w.writeheader()
        for v in cache.values():
            w.writerow({k: v.get(k, "") for k in CACHE_COLS})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    src = OUT / "icp.csv"
    if not src.exists():
        print("out/icp.csv not found - run build_sequence.py first")
        return 0
    rows = list(csv.DictReader(src.open()))
    cache = load_cache()

    def pkey(r):
        return f"{r['full_name'].lower()}|{r['company_domain'].lower()}"

    todo, replayed = [], 0
    for r in rows:
        hit = cache.get(pkey(r))
        if hit:
            r["email"], r["email_status"] = hit["email"], hit["email_status"]
            replayed += 1
        elif r["full_name"] and r["company_domain"]:
            todo.append(r)
    if a.limit:
        todo = todo[:a.limit]

    print(f"source: {src.name}  ({len(rows)} ICP contacts)")
    if replayed:
        print(f"replayed from cache: {replayed} (0 credits)")
    print(f"queued: {len(todo)}   est. cost: {len(todo)*COST:.2f} credits\n")
    if a.dry_run:
        for r in todo:
            print(f"  would enrich: {r['full_name']:22} @ {r['company_domain']}")
        print("\ndry run - 0 credits spent")
        return 0

    found = []
    for r in todo:
        parts = r["full_name"].split()
        d, err = dl("icypeas_email_search", {
            "firstname": parts[0], "lastname": " ".join(parts[1:]) or parts[0],
            "domainOrCompany": r["company_domain"], "wait_for_completion": True,
            "max_wait_ms": 60000})
        if err:
            r["email_status"] = f"LOOKUP_FAILED ({err[:40]})"
            print(f"  !! {r['full_name']:22} {err[:60]}")
            continue
        rec = dig(d, ("email", "emails")) or {}
        email = rec.get("email") or ""
        if isinstance(rec.get("emails"), list) and rec["emails"] and not email:
            e0 = rec["emails"][0]
            email = e0.get("email", "") if isinstance(e0, dict) else str(e0)
        if not email:
            r["email_status"] = "NOT_FOUND"
            print(f"  -- {r['full_name']:22} no email found")
            continue
        r["email"], r["email_status"] = email, "FOUND_UNVERIFIED"
        found.append(r)
        print(f"  ok {r['full_name']:22} {email}")

    # one bulk verify for everything found
    if found:
        d, err = dl("bounceban_verify_bulk", {"emails": [r["email"] for r in found],
                                              "name": f"gtm-{datetime.date.today()}"})
        if err:
            print(f"  !! verify failed: {err[:80]}")
        else:
            blob = json.dumps(d)
            for r in found:
                st = "VERIFY_PENDING"
                node = dig(d, ("result", "status", "state"))
                if isinstance(node, dict):
                    st = str(node.get("result") or node.get("status") or st)
                r["email_status"] = f"VERIFIED:{st}" if r["email"] in blob else "VERIFY_NO_RESULT"
            print(f"  verified {len(found)} addresses")

    for r in rows:
        if r.get("email"):
            cache[pkey(r)] = {"person_key": pkey(r), "email": r["email"],
                              "email_status": r["email_status"], "source": "icypeas",
                              "checked_on": datetime.date.today().isoformat()}
    save_cache(cache)

    with src.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    from collections import Counter
    print("\n=== email status ===")
    for k, v in Counter(r.get("email_status") or "NOT_ENRICHED" for r in rows).most_common():
        print(f"  {k:28} {v}")
    print(f"\nupdated {src.name}; cache: {len(cache)} people")
    print(f"credits spent this step: ~{len(todo)*COST:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
