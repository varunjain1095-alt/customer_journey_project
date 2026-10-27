"""
Core Analytics & Process Mining Subsystem (Phase 2).

Event log assembly runs in DuckDB (filtered by dataset_version); process mining
(variants, DFG) runs in PM4Py on the assembled log. Traces are grouped by the
composite key (case_id, dataset_version) where case_id is journey_id by default.
"""
from collections import defaultdict

import pandas as pd
from duckdb import DuckDBPyConnection

from src.utils import compute_path_id

# Fact tables contributing events to the unified log, with a normalized session column.
_EVENT_LOG_SQL = """
SELECT e.user_id, e.session_id, e.product_id, e.event_type,
       e.event_time, e.event_id, e.journey_id, e.dataset_version,
       COALESCE(c.device_type, 'UNKNOWN_DEVICE') AS device_type,
       COALESCE(c.traffic_source, 'UNKNOWN_TRAFFIC') AS traffic_source,
       COALESCE(p.brand, 'UNKNOWN_BRAND') AS brand
FROM (
    SELECT user_id, user_session AS session_id, product_id, event_type, event_time, event_id, journey_id, dataset_version
    FROM TRANSACTION_EVENTS WHERE dataset_version = ?
    UNION ALL
    SELECT user_id, user_session, NULL, event_type, event_time, event_id, journey_id, dataset_version
    FROM SEARCH_FILTER_EVENTS WHERE dataset_version = ?
    UNION ALL
    SELECT user_id, session_id, product_id, event_type, event_time, event_id, journey_id, dataset_version
    FROM PAGE_ENGAGEMENT_EVENTS WHERE dataset_version = ?
) e
LEFT JOIN CUSTOMERS c ON e.user_id = c.user_id
LEFT JOIN PRODUCTS p ON e.product_id = p.product_id
ORDER BY event_time ASC, event_id ASC
"""

_CASE_COLUMNS = {"journey_id": "journey_id", "user_session": "session_id"}

# Granularity "Collapsed" view (Decision 11): micro-engagement events merge into
# one milestone node; view_product stays the anchor.
COLLAPSED_NODE = "engaged_with_details"
MICRO_EVENTS = {
    "view_reviews", "expand_description", "zoom_image", "check_contents",
    "watch_video", "swipe_pictures", "expand_product_features", "view_comparison",
}


def collapse_event_type(event_type: str) -> str:
    return COLLAPSED_NODE if event_type in MICRO_EVENTS else event_type


def _pm4py():
    """Lazy import: PM4Py's first import is heavy, keep module import cheap."""
    import pm4py
    return pm4py


def assemble_event_log(conn: DuckDBPyConnection, dataset_version: str, case_id: str = "journey_id") -> pd.DataFrame:
    """
    Assembles the PM4Py-formatted event log for one dataset_version.
    case_id: 'journey_id' (default) or 'user_session': the trace grouping key.
    Returns a DataFrame with PM4Py standard columns:
      case:concept:name, concept:name, time:timestamp (+ backbone fields).
    """
    df = conn.execute(_EVENT_LOG_SQL, [dataset_version, dataset_version, dataset_version]).fetchdf()

    df["time:timestamp"] = pd.to_datetime(df["event_time"])
    df["concept:name"] = df["event_type"].astype(str)
    df["case:concept:name"] = df[_CASE_COLUMNS.get(case_id, case_id)].astype(str)
    # PM4Py's numpy-based algorithms require each case's rows to be contiguous
    # (its internal sort_values call drops the result); sort by case + intra-case order.
    df = df.sort_values(["case:concept:name", "time:timestamp", "event_id"]).reset_index(drop=True)
    return df


def assemble_event_log_lazy(conn: DuckDBPyConnection, dataset_version: str, case_id: str = "journey_id"):
    """
    Polars LazyFrame variant of assemble_event_log for the hot path
    (variant extraction on 1M-row datasets).
    """
    import polars as pl
    lf = conn.execute(_EVENT_LOG_SQL, [dataset_version, dataset_version, dataset_version]).pl().lazy()
    case_col = _CASE_COLUMNS.get(case_id, case_id)
    return (
        lf.with_columns(
            pl.col("event_type").cast(pl.String).alias("concept:name"),
            pl.col(case_col).cast(pl.String).alias("case:concept:name"),
            pl.col("event_time").alias("time:timestamp"),
        )
        .sort(["case:concept:name", "time:timestamp", "event_id"])
    )


