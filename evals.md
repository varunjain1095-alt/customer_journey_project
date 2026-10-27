# System Evaluation & Verification Suite (`evals.md`)

This document specifies the verification framework, evaluation criteria, test scenarios, expected behaviors, and quantitative Pass/Fail criteria for each phase of the Customer Journey Analytics Dashboard implementation.

---

## Table of Contents
1. [Phase 1 Evals: Shared Database Schema & Synthetic Generator Engine](#1-phase-1-evals-shared-database-schema--synthetic-generator-engine)
2. [Phase 2 Evals: Python Process Mining & Core Analytics Subsystem](#2-phase-2-evals-python-process-mining--core-analytics-subsystem)
3. [Phase 3 Evals: R Microservice & Animation Engine](#3-phase-3-evals-r-microservice--animation-engine)
4. [Phase 4 Evals: Frontend UI Dashboard Layout & Interactive Visualizations](#4-phase-4-evals-frontend-ui-dashboard-layout--interactive-visualizations)
5. [Phase 5 Evals: Dual-Dataset Comparison & Performance Benchmarks](#5-phase-5-evals-dual-dataset-comparison--performance-benchmarks)

---

## 1. Phase 1 Evals: Shared Database Schema & Synthetic Generator Engine

### Goal
Verify database schema setup, shared dimension consistency across PRE and POST datasets, 10-day inactivity journey assignment, search ranking Zipf distributions, satellite table population, per-version `BEHAVIORAL_METRICS` calculation, and cross-version `journey_id` scoping.

---

### Test Scenario 1.1: Relational Schema Integrity & Partitioning
- **Test Objective**: Verify all 9 database tables are created with exact column definitions, primary/foreign keys, and `dataset_version` (`'PRE'` | `'POST'`) tags.
- **Execution Command**: `pytest tests/test_generator.py::test_schema_integrity`
- **Expected Behavior**: DuckDB initializes 9 tables: `CUSTOMERS`, `BEHAVIORAL_METRICS`, `PRODUCTS`, `TRANSACTION_EVENTS`, `SEARCH_FILTER_EVENTS`, `PAGE_ENGAGEMENT_EVENTS`, `SEARCH_SATELLITE`, `PAGE_ENGAGEMENT_SATELLITE`, `STARRED_PATHS`.
- **Pass Criteria**:
  - `duckdb.SHOW_TABLES()` returns exactly 9 tables.
  - `TRANSACTION_EVENTS`, `SEARCH_FILTER_EVENTS`, `PAGE_ENGAGEMENT_EVENTS`, `SEARCH_SATELLITE`, `PAGE_ENGAGEMENT_SATELLITE`, `BEHAVIORAL_METRICS`, and `STARRED_PATHS` contain a `dataset_version` column.
  - Foreign key relations enforce valid references.
- **Fail Criteria**: Missing tables, missing `dataset_version` columns, or schema type mismatches.

---

### Test Scenario 1.2: Shared Upstream Dimension Consistency
- **Test Objective**: Verify `CUSTOMERS` and `PRODUCTS` are generated **ONCE** and referenced identically by both PRE and POST event streams.
- **Execution Command**: `pytest tests/test_generator.py::test_shared_dimension_consistency`
- **Expected Behavior**: Pre-Feature (Dataset A) and Post-Feature (Dataset B) event streams reference the exact same `user_id` and `product_id` catalog with zero missing keys and 100% identical ID sets.
- **Pass Criteria**:
  - $\text{SET}(\text{DISTINCT } user\_id \text{ WHERE } dataset\_version=\text{'PRE'}) == \text{SET}(\text{DISTINCT } user\_id \text{ WHERE } dataset\_version=\text{'POST'})$ across `TRANSACTION_EVENTS` (and equals `CUSTOMERS.user_id`).
  - $\text{SET}(\text{DISTINCT } product\_id \text{ WHERE } dataset\_version=\text{'PRE'}) == \text{SET}(\text{DISTINCT } product\_id \text{ WHERE } dataset\_version=\text{'POST'})$ across event tables (and equals `PRODUCTS.product_id`).
  - `PRODUCTS.popularity_seed` values for all products are 100% identical across PRE and POST runs.
- **Fail Criteria**: Any `user_id` or `product_id` present in event tables missing from `CUSTOMERS`/`PRODUCTS`, PRE and POST referencing non-identical `user_id` or `product_id` sets, or differing `popularity_seed` values.

---

### Test Scenario 1.3: Journey ID Grouping & 10-Day Timeout Logic
- **Test Objective**: Validate that session grouping into `journey_id` adheres strictly to the 10-day ($864,000\text{s}$) inactivity window and purchase termination rules per `(user_id, dataset_version)`.
- **Execution Command**: `pytest tests/test_generator.py::test_journey_id_assignment`
- **Setup Cases**:
  - *Case A (Within 10 Days)*: Session 1 on Day 1, Session 2 on Day 5 (gap = 4 days $\le 10$ days, no purchase).
  - *Case B (Exceeding 10 Days)*: Session 1 on Day 1–9, Session 2 on Day 21 (gap = 12 days $> 10$ days).
  - *Case C (Purchase Termination)*: Session 1 ends in `purchase` on Day 3; Session 2 starts on Day 4.
- **Expected Behavior**:
  - *Case A*: Session 1 and Session 2 share the same `journey_id`.
  - *Case B*: Session 1 belongs to `journey_id_1`; Session 2 initiates `journey_id_2`.
  - *Case C*: Session 1 belongs to `journey_id_1`; Session 2 initiates `journey_id_2`.
- **Pass Criteria**:
  - Case A: `journey_id(Session 1) == journey_id(Session 2)`.
  - Case B: `journey_id(Session 1) != journey_id(Session 2)`.
  - Case C: `journey_id(Session 1) != journey_id(Session 2)`.
- **Fail Criteria**: Sessions separated by $> 10$ days or occurring after a `purchase` sharing a `journey_id`.

---

### Test Scenario 1.4: Deterministic `path_id` MD5 Hashing
- **Test Objective**: Verify `path_id = MD5(event_types)` generates identical string hashes for identical trace signatures across PRE and POST datasets without run-specific salt.
- **Execution Command**: `pytest tests/test_generator.py::test_deterministic_path_hash`
- **Expected Behavior**: A trace `view_product -> add_to_cart -> purchase` computes MD5 string `b5a79f32c18d42e9...` in both PRE and POST generator runs.
- **Pass Criteria**:
  - `path_id("view_product|add_to_cart|purchase", dataset="PRE") == path_id("view_product|add_to_cart|purchase", dataset="POST")`.
- **Fail Criteria**: Hash outputs differ between PRE and POST for identical trace signatures.

---

### Test Scenario 1.5: Satellite & Derived Table Population
- **Test Objective**: Verify satellite tables and versioned `BEHAVIORAL_METRICS` are populated in storage during generation.
- **Execution Command**: `pytest tests/test_generator.py::test_satellite_tables_populated`
- **Expected Behavior**:
  - `SEARCH_SATELLITE.results_returned` is populated with integer candidate counts ($> 0$).
  - `PAGE_ENGAGEMENT_SATELLITE.time_spent_seconds` is precomputed via `LEAD(event_time)`.
  - `BEHAVIORAL_METRICS` contains rows for `(user_id, dataset_version)` with differing metrics between PRE and POST for the same user.
- **Pass Criteria**:
  - `SELECT COUNT(*) FROM SEARCH_SATELLITE WHERE results_returned IS NULL` equals `0`.
  - `SELECT COUNT(*) FROM PAGE_ENGAGEMENT_SATELLITE WHERE time_spent_seconds IS NULL` equals `0`.
  - `BEHAVIORAL_METRICS` has rows for both `'PRE'` and `'POST'`; metrics (e.g. `cart_abandonment_rate`) differ between versions.
- **Fail Criteria**: NULL values in satellite tables or identical `BEHAVIORAL_METRICS` outputs across PRE and POST.

---

### Test Scenario 1.6: Journey ID Uniqueness & Scoping Across Dataset Versions
- **Test Objective**: Verify `journey_id` values are correctly scoped/namespaced with `dataset_version` as a composite key `(journey_id, dataset_version)` such that no `journey_id` collides across dataset versions without composite disambiguation, and journey grouping logic is evaluated independently within each version.
- **Execution Command**: `pytest tests/test_generator.py::test_journey_id_version_scoping`
- **Setup**: Generate event logs independently for PRE and POST runs with shared `user_id` pools and similar timestamp distributions.
- **Expected Behavior**: Queries and analytics grouping by `(journey_id, dataset_version)` strictly isolate PRE journeys from POST journeys, even if identical string tokens exist across runs.
- **Pass Criteria**:
  - No `journey_id` value exists identically across PRE and POST referring to unrelated journeys UNLESS explicitly joined/queried together with `dataset_version` as a composite key `(journey_id, dataset_version)`.
  - Journey grouping logic (10-day inactivity timeout, purchase termination) produces correct groupings independently within PRE and within POST, completely unaffected by the other version's event timeline.
- **Fail Criteria**: A `journey_id` collision across `dataset_version` that is not properly disambiguated by the composite key `(journey_id, dataset_version)`, or journey grouping logic in one version incorrectly influenced by events in the other version.

---

## 2. Phase 2 Evals: Python Process Mining & Core Analytics Subsystem

### Goal
Evaluate `PM4Py` trace variant extraction, 60% traffic coverage DFG filtering, strict sequential funnel math, and segment profile aggregations filtering by `dataset_version`.

---

### Test Scenario 2.1: Trace Variant Mining & Frequency Ranking
- **Test Objective**: Verify variant engine extracts trace signatures, ranks variants by session count/%, and aggregates long-tail variants into `Other`.
- **Execution Command**: `pytest tests/test_analytics.py::test_pm4py_variant_mining`
- **Inputs**: 1,000,000 event rows for `dataset_version = 'PRE'`. Set Top N cutoff slider to 10.
- **Expected Behavior**: Extracts unique variants ranked 1 to 10, with remaining variants bucketed into `Other (X variants, Y% of sessions)`.
- **Pass Criteria**:
  - Sum of percentages across Top 10 variants + `Other` row equals $100.0\% \pm 0.01\%$.
  - Rank 1 variant count $\ge$ Rank 2 variant count $\ge \dots \ge$ Rank 10 variant count.
- **Fail Criteria**: Percentage sum $\ne 100\%$, incorrect rank order, or missing `Other` aggregation.

---

### Test Scenario 2.2: 60% Traffic Coverage DFG Filter & Node Pruning
- **Test Objective**: Validate that the DFG builder filters transitions covering the top 60% cumulative traffic volume and prunes orphaned 0-degree nodes.
- **Execution Command**: `pytest tests/test_analytics.py::test_dfg_60_percent_filter`
- **Expected Behavior**: Edge set is filtered to top 60% cumulative volume. Nodes with 0 incident edges after filtering are excluded from graph render JSON.
- **Pass Criteria**:
  - $\sum \text{EdgeVolume}(\text{filtered}) \le 0.60 \times \text{TotalVolume}$.
  - All nodes in DFG output JSON have $\text{degree} \ge 1$ (zero floating orphan nodes).
- **Fail Criteria**: Edge volume $> 60\%$, or orphan nodes present in graph JSON.

---

### Test Scenario 2.3: Strict Sequential Funnel Engine Math
- **Test Objective**: Evaluate funnel step conversion and drop-off % calculations using strict ordered prefix matching.
- **Execution Command**: `pytest tests/test_analytics.py::test_strict_funnel_math`
- **Target Path**: $V = [view\_product \rightarrow add\_to\_cart \rightarrow purchase]$.
- **Expected Behavior**: Step 1 counts sessions starting with `view_product`; Step 2 counts sessions starting with `view_product -> add_to_cart`; Step 3 counts completed purchase traces.
- **Pass Criteria**:
  - $\text{Step 1 Count} \ge \text{Step 2 Count} \ge \text{Step 3 Count}$.
  - Step $m$ conversion $\% = (\text{Count}_m / \text{Count}_1) \times 100\%$.
  - Step $m$ drop-off $\% = 100\% - (\text{Count}_m / \text{Count}_{m-1} \times 100\%)$.
- **Fail Criteria**: Out-of-order count increase ($\text{Count}_2 > \text{Count}_1$) or drop-off calculation errors.

---

### Test Scenario 2.4: Segment Profile Aggregation by `dataset_version`
- **Test Objective**: Verify demographic and behavioral metrics aggregation for selected path cohorts filtering by `dataset_version`.
- **Execution Command**: `pytest tests/test_analytics.py::test_segment_profile_aggregation`
- **Expected Behavior**: Joins path user cohort against shared `CUSTOMERS` (`device_type`, `traffic_source`, region) and versioned `BEHAVIORAL_METRICS`.
- **Pass Criteria**:
  - Demographic distribution percentages sum to $100.0\%$.
  - Queries filter strictly by `WHERE dataset_version = :active_version`.
- **Fail Criteria**: Distribution sums $\ne 100\%$ or un-filtered cross-version metric contamination.

---

## 3. Phase 3 Evals: R Microservice & Animation Engine

### Goal
Evaluate R Plumber REST microservice endpoint (`POST /animate`), token downsampling, token visual highlighting, and Python LRU caching performance.

---

### Test Scenario 3.1: Plumber REST API Endpoint (`POST /animate`)
- **Test Objective**: Verify R Plumber microservice receives payload, converts to `bupaR` eventlog, and returns an interactive `htmlwidgets` bundle.
- **Execution Command**: `pytest tests/test_r_service.py::test_r_microservice_health`
- **Input Payload**: `{ "path_id": "b5a79f32...", "dataset_version": "PRE", "events": [...] }`.
- **Expected Behavior**: Returns HTTP status 200 OK with HTML string payload containing `processanimateR` script tags.
- **Pass Criteria**:
  - HTTP Status Code == 200.
  - Response payload contains `<!DOCTYPE html>` and `processanimateR` widget dependencies.
- **Fail Criteria**: HTTP 500 error, malformed HTML payload, or Plumber crash.

---

### Test Scenario 3.2: Animation Token Subsampling & Highlighting
- **Test Objective**: Verify R microservice caps animated tokens at $1,000$ max and highlights target path tokens against grey background traffic.
- **Execution Command**: `pytest tests/test_r_service.py::test_animation_rendering`
- **Input**: Event log containing 40,000 sessions for target `path_id`.
- **Expected Behavior**: R container uniformly subsamples animated tokens to $1,000$ max and assigns vibrant highlight CSS styling to target path tokens.
- **Pass Criteria**:
  - Total token DOM elements in widget HTML $\le 1,000$.
  - Target path tokens carry visual highlight attributes (`color != #888888`).
- **Fail Criteria**: Token count $> 1,000$ or target path tokens un-highlighted.

---

### Test Scenario 3.3: LRU Cache Performance & Timeout Fallback
- **Test Objective**: Evaluate Python LRU cache response speed and fallback behavior when R container is unreachable.
- **Execution Command**: `pytest tests/test_r_service.py::test_animation_cache_hit`
- **Scenario A (Cache Hit)**: Execute repeated `/animate` call with identical `MD5(path_id + dataset_version + date_range)` cache key.
- **Scenario B (Container Timeout)**: Simulate 5-second R microservice timeout.
- **Pass Criteria**:
  - Scenario A: Response latency $< 50\text{ms}$.
  - Scenario B: Python client catches timeout after $5\text{s}$ and returns graceful static graph fallback JSON.
- **Fail Criteria**: Cache hit latency $\ge 50\text{ms}$ or unhandled connection exception on container offline.

---

## 4. Phase 4 Evals: Frontend UI Dashboard Layout & Interactive Visualizations

### Goal
Verify frontend UI state reactivity, Layer 1 diagram highlighting on Layer 2 row clicks, 2–4 path multi-funnel comparison picker constraints, and granularity toggling.

---

### Test Scenario 4.1: Reactive State Node Highlighting
- **Test Objective**: Verify clicking a variant row in Layer 2 table re-renders Layer 1 process diagram highlighting target path nodes/edges and dimming non-selected branches.
- **Execution Action**: Click Rank 2 variant row in Layer 2 table.
- **Expected Behavior**: Layer 1 process graph updates highlight color on Rank 2 path; non-selected nodes/edges opacity dims to $\le 0.2$; Funnel & Segment panels refresh.
- **Pass Criteria**:
  - Active path nodes in Layer 1 diagram carry highlighted class.
  - Dimmed edges have CSS opacity $\le 0.2$.
- **Fail Criteria**: Diagram fails to re-render, wrong path highlighted, or non-selected edges remain fully opaque.

---

### Test Scenario 4.2: Multi-Path Comparison Picker Constraint (2–4 Paths)
- **Test Objective**: Verify multi-path picker constrains user selections to 2, 3, or 4 paths side-by-side.
- **Execution Action**: Check 2 paths, then 3, then 4, then attempt 5th path checkbox.
- **Expected Behavior**: Dashboard renders 2, 3, or 4 side-by-side funnel/segment panels. 5th path click is blocked with warning toast: *"Maximum 4 paths can be compared side-by-side"*.
- **Pass Criteria**:
  - Selection count strictly constrained to $N \in \{2, 3, 4\}$.
  - 5th path selection attempt yields disabled state and visual warning.
- **Fail Criteria**: Dashboard allows 5+ path selections or crashes layout.

---

### Test Scenario 4.3: Granularity Toggle & Collapse Transformer
- **Test Objective**: Verify toggling between `Full Detail` and `Collapsed` updates Layer 1 node network.
- **Execution Action**: Switch granularity control from `Full Detail` to `Collapsed`.
- **Expected Behavior**: Micro-events (`zoom_image`, `expand_description`) collapse into a single milestone node `engaged_with_details`.
- **Pass Criteria**:
  - Node count in Layer 1 graph decreases.
  - `engaged_with_details` node appears in render tree.
- **Fail Criteria**: Node network fails to update or micro-event nodes remain un-collapsed.

---

## 5. Phase 5 Evals: Dual-Dataset Comparison & Performance Benchmarks

### Goal
Evaluate dual-dataset pre/post feature comparison workflows in `COMPARISON` mode, starred path provenance, and system-wide latency benchmarks under 1,000,000 row loads.

---

### Test Scenario 5.1: Dual-Dataset Comparison Mode (`activeDatasetVersion = 'COMPARISON'`)
- **Test Objective**: Verify selecting `COMPARISON` mode triggers concurrent API queries for `PRE` and `POST` datasets using deterministic `path_id`, rendering pre-vs-post conversion deltas.
- **Execution Action**: Select a starred path and switch global header selector to `COMPARISON`.
- **Expected Behavior**: UI executes `fetchPathMetrics(path_id, dataset='PRE')` and `fetchPathMetrics(path_id, dataset='POST')` concurrently, displaying side-by-side conversion delta bars.
- **Pass Criteria**:
  - Both API queries return status 200 OK.
  - Funnel and Segment panels render `PRE` vs `POST` comparison deltas (e.g., $+15\%$ conversion shift).
  - Missing path in `POST` renders *"Path Eliminated in Post-Feature Release"* success indicator.
- **Fail Criteria**: Query failure, incorrect delta calculations, or UI crash on missing POST variants.

---

### Test Scenario 5.2: Starred Paths Version Provenance
- **Test Objective**: Verify starring a path logs `STARRED_PATHS(pm_user_id, path_id, dataset_version, starred_at)` and respects version filtering.
- **Execution Action**: Star a path in `PRE` mode, then toggle UI selector to `POST` mode.
- **Expected Behavior**: Database persists starred path with `dataset_version = 'PRE'`. Switching UI to `POST` mode displays version provenance badge (`PRE Starred`).
- **Pass Criteria**:
  - `SELECT * FROM STARRED_PATHS WHERE dataset_version = 'PRE'` returns saved record.
  - Starred path badge in UI indicates origin version (`PRE`).
- **Fail Criteria**: Missing `dataset_version` tag or cross-version star indicator corruption.

---

### Test Scenario 5.3: End-to-End Latency & Performance Benchmarks
- **Test Objective**: Validate system performance targets under 1,000,000 event row loads per dataset.
- **Execution Command**: `pytest tests/test_e2e.py::test_performance_benchmarks`
- **Benchmark Suite & Quantitative Pass Criteria**:

| Metric | Measurement Target | Pass Threshold | Fail Threshold |
|---|---|---|---|
| **PM4Py Variant Calculation** | 1,000,000 event rows | $< 1.50 \text{ seconds}$ | $\ge 1.50 \text{ seconds}$ |
| **Layer 1 Diagram Highlight Update** | Graph re-render | $< 300 \text{ ms}$ | $\ge 300 \text{ ms}$ |
| **Animation Cache Hit Retrieval** | LRU cache lookup | $< 50 \text{ ms}$ | $\ge 50 \text{ ms}$ |
| **R Microservice Batch Render** | 1,000 tokens | $< 5.00 \text{ seconds}$ | $\ge 5.00 \text{ seconds}$ |

- **Pass Criteria**: All 4 performance benchmark metrics meet their Pass Threshold under load tests.
- **Fail Criteria**: Any benchmark metric exceeds its Fail Threshold.
