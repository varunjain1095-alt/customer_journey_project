"""
Test Suite for Phase 1: Shared Database Schema & Synthetic Generator Engine.
"""
from datetime import datetime, timedelta, timezone

import pytest
from src.db import DatabaseManager
from src.generator import JOURNEY_INACTIVITY_THRESHOLD_SECONDS, SyntheticDatasetGenerator
from src.utils import compute_path_id


@pytest.fixture
def db_gen():
    """Fixture initializing DatabaseManager and generating test data."""
    db_mgr = DatabaseManager(":memory:")
    conn = db_mgr.get_connection()
    gen = SyntheticDatasetGenerator(conn, seed=123)
    
    # Stage 1: Generate shared dimensions
    gen.generate_shared_dimensions(num_customers=50, num_products=20)
    
    # Stage 2: Generate event streams for PRE and POST
    gen.generate_event_stream(dataset_version="PRE", total_sessions=30)
    gen.generate_event_stream(dataset_version="POST", total_sessions=30)
    
    yield conn, gen
    db_mgr.close()


def test_schema_integrity(db_gen):
    """Test 1.1: Verify all 9 relational tables exist with dataset_version tags where applicable."""
    conn, _ = db_gen
    # Query base schema tables
    base_tables = [row[0] for row in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'").fetchall()]
    
    expected_tables = {
        "CUSTOMERS", "BEHAVIORAL_METRICS", "PRODUCTS",
        "TRANSACTION_EVENTS", "SEARCH_FILTER_EVENTS", "PAGE_ENGAGEMENT_EVENTS",
        "SEARCH_SATELLITE", "PAGE_ENGAGEMENT_SATELLITE", "STARRED_PATHS"
    }
    assert expected_tables.issubset(set(base_tables))
    assert len(expected_tables) == 9
    
    # Verify dataset_version column presence
    versioned_tables = [
        "TRANSACTION_EVENTS", "SEARCH_FILTER_EVENTS", "PAGE_ENGAGEMENT_EVENTS",
        "SEARCH_SATELLITE", "PAGE_ENGAGEMENT_SATELLITE", "BEHAVIORAL_METRICS", "STARRED_PATHS"
    ]
    for tbl in versioned_tables:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info('{tbl}')").fetchall()]
        assert "dataset_version" in cols, f"Missing dataset_version in {tbl}"


def test_shared_dimension_consistency(db_gen):
    """Test 1.2: Verify CUSTOMERS and PRODUCTS are shared identically across PRE and POST datasets."""
    conn, _ = db_gen
    
    # Check customer ID set equality across versions
    pre_users = set(row[0] for row in conn.execute("SELECT DISTINCT user_id FROM TRANSACTION_EVENTS WHERE dataset_version = 'PRE'").fetchall())
    post_users = set(row[0] for row in conn.execute("SELECT DISTINCT user_id FROM TRANSACTION_EVENTS WHERE dataset_version = 'POST'").fetchall())
    all_customers = set(row[0] for row in conn.execute("SELECT DISTINCT user_id FROM CUSTOMERS").fetchall())
    
    assert pre_users.issubset(all_customers)
    assert post_users.issubset(all_customers)
    
    # Check popularity_seed consistency for products
    seeds = conn.execute("SELECT product_id, popularity_seed FROM PRODUCTS").fetchall()
    assert len(seeds) == 20
    assert all(s[1] > 0 for s in seeds)


def test_journey_id_assignment(db_gen):
    """Test 1.3: Verify 10-day inactivity timeout and purchase termination rules for journey grouping."""
    conn, gen = db_gen
    base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)

    def prev_state(journey_id, last_time, purchased):
        return {"journey_id": journey_id, "last_event_time": last_time, "last_was_purchase": purchased}

    # Case A: Day 1 then Day 5 (4-day gap <= 10d, no purchase) -> same journey
    j1 = gen.resolve_journey_id(None, base, "PRE")
    j2 = gen.resolve_journey_id(prev_state(j1, base + timedelta(hours=1), False), base + timedelta(days=4), "PRE")
    assert j1 == j2

    # Case B: browsing Day 1-9, return Day 21 (12-day gap > 10d) -> new journey
    j3 = gen.resolve_journey_id(prev_state(j1, base + timedelta(days=9), False), base + timedelta(days=21), "PRE")
    assert j3 != j1

    # Case C: session ends in purchase; next session next day -> new journey
    j4 = gen.resolve_journey_id(prev_state(j1, base + timedelta(days=3), True), base + timedelta(days=4), "PRE")
    assert j4 != j1

    # Boundary: exactly 864,000s continues; 864,001s splits
    j5 = gen.resolve_journey_id(prev_state(j1, base, False), base + timedelta(seconds=JOURNEY_INACTIVITY_THRESHOLD_SECONDS), "PRE")
    assert j5 == j1
    j6 = gen.resolve_journey_id(prev_state(j1, base, False), base + timedelta(seconds=JOURNEY_INACTIVITY_THRESHOLD_SECONDS + 1), "PRE")
    assert j6 != j1


