"""
Test Suite for Phase 2: Python Process Mining & Core Analytics Subsystem.
"""
import pytest

from src.analytics import (
    assemble_event_log,
    build_dfg,
    compute_funnel,
    get_variants,
    profile_segment,
    rank_variants,
)
from src.db import DatabaseManager
from src.generator import SyntheticDatasetGenerator
from src.utils import compute_path_id


@pytest.fixture(scope="module")
def analytics_ctx():
    """Small generated dataset + assembled PRE event log + PM4Py variants."""
    db_mgr = DatabaseManager(":memory:")
    conn = db_mgr.get_connection()
    gen = SyntheticDatasetGenerator(conn, seed=123)

    gen.generate_shared_dimensions(num_customers=50, num_products=20)
    gen.generate_event_stream(dataset_version="PRE", total_sessions=40)
    gen.generate_event_stream(dataset_version="POST", total_sessions=40)

    log = assemble_event_log(conn, "PRE")
    variants = get_variants(log)

    yield conn, log, variants
    db_mgr.close()


def test_event_log_assembly(analytics_ctx):
    """Event log carries the 5 backbone fields, single dataset_version, deterministic ordering."""
    _, log, _ = analytics_ctx
    for col in ("user_id", "session_id", "product_id", "event_type", "event_time"):
        assert col in log.columns
    assert (log["dataset_version"] == "PRE").all()
    assert len(log) > 0
    # Case-contiguous layout required by PM4Py's numpy path; within each case,
    # events ordered by event_time ASC, event_id ASC (edge-case 1.2)
    for _, grp in log.groupby("case:concept:name", sort=False):
        keys = list(zip(grp["event_time"], grp["event_id"]))
        assert keys == sorted(keys)


def test_pm4py_variant_mining(analytics_ctx):
    """Eval 2.1: variants ranked desc, Top-N cutoff, 'Other' row, pct sums to 100."""
    _, _, variants = analytics_ctx
    assert len(variants) > 0

    result = rank_variants(variants, top_n=2)
    total = result["total_sessions"]
    assert total == sum(variants.values())

    counts = [r["session_count"] for r in result["top"]]
    assert counts == sorted(counts, reverse=True)
    assert len(result["top"]) <= 2

    # path_id is the deterministic MD5 of the event sequence
    for r in result["top"]:
        assert r["path_id"] == compute_path_id(r["path"])

    # Percentages sum to 100% +/- 0.01%
    pct_sum = sum(r["pct_of_sessions"] for r in result["top"])
    if result["other"]:
        assert result["other"]["session_count"] == total - sum(counts)
        pct_sum += result["other"]["pct_of_sessions"]
    assert abs(pct_sum - 100.0) <= 0.01


def test_dfg_60_percent_filter(analytics_ctx):
    """Eval 2.2: filtered edge volume <= 60% of total; no orphaned 0-degree nodes."""
    _, log, _ = analytics_ctx
    result = build_dfg(log, coverage=0.60)

    assert result["edge_volume"] <= 0.60 * result["total_edge_volume"]

    nodes = set(result["nodes"])
    degree = {n: 0 for n in nodes}
    for e in result["edges"]:
        assert e["source"] in nodes and e["target"] in nodes
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    assert all(d >= 1 for d in degree.values())


def test_strict_funnel_math(analytics_ctx):
    """Eval 2.3: strict ordered-prefix counts, monotonically non-increasing,
    conversion = count_m / count_1, drop-off = 100% - count_m / count_{m-1}."""
    _, log, variants = analytics_ctx
    path = ["view_product", "add_to_cart", "purchase"]

    funnel = compute_funnel(variants, path)
    steps = funnel["steps"]
    assert [s["count"] for s in steps] == sorted((s["count"] for s in steps), reverse=True)

    # Independent verification against raw traces
    traces = log.sort_values(["time:timestamp", "event_id"]).groupby("case:concept:name")["concept:name"].apply(tuple)
    for i, s in enumerate(steps):
        expected = sum(1 for t in traces if tuple(t[: i + 1]) == tuple(path[: i + 1]))
        assert s["count"] == expected

    first = steps[0]["count"]
    for i, s in enumerate(steps):
        assert abs(s["conversion_pct"] - (s["count"] / first * 100 if first else 0.0)) < 1e-9
        if i == 0:
            assert s["dropoff_pct"] == 0.0
        else:
            prev = steps[i - 1]["count"]
            expected_drop = (1 - s["count"] / prev) * 100 if prev else 0.0
            assert abs(s["dropoff_pct"] - expected_drop) < 1e-9


def test_segment_profile_aggregation(analytics_ctx):
    """Eval 2.4: cohort segment distributions sum to 100%, filtered by dataset_version;
    empty cohorts return zeroed metrics (edge-case 3.3)."""
    conn, _, variants = analytics_ctx

    top_path = list(max(variants, key=lambda v: variants[v]))
    profile = profile_segment(conn, "PRE", top_path)

    assert profile["path_id"] == compute_path_id(top_path)
    assert profile["cohort_size"] > 0
    assert not profile["empty"]

    for col, dist in profile["distributions"].items():
        assert abs(sum(b["pct"] for b in dist) - 100.0) < 1e-6, col

    assert set(profile["kpis"]) == {
        "avg_session_duration", "avg_order_value",
        "avg_cart_abandonment_rate", "avg_lifetime_value",
    }

    # Empty cohort: zeroed metrics + empty flag
    empty = profile_segment(conn, "PRE", ["no_such_event", "also_fake"])
    assert empty["empty"]
    assert empty["cohort_size"] == 0
    assert all(v == 0.0 for v in empty["kpis"].values())


def test_segment_profiles_differentiate_by_path(analytics_ctx):
    """Cohort assignment is attribute-conditioned: the bounce path's segment must
    skew differently from a purchase path's (e.g. mobile share), not uniform."""
    conn, _, _ = analytics_ctx
    bounce = profile_segment(conn, "PRE", ["view_product"])
    purchase = profile_segment(conn, "PRE", ["view_product", "add_to_cart", "login", "purchase"])

    mobile_pct = lambda p: next((d["pct"] for d in p["distributions"]["device_type"] if d["value"] == "Mobile"), 0.0)
    assert abs(mobile_pct(bounce) - mobile_pct(purchase)) > 3.0
