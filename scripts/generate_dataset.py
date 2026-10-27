"""
Full-scale dataset generation entry point.

Generates shared CUSTOMERS/PRODUCTS dimensions plus PRE and POST event streams
into a persisted DuckDB database file.

Usage:
    python scripts/generate_dataset.py
    python scripts/generate_dataset.py --db-path data/journeys.duckdb --sessions-per-version 300000 --overwrite
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.db import DatabaseManager
from src.generator import SyntheticDatasetGenerator


def main():
    parser = argparse.ArgumentParser(description="Generate PRE/POST synthetic datasets into a persisted DuckDB file.")
    parser.add_argument("--db-path", default="data/journeys.duckdb", help="Output DuckDB file path.")
    parser.add_argument("--customers", type=int, default=20000, help="Shared CUSTOMERS pool size.")
    parser.add_argument("--products", type=int, default=500, help="Shared PRODUCTS catalog size.")
    parser.add_argument("--sessions-per-version", type=int, default=300000,
                        help="Sessions to generate per dataset_version (~1M event rows at default scale).")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--overwrite", action="store_true", help="Delete an existing DB file before generating.")
    args = parser.parse_args()

    if os.path.exists(args.db_path):
        if not args.overwrite:
            sys.exit(f"Refusing to append to existing file {args.db_path}. Pass --overwrite to regenerate.")
        os.remove(args.db_path)

    os.makedirs(os.path.dirname(os.path.abspath(args.db_path)), exist_ok=True)

    t0 = time.time()
    db_mgr = DatabaseManager(args.db_path)
    conn = db_mgr.get_connection()
    gen = SyntheticDatasetGenerator(conn, seed=args.seed)

    print(f"Stage 1: generating shared dimensions ({args.customers:,} customers, {args.products:,} products)...")
    gen.generate_shared_dimensions(num_customers=args.customers, num_products=args.products)

    for version in ("PRE", "POST"):
        t = time.time()
        print(f"Stage 2: generating {version} event stream ({args.sessions_per_version:,} sessions)...")
        gen.generate_event_stream(dataset_version=version, total_sessions=args.sessions_per_version)
        print(f"  {version} done in {time.time() - t:.1f}s")

    counts = conn.execute("""
        SELECT 'TRANSACTION_EVENTS' AS tbl, dataset_version, COUNT(*) FROM TRANSACTION_EVENTS GROUP BY dataset_version
        UNION ALL
        SELECT 'SEARCH_FILTER_EVENTS', dataset_version, COUNT(*) FROM SEARCH_FILTER_EVENTS GROUP BY dataset_version
        UNION ALL
        SELECT 'PAGE_ENGAGEMENT_EVENTS', dataset_version, COUNT(*) FROM PAGE_ENGAGEMENT_EVENTS GROUP BY dataset_version
        ORDER BY 1, 2
    """).fetchall()
    total = conn.execute("""
        SELECT (SELECT COUNT(*) FROM TRANSACTION_EVENTS)
             + (SELECT COUNT(*) FROM SEARCH_FILTER_EVENTS)
             + (SELECT COUNT(*) FROM PAGE_ENGAGEMENT_EVENTS)
    """).fetchone()[0]

    for tbl, version, n in counts:
        print(f"  {tbl:<26} {version:<5} {n:>10,}")
    print(f"Total event rows: {total:,} in {time.time() - t0:.1f}s -> {args.db_path}")

    db_mgr.close()


if __name__ == "__main__":
    main()
