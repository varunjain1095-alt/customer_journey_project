"""
Pre-render the combined process animation into static HTML for the
static/Railway deployment.

Renders one animation per dataset_version through the R Plumber animation
service (processanimateR) and saves each htmlwidgets page to
frontend/public/animations/{VERSION}.html. Vite copies public/ into dist/, so
the replay loads with no live backend.

Requires the R container running:  cd r && docker compose up -d

Usage:
    python scripts/precompute_animations.py [--overwrite]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.animation_client import AnimationClient
from src.db import DatabaseManager

DB_PATH = os.environ.get("CJ_DB_PATH", "data/journeys.duckdb")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "public", "animations")
VERSIONS = ("PRE", "POST")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-path", default=DB_PATH)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--animate-url", default=None, help="R service base URL (default http://localhost:8000)")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    conn = DatabaseManager(args.db_path).get_connection()
    # Real renders take tens of seconds (htmlwidget inlining) — far longer than
    # the interactive proxy's 5s timeout.
    client = AnimationClient(timeout=180,
                             **({"base_url": args.animate_url} if args.animate_url else {}))
    os.makedirs(args.out_dir, exist_ok=True)

    failures = 0
    for version in VERSIONS:
        dest = os.path.join(args.out_dir, f"{version}.html")
        if os.path.exists(dest) and not args.overwrite:
            print(f"{version}: skip (exists)")
            continue
        res = client.animate(conn, version)
        if res["status"] != "ok":
            print(f"{version}: FAILED — {res.get('message', 'no html')}")
            print("       Is the R container up?  cd r && docker compose up -d")
            failures += 1
            continue
        with open(dest, "w", encoding="utf-8") as f:
            f.write(res["html"])
        print(f"{version}: wrote {os.path.basename(dest)} "
              f"({res['token_count']} tokens, sampled={res['sampled']})")

    print("done" if failures == 0 else f"done with {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