def get_variants(event_log) -> dict:
    """
    PM4Py variant extraction. Returns {variant_tuple: session_count}.
    Accepts a pandas DataFrame or a polars frame; polars input is converted
    to pandas since this PM4Py version's polars dispatch is broken
    (LazyFrame: not iterable; eager: hits pandas-only .isnull() checks).
    """
    pm4py = _pm4py()
    import polars as pl
    if isinstance(event_log, (pl.LazyFrame, pl.DataFrame)):
        event_log = event_log.collect().to_pandas() if isinstance(event_log, pl.LazyFrame) \
            else event_log.to_pandas()
    raw = pm4py.get_variants(
        event_log[["case:concept:name", "concept:name", "time:timestamp"]])
    # PM4Py returns {variant: count} or {variant: [trace_indices]} depending on version
    return {
        tuple(k): (v if isinstance(v, int) else len(v))
        for k, v in raw.items()
    }


def rank_variants(variants: dict, top_n: int = 10) -> dict:
    """
    Ranks trace variants by session count desc with deterministic tie-break
    (path_id ASC, edge-case 2.2). Returns Top-N rows plus an 'Other' aggregate row.
    """
    rows = []
    for path_tuple, count in variants.items():
        rows.append({
            "path_id": compute_path_id(list(path_tuple)),
            "path": list(path_tuple),
            "session_count": count,
        })
    rows.sort(key=lambda r: (-r["session_count"], r["path_id"]))

    total = sum(r["session_count"] for r in rows)
    top = rows[:top_n]
    rest = rows[top_n:]

    for i, r in enumerate(top):
        r["rank"] = i + 1
        r["pct_of_sessions"] = r["session_count"] / total * 100 if total else 0.0

    other = None
    if rest:
        other_count = sum(r["session_count"] for r in rest)
        other = {
            "label": f"Other ({len(rest)} variants)",
            "variant_count": len(rest),
            "session_count": other_count,
            # Set so top-N + Other percentages sum to exactly 100
            "pct_of_sessions": 100.0 - sum(r["pct_of_sessions"] for r in top),
        }

    return {"total_sessions": total, "top": top, "other": other}


def build_dfg(event_log: pd.DataFrame, coverage: float = 0.60, granularity: str = "full") -> dict:
    """
    Builds the Directly-Follows Graph via PM4Py, filtered to edges covering the
    top `coverage` fraction of cumulative traffic volume (Decision 11), with
    orphaned 0-degree nodes pruned (edge-case 2.3).
    granularity='collapsed' merges micro-engagement events into engaged_with_details.
    """
    if granularity == "collapsed":
        event_log = event_log.copy()
        event_log["concept:name"] = event_log["concept:name"].map(collapse_event_type)

    pm4py = _pm4py()
    dfg, start_acts, end_acts = pm4py.discover_dfg(event_log)
    perf_dfg, _, _ = pm4py.discover_performance_dfg(event_log)

    # Feature 1: source_total is strictly the sum of all DFG out-edges from u
    # plus end_activities[u] (the EXIT edge), computed before coverage pruning.
    source_totals = defaultdict(int)
    for (src, _tgt), cnt in dfg.items():
        source_totals[src] += int(cnt)
    for act, exit_cnt in end_acts.items():
        source_totals[act] += int(exit_cnt)

    total_volume = sum(dfg.values())
    threshold = coverage * total_volume

    edges_sorted = sorted(dfg.items(), key=lambda kv: (-kv[1], kv[0]))
    kept_edges = []
    cum = 0
    for (src, tgt), count in edges_sorted:
        if cum + count > threshold:
            continue
        perf = perf_dfg.get((src, tgt)) or {}
        src_total = source_totals.get(src, int(count))
        lost_vol = max(src_total - int(count), 0)
        dropoff_pct = (lost_vol / src_total * 100.0) if src_total > 0 else 0.0
        kept_edges.append({
            "source": src,
            "target": tgt,
            "count": int(count),
            "source_total": int(src_total),
            "lost_volume": int(lost_vol),
            "dropoff_pct": round(dropoff_pct, 2),
            "exit_at_source": int(end_acts.get(src, 0)),
            "perf_median": perf.get("median"),
            "perf_mean": perf.get("mean"),
        })
        cum += count

    # Prune nodes with 0 incident edges under the active filter
    node_degree = defaultdict(int)
    for e in kept_edges:
        node_degree[e["source"]] += 1
        node_degree[e["target"]] += 1
    nodes = [n for n, deg in node_degree.items() if deg >= 1]

    kept = set(nodes)
    return {
        "nodes": sorted(nodes),
        "edges": kept_edges,
        "start_activities": {k: v for k, v in start_acts.items() if k in kept},
        "end_activities": {k: v for k, v in end_acts.items() if k in kept},
        "source_totals": {k: int(v) for k, v in source_totals.items() if k in kept},
        "edge_volume": cum,
        "total_edge_volume": total_volume,
    }


