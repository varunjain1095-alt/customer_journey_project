# Customer Journey Analytics Dashboard

## Setup
```
pip install -r requirements.txt
```

## Tests
```
python -m pytest tests/
```

## Full dataset generation (persisted DuckDB)
```
python scripts/generate_dataset.py --overwrite
```
Default output: `data/journeys.duckdb` (~1M event rows per dataset_version).

## Pre-render animations (static/Railway deploy)
```
cd r && docker compose up -d   # R animation service must be running
python scripts/precompute_animations.py [--top-n 10] [--overwrite]
```
Writes `frontend/public/animations/{VERSION}_{path_id}.html` (genuine processanimateR
output); Vite copies it into `dist/`. The play button serves the static file first
and falls back to the live `/api/animate` proxy.

## Structure
- `src/db.py` — DuckDB schema (9 tables, `dataset_version`-tagged)
- `src/generator.py` — 2-stage synthetic generator (shared dims, per-version events)
- `src/analytics.py` — PM4Py variant mining, DFG (60% filter), strict funnel, segment profiling
- `src/utils.py` — `compute_path_id` (MD5), Pareto click sampling
- `tests/` — Phase 1 (`test_generator.py`) + Phase 2 (`test_analytics.py`) suites
- `implementation_plan.md` — 5-phase plan (Phase 1 complete; Phases 2–5 pending)

## Decisions from user Q&A — do not re-litigate

Recorded so this is never asked or re-decided again:

- **PM4Py stays.** Explicitly required (Decision 2). Do not propose removing/replacing it. Known footgun: `pm4py.get_variants` pandas path assumes contiguous cases (its internal `sort_values` is discarded) — `assemble_event_log` pre-sorts by case/timestamp/event_id. Perf measured: polars backend ~2.5s on ~1.7M rows.
- **1.5s variant benchmark: user does not care.** Not a gate.
- **Frontend stack: FastAPI + React + React Flow + ECharts** (user chose option B). Build: `cd frontend && npm run build`. Serve: `uvicorn --factory src.server:create_app --port 8001`.
- **Synthetic data design decisions** (made during segment/funnel/comparison fixes):
  - Cohorts are attribute-conditioned on device_type, traffic_source, membership_status, country, gender (`_cohort_weights`) so segment profiles differentiate.
  - Sessions can exit mid-path (progressive bail probabilities) so funnels taper.
  - **POST = guest checkout feature release**: ~55% of converting POST sessions skip `login`→`purchase`. This is what makes PRE vs POST comparisons meaningful and produces new/eliminated variants.
  - Segment profiling groups at `user_session` granularity and weights users by matching-trace count (not DISTINCT users) — must match the variant/funnel granularity.
- **Diagram coverage**: UI default is "All Edges" (coverage=1.0); API default stays 0.6 per spec. Selected path is always overlaid on the filtered graph.
- **Server caching**: signature tables, variant sets, DFGs, segment results are cached per version; background warmup at startup (~60s). `/api/health` reports `warm`.

## Ground rules from user

- Do NOT run commands without permission — describe and wait.
- Visual/UI changes must be verified visually before being called done.
- Never silently drop or swap a spec'd library or architectural decision; surface findings instead.