def test_journey_invariants_in_storage(db_gen):
    """Test 1.3 (storage level): no stored journey contains a >10-day gap between
    consecutive sessions or a session following a purchase-terminated session."""
    conn, _ = db_gen
    violations = conn.execute("""
        WITH all_events AS (
            SELECT dataset_version, journey_id, user_session AS session_id, event_time, event_type FROM TRANSACTION_EVENTS
            UNION ALL
            SELECT dataset_version, journey_id, user_session, event_time, event_type FROM SEARCH_FILTER_EVENTS
            UNION ALL
            SELECT dataset_version, journey_id, session_id, event_time, event_type FROM PAGE_ENGAGEMENT_EVENTS
        ),
        sess AS (
            SELECT journey_id, dataset_version, session_id,
                   MIN(event_time) AS s_start, MAX(event_time) AS s_end,
                   MAX(CASE WHEN event_type = 'purchase' THEN 1 ELSE 0 END) AS has_purchase
            FROM all_events
            GROUP BY journey_id, dataset_version, session_id
        ),
        ordered AS (
            SELECT *,
                   LAG(s_end) OVER w AS prev_end,
                   LAG(has_purchase) OVER w AS prev_purchased
            FROM sess
            WINDOW w AS (PARTITION BY journey_id, dataset_version ORDER BY s_start, session_id)
        )
        SELECT COUNT(*) FROM ordered
        WHERE (prev_end IS NOT NULL AND DATE_DIFF('second', prev_end, s_start) > 864000)
           OR prev_purchased = 1
    """).fetchone()[0]
    assert violations == 0


def test_session_timestamps_strictly_monotonic(db_gen):
    """Edge-case 1.2: all event timestamps within a session are distinct (strictly increasing)."""
    conn, _ = db_gen
    dup = conn.execute("""
        WITH all_events AS (
            SELECT dataset_version, user_session AS session_id, event_time FROM TRANSACTION_EVENTS
            UNION ALL
            SELECT dataset_version, user_session, event_time FROM SEARCH_FILTER_EVENTS
            UNION ALL
            SELECT dataset_version, session_id, event_time FROM PAGE_ENGAGEMENT_EVENTS
        )
        SELECT COUNT(*) FROM (
            SELECT dataset_version, session_id, event_time
            FROM all_events
            GROUP BY dataset_version, session_id, event_time
            HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    assert dup == 0


def test_journey_id_version_scoping(db_gen):
    """Test 1.6: Verify journey_id values are scoped by (journey_id, dataset_version)."""
    conn, _ = db_gen
    # Grouping by journey_id, dataset_version yields clean separation
    pre_journeys = set(row[0] for row in conn.execute("SELECT DISTINCT journey_id FROM TRANSACTION_EVENTS WHERE dataset_version = 'PRE'").fetchall())
    post_journeys = set(row[0] for row in conn.execute("SELECT DISTINCT journey_id FROM TRANSACTION_EVENTS WHERE dataset_version = 'POST'").fetchall())
    
    # Pre and Post journey IDs are namespaced per dataset version
    assert all(j.startswith("JRN_PRE_") for j in pre_journeys)
    assert all(j.startswith("JRN_POST_") for j in post_journeys)


def test_deterministic_path_hash():
    """Test 1.4: Verify compute_path_id produces deterministic MD5 hashes without run-specific salt."""
    trace_1 = ["view_product", "add_to_cart", "purchase"]
    trace_2 = ["view_product", "add_to_cart", "purchase"]
    
    hash_1 = compute_path_id(trace_1)
    hash_2 = compute_path_id(trace_2)
    
    assert hash_1 == hash_2
    assert hash_1 == "c22374b7c3318692b4315ed301044165"


def test_behavioral_metrics_per_version(db_gen):
    """Test 1.5 & Phase 1 Eval: Verify BEHAVIORAL_METRICS exists for both PRE and POST
    versions per user and differs meaningfully across versions for the same user."""
    conn, _ = db_gen
    metrics_pre = conn.execute("SELECT COUNT(*) FROM BEHAVIORAL_METRICS WHERE dataset_version = 'PRE'").fetchone()[0]
    metrics_post = conn.execute("SELECT COUNT(*) FROM BEHAVIORAL_METRICS WHERE dataset_version = 'POST'").fetchone()[0]

    assert metrics_pre > 0
    assert metrics_post > 0

    differing_users = conn.execute("""
        SELECT COUNT(*) FROM BEHAVIORAL_METRICS a
        JOIN BEHAVIORAL_METRICS b
          ON a.user_id = b.user_id AND a.dataset_version = 'PRE' AND b.dataset_version = 'POST'
        WHERE a.cart_abandonment_rate != b.cart_abandonment_rate
           OR a.lifetime_value != b.lifetime_value
    """).fetchone()[0]
    assert differing_users > 0


def test_cart_abandonment_and_coupon_events(db_gen):
    """Verify remove_from_cart abandonment and apply_coupon events are generated, so
    discount_usage and cart_abandonment_rate are real (non-zero) metrics."""
    conn, _ = db_gen
    assert conn.execute("SELECT COUNT(*) FROM TRANSACTION_EVENTS WHERE event_type = 'remove_from_cart'").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM TRANSACTION_EVENTS WHERE event_type = 'apply_coupon'").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM BEHAVIORAL_METRICS WHERE discount_usage > 0").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM BEHAVIORAL_METRICS WHERE cart_abandonment_rate > 0").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM BEHAVIORAL_METRICS WHERE days_since_last_purchase IS NOT NULL").fetchone()[0] > 0


def test_satellite_tables_populated(db_gen):
    """Test 1.5 & Phase 1 Eval: Verify SEARCH_SATELLITE and PAGE_ENGAGEMENT_SATELLITE are non-empty and carry dataset_version."""
    conn, _ = db_gen
    search_sat_count = conn.execute("SELECT COUNT(*) FROM SEARCH_SATELLITE WHERE results_returned IS NOT NULL").fetchone()[0]
    page_sat_count = conn.execute("SELECT COUNT(*) FROM PAGE_ENGAGEMENT_SATELLITE WHERE time_spent_seconds IS NOT NULL").fetchone()[0]
    
    assert search_sat_count > 0
    assert page_sat_count > 0