def compute_funnel(variants: dict, path: list[str]) -> dict:
    """
    Strict sequential funnel for a selected path (Decision 6, edge-case 3.1):
    a trace counts at step m only if its first m events equal path[:m]
    (position-indexed, so repeated event names are unambiguous).
    """
    steps = []
    for m in range(1, len(path) + 1):
        prefix = tuple(path[:m])
        count = sum(c for v, c in variants.items() if tuple(v[:m]) == prefix)
        steps.append({"step": m, "event_type": path[m - 1], "count": count})

    first = steps[0]["count"]
    for i, s in enumerate(steps):
        s["conversion_pct"] = s["count"] / first * 100 if first else 0.0
        if i == 0:
            s["dropoff_pct"] = 0.0
        else:
            prev = steps[i - 1]["count"]
            s["dropoff_pct"] = (1 - s["count"] / prev) * 100 if prev else 0.0

    return {"path": path, "path_id": compute_path_id(path), "steps": steps}


def compute_trace_signatures(conn: DuckDBPyConnection, dataset_version: str,
                             case_id: str = "user_session") -> pd.DataFrame:
    """
    Per-trace signature table (trace_id, user_id, sig) for one dataset_version.
    Computed once and cached by callers: it is the shared basis for segment
    profiling and avoids re-running a STRING_AGG over all events per request.
    """
    if case_id not in _CASE_COLUMNS:
        raise ValueError(f"case_id must be one of {sorted(_CASE_COLUMNS)}")
    tx_key = "user_session" if case_id == "user_session" else "journey_id"
    search_key = tx_key
    page_key = "session_id" if case_id == "user_session" else "journey_id"
    return conn.execute(
        f"""
        WITH all_events AS (
            SELECT user_id, {tx_key} AS trace_id, event_type, event_time, event_id FROM TRANSACTION_EVENTS
            WHERE dataset_version = ?
            UNION ALL
            SELECT user_id, {search_key} AS trace_id, event_type, event_time, event_id FROM SEARCH_FILTER_EVENTS
            WHERE dataset_version = ?
            UNION ALL
            SELECT user_id, {page_key} AS trace_id, event_type, event_time, event_id FROM PAGE_ENGAGEMENT_EVENTS
            WHERE dataset_version = ?
        )
        SELECT trace_id, MIN(user_id) AS user_id,
               STRING_AGG(event_type, '|' ORDER BY event_time, event_id) AS sig
        FROM all_events
        GROUP BY trace_id
        """,
        [dataset_version, dataset_version, dataset_version],
    ).fetchdf()


