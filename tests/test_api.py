"""
Test Suite for Phase 4 API layer + granularity collapse (eval 4.3).
"""
import pytest
from fastapi.testclient import TestClient

from src.analytics import COLLAPSED_NODE
from src.db import DatabaseManager
from src.generator import SyntheticDatasetGenerator
from src.server import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app(":memory:")
    conn = app.state.conn
    gen = SyntheticDatasetGenerator(conn, seed=123)
    gen.generate_shared_dimensions(num_customers=50, num_products=20)
    gen.generate_event_stream(dataset_version="PRE", total_sessions=40)
    gen.generate_event_stream(dataset_version="POST", total_sessions=40)
    return TestClient(app)


def test_health(client):
    assert client.get("/api/health").status_code == 200


def test_variants_endpoint(client):
    r = client.get("/api/variants", params={"dataset_version": "PRE", "top_n": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["total_sessions"] > 0
    counts = [row["session_count"] for row in body["top"]]
    assert counts == sorted(counts, reverse=True)
    pct = sum(row["pct_of_sessions"] for row in body["top"])
    if body["other"]:
        pct += body["other"]["pct_of_sessions"]
    assert abs(pct - 100.0) <= 0.01


def test_dfg_endpoint_and_granularity_collapse(client):
    """Eval 4.3: toggling to Collapsed shrinks the node network and introduces engaged_with_details."""
    # coverage=1.0 so micro-event nodes aren't already pruned by the traffic filter
    full = client.get("/api/dfg", params={"granularity": "full", "coverage": 1.0}).json()
    collapsed = client.get("/api/dfg", params={"granularity": "collapsed", "coverage": 1.0}).json()

    assert len(collapsed["nodes"]) < len(full["nodes"])
    assert COLLAPSED_NODE in collapsed["nodes"]
    # Micro-events are gone from the collapsed view
    assert "view_reviews" not in collapsed["nodes"]
    assert "expand_description" not in collapsed["nodes"]


def test_funnel_endpoint(client):
    r = client.get("/api/funnel", params={
        "dataset_version": "PRE",
        "path": ["view_product", "add_to_cart", "login", "purchase"],
    })
    body = r.json()
    counts = [s["count"] for s in body["steps"]]
    assert counts == sorted(counts, reverse=True)


def test_segment_endpoint(client):
    top = client.get("/api/variants", params={"top_n": 1}).json()["top"][0]
    r = client.get("/api/segment", params={"dataset_version": "PRE", "path": top["path"]})
    body = r.json()
    assert body["cohort_size"] > 0
    for dist in body["distributions"].values():
        assert abs(sum(b["pct"] for b in dist) - 100.0) < 1e-6


def test_starred_roundtrip(client):
    star = client.post("/api/star", json={
        "pm_user_id": "pm_test", "path_id": "abc123", "dataset_version": "PRE"})
    assert star.status_code == 200

    listed = client.get("/api/starred", params={"pm_user_id": "pm_test"}).json()["starred"]
    assert any(s["path_id"] == "abc123" and s["dataset_version"] == "PRE" for s in listed)

    client.request("DELETE", "/api/star", json={
        "pm_user_id": "pm_test", "path_id": "abc123", "dataset_version": "PRE"})
    listed = client.get("/api/starred", params={"pm_user_id": "pm_test"}).json()["starred"]
    assert not any(s["path_id"] == "abc123" for s in listed)
