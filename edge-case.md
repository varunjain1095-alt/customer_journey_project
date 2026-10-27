# Corner Scenarios & Edge Cases: Customer Journey Analytics Dashboard

This document details all technical, analytical, data-pipeline, and UI edge cases, boundary conditions, and failure modes for the Customer Journey Analytics Dashboard, along with their explicit resolution strategies.

---

## Table of Contents
1. [Data Pipeline & Event Log Edge Cases](#1-data-pipeline--event-log-edge-cases)
2. [Process Mining & PM4Py Algorithm Edge Cases](#2-process-mining--pm4py-algorithm-edge-cases)
3. [Funnel Analysis & Segment Aggregation Edge Cases](#3-funnel-analysis--segment-aggregation-edge-cases)
4. [R Microservice & Animation Container Edge Cases](#4-r-microservice--animation-container-edge-cases)
5. [Dual-Dataset & Feature Release Comparison Edge Cases](#5-dual-dataset--feature-release-comparison-edge-cases)
6. [UI State Management & Frontend Edge Cases](#6-ui-state-management--frontend-edge-cases)

---

## 1. Data Pipeline & Event Log Edge Cases

### 1.1 Single-Event Sessions (One-Click Bounces)
- **Scenario**: A customer opens a product page (`view_product`) and immediately exits without performing any further action.
- **Risk**: Post-hoc duration computation (`LEAD(event_time)`) evaluates to `NULL` / `0` seconds.
- **Resolution Strategy**:
  - `PAGE_ENGAGEMENT_SATELLITE.time_spent_seconds` is explicitly set to `0.0` seconds for single-event sessions.
  - Process mining trace variant engine treats `[view_product]` as a valid, distinct 1-step trace (Rank 3 in baseline data: `view_product (exit)`).

### 1.2 Timestamp Collision Events (Zero-Delta Micro-Events)
- **Scenario**: Multiple micro-events occur within the same millisecond (e.g. `scroll_checkpoint` and `zoom_image` logged at the exact same $T$).
- **Risk**: Non-deterministic trace ordering across SQL queries or PM4Py mining runs; artificial zero-second loops.
- **Resolution Strategy**:
  - All event queries order by `ORDER BY event_time ASC, event_id ASC`.
  - The generator guarantees strict monotonic timestamp increments (minimum $+100\text{ms}$) between consecutive events in a session.

### 1.3 Inactivity Boundary & Long-Duration Journeys
- **Scenario**: A customer browses for 9 days, pauses, returns on Day 21 (12 days later), and completes a purchase on Day 22.
- **Risk**: Incorrect calculation of inactivity gap causing sessions across a long inactivity window to be misgrouped into a single journey.
- **Resolution Strategy**:
  - **Hard 10-Day Timeout**: The grouping algorithm strictly checks $(T_{\text{start\_new}} - T_{\text{end\_prev}}) > 10\text{ days } (864,000\text{s})$.
  - Since the gap between Day 9 ($T_{\text{end\_prev}}$) and Day 21 ($T_{\text{start\_new}}$) is **12 days**, which exceeds the 10-day ($864,000\text{s}$) threshold:
    - Day 1–9 sessions belong to `journey_id_1` (closed on Day 19 due to reaching 10 consecutive days of inactivity).
    - Day 21–22 sessions belong to `journey_id_2` (a new journey initiated on Day 21, which terminates upon `purchase` on Day 22).

### 1.4 Orphan Foreign Key References
- **Scenario**: Event tables reference a `product_id` or `user_id` missing from shared dimension tables.
- **Risk**: `INNER JOIN` drops events, corrupting path variant sequence integrity.
- **Resolution Strategy**:
  - Relational foreign key constraints prevent orphan event insertion.
  - Event log assembly uses `LEFT JOIN` with fallback default attributes (`'UNKNOWN_DEVICE'`, `'UNKNOWN_BRAND'`).

### 1.5 Scoping `journey_id` Uniqueness Across `dataset_version`
- **Scenario**: Independent event generator runs for PRE and POST datasets produce identical `journey_id` strings (e.g. `journey_00102`).
- **Risk**: Cross-version query joins merge unrelated user journeys from Dataset A and Dataset B.
- **Resolution Strategy**:
  - `journey_id` is **always queried and grouped together with `dataset_version`** as a composite grouping key: `(journey_id, dataset_version)`.
  - All SQL queries, trace mining pipelines, and funnel aggregators execute `GROUP BY journey_id, dataset_version` to guarantee strict cross-version isolation.

---

## 2. Process Mining & PM4Py Algorithm Edge Cases

### 2.1 High-Frequency Self-Loops
- **Scenario**: A user repeatedly clicks `zoom_image` 10 consecutive times within a single product view ($E \rightarrow E \rightarrow E \dots$).
- **Risk**: Directly-Follows Graph (DFG) degenerates into visual clutter; variant string length explodes.
- **Resolution Strategy**:
  - In **Collapsed View Mode**, consecutive duplicate events are collapsed into a single node annotated with repeat count (e.g. `zoom_image (x10)`).
  - In **Full Detail Mode**, self-loops are rendered as bounded curved loop edges on Layer 1.

### 2.2 Deterministic Sorting for Tied Variant Frequencies
- **Scenario**: Variant $A$ and Variant $B$ both account for exactly 5,420 sessions ($4.21\%$ of traffic).
- **Risk**: Unstable variant ranking order in Layer 2 table across page reloads.
- **Resolution Strategy**:
  - Secondary deterministic sort key applied: `ORDER BY session_count DESC, path_id ASC` (where `path_id = MD5(event_types)`).

### 2.3 Orphaned Nodes Under 60% Traffic Cover Filtering
- **Scenario**: Applying the 60% cumulative frequency filter removes low-volume edges incident to a specific event node $N_k$.
- **Risk**: Node $N_k$ sits disconnected in floating space with no incoming or outgoing arrows.
- **Resolution Strategy**:
  - The DFG rendering pipeline automatically prunes any node $N_k$ whose total degree ($\text{in\_degree} + \text{out\_degree}$) drops to $0$ under the active filter.

---

## 3. Funnel Analysis & Segment Aggregation Edge Cases

### 3.1 Out-of-Order Step Execution (Strict Sequential Violation)
- **Scenario**: Selected path is $V = [view \rightarrow add\_to\_cart \rightarrow purchase]$. User executes $[view \rightarrow review \rightarrow add\_to\_cart \rightarrow purchase]$.
- **Risk**: Non-prefix step execution miscounts conversion in strict funnels.
- **Resolution Strategy**:
  - **Strict Prefix Rule**: A session is counted at step $m$ **only if** its trace contains the exact ordered prefix $(E_1 \dots E_m)$. Interleaved un-selected events disqualify the session from strict path matching.

### 3.2 Repeated Event Names in Target Path
- **Scenario**: Selected path is $V = [view\_product \rightarrow add\_to\_cart \rightarrow view\_product \rightarrow purchase]$ (user browses a second product before buying).
- **Risk**: Step set matching ambiguitates which `view_product` step is active.
- **Resolution Strategy**:
  - Funnel engine matches by **position index in path array** ($m = 1$ vs. $m = 3$), not event string equality.

### 3.3 Zero-Session Segment Profiles (Empty Cohorts)
- **Scenario**: A PM selects a rare variant or filters by a narrow date range where 0 customer sessions exist.
- **Risk**: Division-by-zero errors in average session duration, AOV, or lifetime value calculations.
- **Resolution Strategy**:
  - Segment engine checks `IF cohort_size == 0`: returns zeroed KPI metrics (`AOV = $0.00`, `Session Time = 0s`) and renders an empty state visual banner: *"No user sessions match this path filter"*.

---

## 4. R Microservice & Animation Container Edge Cases

### 4.1 Memory Saturation During Large Variant Animations
- **Scenario**: PM clicks `"▶ animate this path"` on a variant representing 250,000 sessions.
- **Risk**: R process memory exceeds 4 GB Docker limit, causing container OOM crash or browser iFrame freezing.
- **Resolution Strategy**:
  - **Token Subsampling**: R microservice caps animated tokens at $1,000$ maximum. If session count $> 1,000$, tokens are uniformly downsampled with a visual label: *"Displaying 1,000 sampled tokens representing 250,000 sessions"*.

### 4.2 R Microservice Timeout / Container Failure
- **Scenario**: R Plumber container is restarting or unreachable.
- **Risk**: UI hangs indefinitely waiting for animation HTML payload.
- **Resolution Strategy**:
  - Python HTTP client enforces a strict **5-second request timeout**.
  - On timeout/error, Python returns a graceful fallback payload, rendering a user toast: *"Animation engine offline. Displaying static process graph."*

### 4.3 Animation Cache Key Collisions
- **Scenario**: Animation requested for identical `path_id` under different date ranges or dataset versions.
- **Risk**: Stale animation widget returned from cache.
- **Resolution Strategy**:
  - LRU cache key explicitly includes all filter parameters:
    $$\text{CacheKey} = \text{MD5}(path\_id \parallel dataset\_version \parallel start\_date \parallel end\_date)$$

---

## 5. Dual-Dataset & Feature Release Comparison Edge Cases

### 5.1 Variant Disappearance in Post-Feature Dataset
- **Scenario**: Path $V_{\text{abandon}} = [view \rightarrow add\_to\_cart \rightarrow exit]$ exists in `PRE` (Dataset A) but was completely eliminated in `POST` (Dataset B) due to UX fixes.
- **Risk**: Querying `POST` for $V_{\text{abandon}}$ returns 0 rows, causing comparison UI code to crash.
- **Resolution Strategy**:
  - Comparison query handles missing variants gracefully:
    $$\text{POST Metrics} = \{ \text{count}: 0, \text{percentage}: 0.0\%, \text{status}: \text{"Path Eliminated in Post-Feature Release"} \}$$
  - UI displays a green success banner indicating $100\%$ drop-off reduction.

### 5.2 Starred Path Across Version Switch
- **Scenario**: PM stars a path while viewing `PRE` mode, then switches UI selector to `POST` mode.
- **Risk**: UI attempts to look up `STARRED_PATHS` using wrong version context.
- **Resolution Strategy**:
  - `STARRED_PATHS` table uses composite PK `(pm_user_id, path_id, dataset_version)`.
  - UI checks star status against active `dataset_version`. Star icons indicate version provenance badges (`PRE Starred` vs. `POST Starred`).

### 5.3 Zipf Popularity Seed Tie Resolution
- **Scenario**: Two products in `PRODUCTS` receive identical `popularity_seed` floats during Stage 1 generator seeding.
- **Risk**: Non-deterministic search result ordering during generator runs.
- **Resolution Strategy**:
  - Search sorting enforces deterministic tie-breaking: `ORDER BY popularity_seed DESC, product_id ASC`.

### 5.4 Cross-Version Journey Isolation
- **Scenario**: Comparing journey metrics across PRE and POST runs.
- **Risk**: Merging sessions across datasets due to matching `journey_id` strings.
- **Resolution Strategy**:
  - All queries enforce composite grouping on `(journey_id, dataset_version)` ensuring PRE and POST journeys remain completely isolated.

---

## 6. UI State Management & Frontend Edge Cases

### 6.1 Multi-Path Selector Over-Constraint
- **Scenario**: User attempts to select a 5th path for comparison when the limit is 4.
- **Risk**: Messy side-by-side dashboard layout overlap.
- **Resolution Strategy**:
  - Multi-path selection UI control hard-enforces selection limit $N \in \{2, 3, 4\}$.
  - Attempting to select a 5th path disables further checkbox selections and displays an inline warning: *"Maximum 4 paths can be compared side-by-side"*.

### 6.2 Rapid Toggle Switching (Race Conditions)
- **Scenario**: User rapidly clicks between `PRE`, `POST`, and `COMPARISON` dataset toggle buttons before prior API fetches complete.
- **Risk**: Out-of-order API response resolves late, rendering wrong dataset metrics.
- **Resolution Strategy**:
  - Frontend utilizes `AbortController` to cancel pending API HTTP requests upon new dataset/path selections.
  - UI state updates only from the latest request promise sequence.