def profile_segment(conn: DuckDBPyConnection, dataset_version: str, path: list[str],
                    case_id: str = "user_session", sig_df: pd.DataFrame | None = None) -> dict:
    """
    Segment profile for the cohort of users whose traces match `path` at the
    requested granularity (Decision 8): `user_session` groups per-session
    signatures, `journey_id` per-journey. Must match the granularity used by
    the variant/funnel endpoints so the profiled cohort is the clicked path.
    `sig_df` is the cached output of compute_trace_signatures: pass it to
    avoid re-scanning all events per request.
    Joins shared CUSTOMERS + versioned BEHAVIORAL_METRICS.
    Empty cohorts return zeroed metrics (edge-case 3.3).
    """
    if case_id not in _CASE_COLUMNS:
        raise ValueError(f"case_id must be one of {sorted(_CASE_COLUMNS)}")

    path_id = compute_path_id(path)
    sig = "|".join(path)
    if sig_df is None:
        sig_df = compute_trace_signatures(conn, dataset_version, case_id)

    # Weight each user by their number of matching traces: profiling DISTINCT
    # users once each dilutes toward population (nearly everyone has >=1
    # matching session), while trace-weighting reflects the path's actual
    # session population (e.g. mobile users bounce more often).
    cohort_users = (
        sig_df.loc[sig_df["sig"] == sig, "user_id"]
        .value_counts()
        .rename_axis("user_id")
        .reset_index(name="trace_weight")
    )

    # cohort_size = matching traces (not distinct users): the profiled population
    cohort_size = int(cohort_users["trace_weight"].sum())
    if cohort_size == 0:
        return {
            "path_id": path_id,
            "cohort_size": 0,
            "empty": True,
            "distributions": {},
            "kpis": {"avg_session_duration": 0.0, "avg_order_value": 0.0,
                     "avg_cart_abandonment_rate": 0.0, "avg_lifetime_value": 0.0},
        }

    conn.register("cohort_users_df", cohort_users)

    distributions = {}
    for col in ("device_type", "traffic_source", "country", "gender"):
        rows = conn.execute(
            f"""
            SELECT COALESCE({col}, 'UNKNOWN') AS bucket,
                   SUM(u.trace_weight) * 100.0 / SUM(SUM(u.trace_weight)) OVER () AS pct
            FROM CUSTOMERS c JOIN cohort_users_df u ON c.user_id = u.user_id
            GROUP BY bucket ORDER BY pct DESC, bucket ASC
            """
        ).fetchall()
        distributions[col] = [{"value": r[0], "pct": r[1]} for r in rows]

    kpi = conn.execute(
        """
        SELECT SUM(b.session_duration_average * u.trace_weight) / SUM(u.trace_weight),
               SUM(b.average_order_value * u.trace_weight) / SUM(u.trace_weight),
               SUM(b.cart_abandonment_rate * u.trace_weight) / SUM(u.trace_weight),
               SUM(b.lifetime_value * u.trace_weight) / SUM(u.trace_weight)
        FROM BEHAVIORAL_METRICS b
        JOIN cohort_users_df u ON b.user_id = u.user_id
        WHERE b.dataset_version = ?
        """,
        [dataset_version],
    ).fetchone()

    return {
        "path_id": path_id,
        "cohort_size": cohort_size,
        "empty": False,
        "distributions": distributions,
        "kpis": {
            "avg_session_duration": kpi[0] or 0.0,
            "avg_order_value": kpi[1] or 0.0,
            "avg_cart_abandonment_rate": kpi[2] or 0.0,
            "avg_lifetime_value": kpi[3] or 0.0,
        },
    }


def compute_value_defaults(conn: DuckDBPyConnection, dataset_version: str) -> dict:
    """
    Feature 1: Pulls real conversion value defaults from TRANSACTION_EVENTS.price
    and BEHAVIORAL_METRICS.average_order_value for the requested dataset_version.
    """
    tx_row = conn.execute(
        """
        SELECT AVG(price), COUNT(*)
        FROM TRANSACTION_EVENTS
        WHERE dataset_version = ? AND event_type = 'purchase' AND price IS NOT NULL
        """,
        [dataset_version],
    ).fetchone()
    bm_row = conn.execute(
        """
        SELECT AVG(average_order_value)
        FROM BEHAVIORAL_METRICS
        WHERE dataset_version = ? AND average_order_value > 0
        """,
        [dataset_version],
    ).fetchone()
    avg_price = float(tx_row[0]) if tx_row and tx_row[0] is not None else 0.0
    purchase_cnt = int(tx_row[1]) if tx_row and tx_row[1] is not None else 0
    avg_aov = float(bm_row[0]) if bm_row and bm_row[0] is not None else avg_price
    return {
        "default_value_per_conversion": round(avg_price or avg_aov, 2),
        "avg_purchase_price": round(avg_price, 2),
        "avg_order_value": round(avg_aov, 2),
        "purchase_count": purchase_cnt,
    }


