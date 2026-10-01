# GTM Lead Gen Engine

Finds companies hiring for GTM Engineer roles, proves their funding stage, and
writes sequence-ready CSVs. Built 2026-10-01.

## Run it

```bash
./scripts/run_weekly.sh                      # free stages, paid gate as dry-run
ALLOW_SPEND=1 COMMIT=1 ./scripts/run_weekly.sh   # paid gate + ledger write
```

## Pipeline

| Stage | Script | Source | Cost |
|---|---|---|---|
| 1 Discovery | `harvest_yc.py` | YC `/jobs/role/<slug>` x10 | **free** |
| 2 Enrichment | `enrich_yc.py` | YC `/companies/<slug>` | **free** |
| 3 Stage gate | `stage_gate.py` | PeopleDataLabs company search | ~1 credit/company, cached forever |
| 4 Sequence | `build_sequence.py` | local -> **the 3 final CSVs** | **free** |
| 5 Email | `enrich_emails.py` | icypeas + bounceban | ~0.20 credits/contact, cached |

Stages 1-2 are free because YC's pages are server-rendered: the whole payload
sits in a `data-page="..."` attribute. That yields YC batch (membership proof),
job title, posting date, company domain, team size, company LinkedIn, **founder
names + LinkedIn URLs**, and funding news headlines. Only the funding-stage
decision needs a paid provider, and only for rows the free layer cannot settle.

## Current target: seed / Series A

`stage.accept = ["seed","series_a"]`. The original spec was Series B+, which
measured empty on 2026-10-01: of 239 YC postings, 16 matched a GTM title, and
every ambiguous one verified as pre_seed, seed or series_a. YC's board is
dominated by current and recent batches, and companies past Series A stop
posting there. The evidence is preserved in `data/stage_cache.csv`.

Switching target stage is a one-line config change and costs nothing: the cache
stores funding **evidence**, not verdicts, so classification is recomputed from
`stage.accept` on every run.

### Result on 2026-10-01

239 postings scanned -> 14 GTM-title candidates (2 intern roles excluded)
-> **9 ICP companies, 19 contacts**, 4 non-ICP (pre_seed), 1 needs review.

| Company | Batch | Team | Stage |
|---|---|---|---|
| Coderhouse | W21 | 70 | series_a |
| Draftwise | S20 | 50 | series_a |
| Agave | W22 | 50 | series_a |
| Humaans | W21 | 48 | series_a |
| Orangewood Labs | W18 | 40 | series_unknown (seed in history) |
| Mach9 | S21 | 25 | seed |
| Weave | W25 | 16 | series_a |
| Thera | S22 | 15 | seed |
| Reflex | W23 | 10 | seed |

Excluded: Yarn (pre_seed), Soff (pre_seed), Rulebase (pre_seed), Nautilus
(pre_seed). AviaryAI needs review - no PDL record.

## Classification rules

- **Latest funding stage governs.** Funding *history* may promote a company only
  when its latest stage is genuinely unlabelled (`series_unknown`), never when
  the latest stage sits below the floor. Yarn is the worked example: history
  contains `seed` but latest is `pre_seed`, so it is non-ICP.
- `intern` in a title is excluded at stage 1 - never a real GTM-engineer hire.
- `founding` / `first` is a **positive** marker at seed/Series A (it marks the
  first dedicated GTM hire). If `stage.accept` is ever moved to Series B+, flip
  it to an inverse signal: a Series B company is not hiring "founding GTM".
- Unknown evidence routes to `NEEDS_REVIEW`, never to ICP.

## Provider availability on this workspace

Probed 2026-10-01 with empty payloads (a 400/422 is returned before billing, so
the scan was free).

| Status | Providers |
|---|---|
| Available | serper, exa, firecrawl, peopledatalabs, prospeo, predictleads, akta, hunter, leadmagic, icypeas, fullenrich, bounceban, hackernews (**free**) |
| Not enabled on plan | **theirstack**, **crustdata-v3**, harvestapi, bloomberry, leadmagic_jobs_finder, ai_ark, forager, contactout |
| Needs your own API key | apollo, amplemarket |
| Over budget | sentrion (one call exceeds a 25-credit balance) |

