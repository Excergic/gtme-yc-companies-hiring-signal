"""
Single source of truth for where data lives.

Locally everything sits beside the code. On Railway the container filesystem is
ephemeral, so DATA_DIR and OUT_DIR are pointed at a mounted volume - otherwise
the stage/email caches vanish on every deploy and the next run re-pays for
funding lookups it has already bought.
"""
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR") or (ROOT / "data"))
OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR") or (ROOT / "out"))
CONFIG_PATH = pathlib.Path(os.environ.get("ICP_CONFIG") or (ROOT / "config" / "icp.yml"))

RAW_DIR = DATA_DIR / "raw"
WORK_DIR = DATA_DIR / "work"
LOG_DIR = DATA_DIR / "logs"
STAGE_CACHE = DATA_DIR / "stage_cache.csv"
EMAIL_CACHE = DATA_DIR / "email_cache.csv"
LEDGER = DATA_DIR / "seen_contacts.csv"

FINAL_FILES = {"ICP": "icp.csv", "NEEDS_REVIEW": "needs_review.csv", "NON_ICP": "non_icp.csv"}


SEED_DIR = ROOT / "seed"


def ensure():
    for d in (DATA_DIR, OUT_DIR, RAW_DIR, RAW_DIR / "companies", WORK_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    seed_if_empty()


def seed_if_empty():
    """
    First boot on a fresh volume: copy the committed caches in.

    Without this the container starts with no funding evidence, so companies
    already paid for come back as NEEDS_PAID_STAGE_CHECK and either get
    re-charged or sit in needs_review. Only ever fills a MISSING file - it
    never overwrites state the running service has built up.
    """
    import shutil
    if not SEED_DIR.is_dir():
        return []
    seeded = []
    targets = {"stage_cache.csv": STAGE_CACHE,
               "email_cache.csv": EMAIL_CACHE,
               "seen_contacts.csv": LEDGER,
               "icp.csv": OUT_DIR / "icp.csv",
               "needs_review.csv": OUT_DIR / "needs_review.csv",
               "non_icp.csv": OUT_DIR / "non_icp.csv"}
    for name, dest in targets.items():
        src = SEED_DIR / name
        if src.exists() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            seeded.append(name)
    return seeded


def load_config():
    import yaml
    return yaml.safe_load(CONFIG_PATH.read_text())