def profile_edge_segment(conn: DuckDBPyConnection, dataset_version: str,
                         edge_source: str, edge_target: str,
                         case_id: str = "user_session") -> dict:
    """
    Feature 2: Breaks a selected transition (edge_source -> edge_target) by
    WHO (device_type, traffic_source, membership_status, country, age_bucket)
    by joining the event log to CUSTOMERS on user_id.
    Computes each segment's drop rate on that edge vs the overall baseline,
    using the exact same DFG departure definition:
      source_total = all departures from edge_source (out-edges + EXIT)
      advanced_count = transitions from edge_source immediately to edge_target
      lost_volume = source_total - advanced_count
      drop_rate_pct = lost_volume / source_total * 100
    """
    if case_id not in _CASE_COLUMNS:
        raise ValueError(f"case_id must be one of {sorted(_CASE_COLUMNS)}")
    tx_key = "user_session" if case_id == "user_session" else "journey_id"
    page_key = "session_id" if case_id == "user_session" else "journey_id"

    transitions_df = conn.execute(
        f"""
        WITH all_events AS (
            SELECT user_id, {tx_key} AS trace_id, event_type, event_time, event_id
            FROM TRANSACTION_EVENTS WHERE dataset_version = ?
            UNION ALL
            SELECT user_id, {tx_key} AS trace_id, event_type, event_time, event_id
            FROM SEARCH_FILTER_EVENTS WHERE dataset_version = ?
            UNION ALL
            SELECT user_id, {page_key} AS trace_id, event_type, event_time, event_id
            FROM PAGE_ENGAGEMENT_EVENTS WHERE dataset_version = ?
        ),
        ordered AS (
            SELECT user_id, trace_id, event_type AS src,
                   LEAD(event_type, 1, '__EXIT__') OVER (
                       PARTITION BY trace_id ORDER BY event_time ASC, event_id ASC
                   ) AS tgt
            FROM all_events
        )
        SELECT o.tgt,
               COALESCE(c.device_type, 'UNKNOWN') AS device_type,
               COALESCE(c.traffic_source, 'UNKNOWN') AS traffic_source,
               COALESCE(c.membership_status, 'UNKNOWN') AS membership_status,
               COALESCE(c.country, 'UNKNOWN') AS country,
               CASE
                   WHEN c.age IS NULL THEN 'UNKNOWN'
                   WHEN c.age < 25 THEN '18-24'
                   WHEN c.age < 35 THEN '25-34'
                   WHEN c.age < 45 THEN '35-44'
                   WHEN c.age < 55 THEN '45-54'
                   ELSE '55+'
               END AS age_bucket,
               COUNT(*) AS cnt
        FROM ordered o
        LEFT JOIN CUSTOMERS c ON o.user_id = c.user_id
        WHERE o.src = ?
        GROUP BY 1, 2, 3, 4, 5, 6
        """,
        [dataset_version, dataset_version, dataset_version, edge_source],
    ).fetchdf()

    if transitions_df.empty:
        return {
            "edge": {"source": edge_source, "target": edge_target},
            "source_total": 0,
            "advanced_count": 0,
            "lost_volume": 0,
            "baseline_drop_rate_pct": 0.0,
            "dimensions": {},
        }

    total_src = int(transitions_df["cnt"].sum())
    adv_mask = transitions_df["tgt"] == edge_target
    total_adv = int(transitions_df.loc[adv_mask, "cnt"].sum())
    total_lost = max(total_src - total_adv, 0)
    baseline_drop_rate = (total_lost / total_src * 100.0) if total_src > 0 else 0.0

    dimensions = {}
    for dim in ("device_type", "traffic_source", "membership_status", "country", "age_bucket"):
        grp_all = transitions_df.groupby(dim, as_index=False)["cnt"].sum().rename(columns={"cnt": "source_total"})
        grp_adv = (
            transitions_df.loc[adv_mask]
            .groupby(dim, as_index=False)["cnt"]
            .sum()
            .rename(columns={"cnt": "advanced_count"})
        )
        merged = grp_all.merge(grp_adv, on=dim, how="left")
        merged["advanced_count"] = merged["advanced_count"].fillna(0).astype(int)
        merged["lost_volume"] = (merged["source_total"] - merged["advanced_count"]).clip(lower=0)
        merged["drop_rate_pct"] = (merged["lost_volume"] / merged["source_total"] * 100.0).fillna(0.0)
        merged["delta_vs_baseline_pct"] = merged["drop_rate_pct"] - baseline_drop_rate
        merged["share_of_lost_pct"] = (
            merged["lost_volume"] / total_lost * 100.0 if total_lost > 0 else 0.0
        )
        merged = merged.sort_values(["drop_rate_pct", "lost_volume"], ascending=[False, False])

        dimensions[dim] = [
            {
                "segment": str(r[dim]),
                "source_total": int(r["source_total"]),
                "advanced_count": int(r["advanced_count"]),
                "lost_volume": int(r["lost_volume"]),
                "drop_rate_pct": round(float(r["drop_rate_pct"]), 2),
                "delta_vs_baseline_pct": round(float(r["delta_vs_baseline_pct"]), 2),
                "share_of_lost_pct": round(float(r["share_of_lost_pct"]), 2),
            }
            for _, r in merged.iterrows()
        ]

    return {
        "edge": {"source": edge_source, "target": edge_target},
        "source_total": total_src,
        "advanced_count": total_adv,
        "lost_volume": total_lost,
        "baseline_drop_rate_pct": round(baseline_drop_rate, 2),
        "dimensions": dimensions,
    }