The two providers that could answer the whole ICP in a single cheap query -
TheirStack (`only_yc_companies` + `funding_stage_or`) and crustdata (investor
filter, 0.02/result) - are both unavailable. Getting either enabled is the
single highest-leverage unblock for this project.

## Credit economics

Balance started at 25 (no payment method on the account). Prices verified live:

| Tool | Credits |
|---|---|
| serper_google_search / firecrawl_scrape | 0.02 |
| hackernews_search | **0** |
| icypeas_email_search | 0.14 |
| hunter_email_finder | 0.30 |
| leadmagic_email_finder | 0.34 |
| prospeo_search_company / _person | 0.55 |
| predictleads_company_job_openings | 0.56 |
| peopledatalabs_company_search | 1.00 |
| peopledatalabs_person_search | 1.40 |
| bounceban_verify_bulk | 0.06 |

Because stages 1-2 are free and give contacts, the only per-lead cost is the
stage gate plus email: roughly **1.2 credits per finished lead**. 25 credits is
about 20 leads once - not a recurring weekly engine. Fund the account or get
TheirStack enabled before treating this as a standing process.

## State of each stage

- Stages 1-4 complete and runnable, free.
- Stage 5 (`enrich_emails.py`) works but is **budget-blocked**: 2 of 19 contacts
  were enriched before the balance fell below Deepline's minimum. bounceban
  verification never ran - `INSUFFICIENT_CREDITS`. The remaining 17 need ~3.4
  credits. Addresses live only in `data/email_cache.csv`, which is not committed.
- Contacts are usable without email: every one carries a LinkedIn URL.

## Not built yet

- **Reddit source.** Available free through the Scavio MCP connector, no
  Deepline credits.
- **LinkedIn job source.** Needs harvestapi or theirstack; neither is enabled.
- **Scheduling.** `run_weekly.sh` is cron-ready. A Deepline Play with
  `{ cron: { schedule: '0 9 * * 1' } }` is the alternative once the paid routes
  are funded.

## Layout

```
out/icp.csv            <- THE DELIVERABLE: contacts at companies that qualify
out/needs_review.csv   <- evidence was inconclusive; never auto-sequenced
out/non_icp.csv        <- verified outside the target stage, with the reason
                          (all three are gitignored - they hold personal data)

config/icp.yml           title patterns, accepted stages, role pages  <- edit this
scripts/                 the five stages + run_weekly.sh
data/work/               stage 1-3 intermediates (not the deliverable)
data/raw/                cached YC HTML (6h role pages, 24h profiles)
data/stage_cache.csv     funding-stage EVIDENCE, replayed free; verdicts recomputed
data/email_cache.csv     found addresses, replayed free
data/seen_contacts.csv   ledger behind the is_new_this_run flag
```

## The three output files

`out/` holds exactly three CSVs, all sharing one 23-column schema so they can be
diffed or concatenated. Each row is one **contact**, carrying its company's
evidence.

| File | Rows now | Meaning |
|---|---|---|
| `icp.csv` | 19 contacts / 9 companies | stage verified inside `stage.accept`, `sequence_status=READY` |
| `needs_review.csv` | 2 / 1 | evidence inconclusive, `HELD` - never auto-sequenced |
| `non_icp.csv` | 7 / 4 | stage verified outside target, with the disqualifying reason |

All three are rewritten in full on every run and always hold the complete
current set - a re-run never empties them. Weekly novelty is the
`is_new_this_run` column (driven by `data/seen_contacts.csv`), not row removal,
so filter on `is_new_this_run=yes` for just this week's additions.

Key columns: `classification_reason` and `stage_evidence` carry the proof for
every verdict; `yc_proof_url` links the actual job posting; `email_status` is
`NOT_ENRICHED`, `FOUND_UNVERIFIED`, `NOT_FOUND` or `LOOKUP_FAILED(...)` - never
blank-as-success.

Nothing writes to LinkedIn, email or any sequencer. Stage 4 produces CSVs only.
