# Project Context: Customer Journey Analytics Dashboard

This document represents the comprehensive, unabridged source of truth for the project context, design decisions, event schema, dashboard architecture, synthetic data rules, and technical boundaries. It is directly constructed from the complete decisions log (`decisions.md`).

---

## Table of Contents
1. [Core Architectural Philosophy](#1-core-architectural-philosophy)
2. [Decision 1: Self-Designed Event Schema (9-Table Fact/Satellite Model)](#decision-1-self-designed-event-schema-9-table-factsatellite-model)
3. [Decision 2: Two-Layer Dashboard Architecture (PM4Py Integration)](#decision-2-two-layer-dashboard-architecture-pm4py-integration)
4. [Decision 3: Interactive Linking Between Diagram & Variant Table](#decision-3-interactive-linking-between-diagram--variant-table)
5. [Decision 4: Animated Process Replay via bupaR & processanimateR](#decision-4-animated-process-replay-via-bupar--processanimater)
6. [Decision 5: Contextual Animation Trigger & Congestion Engineering](#decision-5-contextual-animation-trigger--congestion-engineering)
7. [Decision 6: Path-Specific Sequential Funnel Analysis](#decision-6-path-specific-sequential-funnel-analysis)
8. [Decision 7: Multi-Path Funnel Comparison (2–4 Paths Side-by-Side)](#decision-7-multi-path-funnel-comparison-24-paths-side-by-side)
9. [Decision 8: Path-Specific Segment Profile Panel](#decision-8-path-specific-segment-profile-panel)
10. [Decision 9: Independent Segment Profile Comparison](#decision-9-independent-segment-profile-comparison)
11. [Decision 10: Starred / Favorite Paths Bookmark System](#decision-10-starred--favorite-paths-bookmark-system)
12. [Decision 11: Process Diagram Complexity Controls at Scale](#decision-11-process-diagram-complexity-controls-at-scale)
13. [Decision 12: Feature-Release Comparison via Dual Synthetic Datasets](#decision-12-feature-release-comparison-via-dual-synthetic-datasets)
14. [Decision 13: Synthetic Dataset Generation Specifications](#decision-13-synthetic-dataset-generation-specifications)
15. [Decision 14: Mandatory Login Requirement (No Guest Checkout)](#decision-14-mandatory-login-requirement-no-guest-checkout)
16. [Decision 15: Explicit Scope Boundaries](#decision-15-explicit-scope-boundaries)

---

## 1. Core Architectural Philosophy
The objective of this project is to build an interactive, production-grade Customer Journey Analytics Dashboard designed for Product Managers (PMs). The system bridges process mining techniques (`PM4Py` in Python and `bupaR`/`processanimateR` in R) with e-commerce analytics to extract high-resolution behavioral insights, highlight bottlenecks, analyze conversion drop-offs, and compare customer segment profiles across dataset versions.

---

## Decision 1: Self-Designed Event Schema (9-Table Fact/Satellite Model)

### Fact Table + Satellite Table Architecture
Every event table, satellite table, and event-derived summary table carries a `dataset_version` column (`'PRE'` | `'POST'`). To guarantee cross-version journey isolation, `journey_id` is scoped by combining it with `dataset_version` as a composite grouping key `(journey_id, dataset_version)`:
- **Shared Dimension Tables**: `CUSTOMERS` and `PRODUCTS` (generated once).
- **Derived Summary Table**: `BEHAVIORAL_METRICS` (generated per `dataset_version`, PK: `user_id, dataset_version`).
- **Core Event & Satellite Tables**: Partitioned/tagged with `dataset_version` and grouped by `(journey_id, dataset_version)`.

---

## 5. Synthetic Dataset Generation Rules
- **Shared Dimension Universe**: `CUSTOMERS` and `PRODUCTS` generated ONCE and shared.
- **Journey Boundaries & Assignment Rule**:
  - A journey groups multi-session arcs for a `(user_id, dataset_version)` pair.
  - A journey closes upon a completed `purchase` event OR after **10 consecutive days** of inactivity ($864,000\text{ seconds}$).
  - Example: Browsing on Day 1–9, pausing until Day 21 (12-day gap $> 10$ days) creates `journey_id_1` (closed on Day 19 due to timeout) and `journey_id_2` (new journey starting Day 21, closed on Day 22 upon purchase).
- **Mandatory Login**: E-commerce checkouts require a `login` (no guest checkout).
- **Search Rankings (Best-Selling First via `popularity_seed`)**:
  - Search results sort candidate products by `PRODUCTS.popularity_seed DESC` (pre-generated Zipf seed).
  - `result_position_clicked` is sampled from a Pareto distribution (80% of clicks target positions 1–3).
  - `total_units_sold` is derived post-hoc after generation.
- **Deterministic `path_id` Formula**: `path_id = MD5("E_1|E_2|...|E_k")` with no run-specific salt or timestamp.

---

## 6. Scope Boundaries
Out-of-scope boundaries per Decision 15.