def compute_edge_timeline_changes(conn: DuckDBPyConnection, dataset_version: str,
                                  case_id: str = "user_session") -> dict:
    """
    Feature 3: Uses event_time to compute per-edge % change across the first half
    vs second half of the dataset window, tags spike / drop / stable and
    healthy / unhealthy, and pulls health signals from BEHAVIORAL_METRICS
    (cart_abandonment_rate, session_duration_average, days_since_last_purchase).
    """
    if case_id not in _CASE_COLUMNS:
        raise ValueError(f"case_id must be one of {sorted(_CASE_COLUMNS)}")
    tx_key = "user_session" if case_id == "user_session" else "journey_id"
    page_key = "session_id" if case_id == "user_session" else "journey_id"

    rows = conn.execute(
        f"""
        WITH all_events AS (
            SELECT user_id, {tx_key} AS trace_id, event_type, event_time, event_id
            FROM TRANSACTION_EVENTS WHERE dataset_version = ?
            UNION ALL
            SELECT user_id, {tx_key} AS trace_id, event_type, event_time, event_id
            FROM SEARCH_FILTER_EVENTS WHERE dataset_version = ?
            UNION ALL
            SELECT user_id, {page_key} AS trace_id, event_type, event_time, event_id
            FROM PAGE_ENGAGEMENT_EVENTS WHERE dataset_version = ?
        ),
        bounds AS (
            SELECT MIN(event_time) AS t_min,
                   MAX(event_time) AS t_max,
                   MIN(event_time) + (MAX(event_time) - MIN(event_time)) / 2 AS t_mid
            FROM all_events
        ),
        ordered AS (
            SELECT e.user_id, e.trace_id, e.event_type AS src, e.event_time,
                   LEAD(e.event_type, 1, '__EXIT__') OVER (
                       PARTITION BY e.trace_id ORDER BY e.event_time ASC, e.event_id ASC
                   ) AS tgt,
                   CASE WHEN e.event_time < b.t_mid THEN 'H1' ELSE 'H2' END AS half,
                   b.t_min, b.t_mid, b.t_max
            FROM all_events e CROSS JOIN bounds b
        )
        SELECT o.src, o.tgt, o.half,
               COUNT(*) AS edge_cnt,
               AVG(bm.cart_abandonment_rate) AS avg_abandon,
               AVG(bm.session_duration_average) AS avg_dur,
               AVG(bm.days_since_last_purchase) AS avg_recency,
               MIN(o.t_min) AS t_min, MIN(o.t_mid) AS t_mid, MIN(o.t_max) AS t_max
        FROM ordered o
        LEFT JOIN BEHAVIORAL_METRICS bm
          ON o.user_id = bm.user_id AND bm.dataset_version = ?
        GROUP BY o.src, o.tgt, o.half
        """,
        [dataset_version, dataset_version, dataset_version, dataset_version],
    ).fetchdf()

    pop_bm = conn.execute(
        """
        SELECT AVG(cart_abandonment_rate),
               AVG(session_duration_average),
               AVG(days_since_last_purchase)
        FROM BEHAVIORAL_METRICS
        WHERE dataset_version = ?
        """,
        [dataset_version],
    ).fetchone()
    pop_abandon = float(pop_bm[0] or 0.0)
    pop_dur = float(pop_bm[1] or 0.0)
    pop_recency = float(pop_bm[2] or 0.0)

    if rows.empty:
        return {"window": {}, "population_health": {}, "changes": []}

    t_min = str(rows["t_min"].iloc[0])[:10]
    t_mid = str(rows["t_mid"].iloc[0])[:10]
    t_max = str(rows["t_max"].iloc[0])[:10]

    # Source totals per (src, half)
    src_half = rows.groupby(["src", "half"])["edge_cnt"].sum().to_dict()

    # Build per-(src, tgt) comparison for real edges (tgt != '__EXIT__')
    real_rows = rows[rows["tgt"] != "__EXIT__"]
    pairs = real_rows[["src", "tgt"]].drop_duplicates()

    indexed = real_rows.set_index(["src", "tgt", "half"])
    changes = []
    for _, p in pairs.iterrows():
        src, tgt = str(p["src"]), str(p["tgt"])
        if src == "purchase":
            continue
        h1_cnt = int(indexed.loc[(src, tgt, "H1"), "edge_cnt"]) if (src, tgt, "H1") in indexed.index else 0
        h2_cnt = int(indexed.loc[(src, tgt, "H2"), "edge_cnt"]) if (src, tgt, "H2") in indexed.index else 0
        h1_src_tot = int(src_half.get((src, "H1"), 0))
        h2_src_tot = int(src_half.get((src, "H2"), 0))
        if h1_src_tot + h2_src_tot < 50:
            continue

        h1_lost = max(h1_src_tot - h1_cnt, 0)
        h2_lost = max(h2_src_tot - h2_cnt, 0)
        h1_drop_rate = (h1_lost / h1_src_tot * 100.0) if h1_src_tot > 0 else 0.0
        h2_drop_rate = (h2_lost / h2_src_tot * 100.0) if h2_src_tot > 0 else 0.0

        # % change in drop-off rate and in edge transition volume across the window
        drop_rel_change = (
            ((h2_drop_rate - h1_drop_rate) / h1_drop_rate * 100.0)
            if h1_drop_rate > 0 else 0.0
        )
        drop_pts_change = h2_drop_rate - h1_drop_rate
        vol_pct_change = ((h2_cnt - h1_cnt) / h1_cnt * 100.0) if h1_cnt > 0 else 0.0

        # Behavioral health signals on this edge (weighted across H1/H2)
        tot_edge = h1_cnt + h2_cnt
        if tot_edge > 0:
            a1 = float(indexed.loc[(src, tgt, "H1"), "avg_abandon"] or 0.0) if (src, tgt, "H1") in indexed.index else pop_abandon
            a2 = float(indexed.loc[(src, tgt, "H2"), "avg_abandon"] or 0.0) if (src, tgt, "H2") in indexed.index else pop_abandon
            d1 = float(indexed.loc[(src, tgt, "H1"), "avg_dur"] or 0.0) if (src, tgt, "H1") in indexed.index else pop_dur
            d2 = float(indexed.loc[(src, tgt, "H2"), "avg_dur"] or 0.0) if (src, tgt, "H2") in indexed.index else pop_dur
            r1 = float(indexed.loc[(src, tgt, "H1"), "avg_recency"] or 0.0) if (src, tgt, "H1") in indexed.index else pop_recency
            r2 = float(indexed.loc[(src, tgt, "H2"), "avg_recency"] or 0.0) if (src, tgt, "H2") in indexed.index else pop_recency
            edge_abandon = (a1 * h1_cnt + a2 * h2_cnt) / tot_edge
            edge_dur = (d1 * h1_cnt + d2 * h2_cnt) / tot_edge
            edge_recency = (r1 * h1_cnt + r2 * h2_cnt) / tot_edge
        else:
            edge_abandon, edge_dur, edge_recency = pop_abandon, pop_dur, pop_recency

        # Tag spike / drop / stable based on transition volume & drop-off movement
        if vol_pct_change <= -3.0 or drop_rel_change >= 2.0:
            trend_tag = "drop"
        elif vol_pct_change >= 3.0 or drop_rel_change <= -2.0:
            trend_tag = "spike"
        else:
            trend_tag = "stable"

        # Tag healthy / unhealthy from BEHAVIORAL_METRICS + drop-off worsening
        unhealthy_signals = 0
        if edge_abandon > pop_abandon * 1.02:
            unhealthy_signals += 1
        if edge_recency > pop_recency * 1.02:
            unhealthy_signals += 1
        if drop_pts_change > 0.5 or h2_drop_rate > 50.0:
            unhealthy_signals += 1
        health_tag = "unhealthy" if unhealthy_signals >= 2 else "healthy"

        # Human callout summary
        magnitude = abs(vol_pct_change) if abs(vol_pct_change) >= abs(drop_rel_change) else abs(drop_rel_change)
        if vol_pct_change < -0.5 or drop_pts_change > 0.1:
            direction_word = "worsened"
            callout_pct = round(max(abs(vol_pct_change), abs(drop_rel_change)), 1)
        elif vol_pct_change > 0.5 or drop_pts_change < -0.1:
            direction_word = "improved"
            callout_pct = round(max(abs(vol_pct_change), abs(drop_rel_change)), 1)
        else:
            direction_word = "shifted"
            callout_pct = round(magnitude, 1)

        changes.append({
            "source": src,
            "target": tgt,
            "edge_key": f"{src}→{tgt}",
            "callout": f"{src} → {tgt} {direction_word} {callout_pct}%",
            "direction": direction_word,
            "trend_tag": trend_tag,
            "health_tag": health_tag,
            "h1_count": h1_cnt,
            "h2_count": h2_cnt,
            "h1_lost_volume": h1_lost,
            "h2_lost_volume": h2_lost,
            "h1_drop_rate_pct": round(h1_drop_rate, 2),
            "h2_drop_rate_pct": round(h2_drop_rate, 2),
            "drop_rate_rel_change_pct": round(drop_rel_change, 2),
            "drop_rate_pts_change": round(drop_pts_change, 2),
            "volume_pct_change": round(vol_pct_change, 2),
            "behavioral_signals": {
                "cart_abandonment_rate": round(edge_abandon, 3),
                "session_duration_average": round(edge_dur, 1),
                "days_since_last_purchase": round(edge_recency, 1),
            },
        })

    changes.sort(key=lambda c: (c["health_tag"] != "unhealthy", -(c["h1_lost_volume"] + c["h2_lost_volume"])))

    return {
        "window": {"start": t_min, "midpoint": t_mid, "end": t_max},
        "population_health": {
            "cart_abandonment_rate": round(pop_abandon, 3),
            "session_duration_average": round(pop_dur, 1),
            "days_since_last_purchase": round(pop_recency, 1),
        },
        "changes": changes,
    }
