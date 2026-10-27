"""
Test Suite for Phase 3: R Microservice & Contextual Animation Subsystem.

Integration tests (service health, live rendering) are skipped when the
Plumber container isn't reachable on localhost:8000.
"""
import socket
import time

import pytest
import requests

from src.animation_client import AnimationClient, animation_cache_key
from src.db import DatabaseManager
from src.generator import SyntheticDatasetGenerator

SERVICE_URL = "http://localhost:8000"


def _service_up(url: str = SERVICE_URL, timeout: float = 2.0) -> bool:
    """True only if the Plumber service answers /health — an open port alone
    isn't enough (another app may be bound to :8000)."""
    try:
        resp = requests.get(f"{url}/health", timeout=timeout)
        return resp.status_code == 200 and resp.json().get("status") == "ok"
    except requests.RequestException:
        return False


service_required = pytest.mark.skipif(
    not _service_up(), reason="R Plumber container not reachable on localhost:8000"
)


@pytest.fixture(scope="module")
def conn():
    db_mgr = DatabaseManager(":memory:")
    c = db_mgr.get_connection()
    gen = SyntheticDatasetGenerator(c, seed=123)
    gen.generate_shared_dimensions(num_customers=50, num_products=20)
    gen.generate_event_stream(dataset_version="PRE", total_sessions=40)
    yield c
    db_mgr.close()


@service_required
def test_r_microservice_health():
    """Eval 3.1: Plumber service responds."""
    resp = requests.get(f"{SERVICE_URL}/health", timeout=5)
    assert resp.status_code == 200


@service_required
def test_animation_rendering(conn):
    """Evals 3.1/3.2: POST /animate returns an htmlwidgets bundle; tokens <= 2,000."""
    client = AnimationClient(SERVICE_URL)
    result = client.animate(conn, "PRE")
    assert result["status"] == "ok"
    assert "<!DOCTYPE html>" in result["html"] or "htmlwidgets" in result["html"]
    assert result["token_count"] <= 2000


def test_animation_cache_key():
    """Edge-case 4.3: cache key covers dataset_version and date range."""
    k1 = animation_cache_key("PRE", "2026-01-01", "2026-01-31")
    k2 = animation_cache_key("PRE", "2026-01-01", "2026-01-31")
    assert k1 == k2
    assert k1 != animation_cache_key("POST", "2026-01-01", "2026-01-31")
    assert k1 != animation_cache_key("PRE", "2026-01-02", "2026-01-31")


def test_animation_cache_hit(conn, monkeypatch):
    """Eval 3.3A: repeated /animate call with identical key is a <50ms cache hit."""
    calls = []

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"html": "<!DOCTYPE html><div></div>", "token_count": 10,
                    "total_cases": 10, "sampled": False}

    monkeypatch.setattr(requests, "post", lambda *a, **kw: (calls.append(1), FakeResp())[1])

    client = AnimationClient(SERVICE_URL)
    r1 = client.animate(conn, "PRE")
    t = time.perf_counter()
    r2 = client.animate(conn, "PRE")
    elapsed_ms = (time.perf_counter() - t) * 1000

    assert len(calls) == 1            # R service hit only once
    assert elapsed_ms < 50.0
    assert r1 == r2


def test_animation_timeout_fallback(conn, monkeypatch):
    """Eval 3.3B: container timeout -> graceful static-graph fallback (edge-case 4.2)."""
    def raise_timeout(*a, **kw):
        raise requests.ConnectTimeout("simulated 5s timeout")

    monkeypatch.setattr(requests, "post", raise_timeout)

    client = AnimationClient(SERVICE_URL, timeout=5)
    result = client.animate(conn, "PRE")

    assert result["status"] == "fallback"
    assert "static_graph" in result
    assert result["message"] == "Animation engine offline. Displaying static process graph."
