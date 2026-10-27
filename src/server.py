"""
FastAPI backend for the Customer Journey Analytics Dashboard (Phase 4).

Serves the React build (frontend/dist) and JSON endpoints over the Phase 1/2
analytics modules. All data endpoints are filtered by dataset_version.
"""
import os
import threading

import pandas as pd
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.analytics import (
    assemble_event_log,
    assemble_event_log_lazy,
    build_dfg,
    compute_edge_timeline_changes,
    compute_funnel,
    compute_trace_signatures,
    compute_value_defaults,
    get_variants,
    profile_edge_segment,
    profile_segment,
    rank_variants,
)
from src.animation_client import AnimationClient
from src.db import DatabaseManager

DB_PATH = os.environ.get("CJ_DB_PATH", "data/journeys.duckdb")
CASE_IDS = {"journey_id", "user_session"}


class AnimateRequest(BaseModel):
    dataset_version: str
    path: list[str] | None = None  # legacy field, ignored: animation is whole-process
    start_date: str | None = None
    end_date: str | None = None


class StarRequest(BaseModel):
    path_id: str
    dataset_version: str
    pm_user_id: str = "pm_default"


class HypothesisRequest(BaseModel):
    edge_key: str
    dataset_version: str = "PRE"
    hypothesis: str
    experiment_url: str = ""
    baseline_lost_volume: int | None = None
    baseline_edge_count: int | None = None
    baseline_dropoff_pct: float | None = None
    pm_user_id: str = "pm_default"


class HypothesisDeleteRequest(BaseModel):
    edge_key: str
    dataset_version: str = "PRE"
    pm_user_id: str = "pm_default"


def create_app(db_path: str = DB_PATH) -> FastAPI:
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    db_mgr = DatabaseManager(db_path)
    conn = db_mgr.get_connection()
    anim_client = AnimationClient()

    app = FastAPI(title="Customer Journey Analytics Dashboard")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.conn = conn
    # Per-(version, case_id) caches: mining is the expensive step, ranking is cheap
    app.state.variant_cache = {}
    app.state.pd_log_cache = {}
    app.state.sig_cache = {}
    app.state.dfg_cache = {}
    app.state.segment_cache = {}
    app.state.edge_segment_cache = {}
    app.state.value_defaults_cache = {}
    app.state.timeline_cache = {}
    app.state.warm = False
    # DuckDB connections are not thread-safe: the warmup thread and request
    # handlers must serialize, or concurrent queries contaminate each other.
    app.state.db_lock = threading.Lock()

    def get_variants_cached(dataset_version: str, case_id: str):
        key = (dataset_version, case_id)
        if key not in app.state.variant_cache:
            with app.state.db_lock:
                log = assemble_event_log_lazy(conn, dataset_version, case_id)
            variants = get_variants(log)
            if variants:  # never cache an empty result: data may still be loading
                app.state.variant_cache[key] = variants
            return variants
        return app.state.variant_cache[key]

    def get_pd_log(dataset_version: str, case_id: str) -> pd.DataFrame:
        key = (dataset_version, case_id)
        if key not in app.state.pd_log_cache:
            with app.state.db_lock:
                log = assemble_event_log(conn, dataset_version, case_id)
            if not log.empty:
                app.state.pd_log_cache[key] = log
            return log
        return app.state.pd_log_cache[key]

    def get_sigs(dataset_version: str, case_id: str) -> pd.DataFrame:
        """Cached per-trace signature table: computing it scans all events once."""
        key = (dataset_version, case_id)
        if key not in app.state.sig_cache:
            with app.state.db_lock:
                sigs = compute_trace_signatures(conn, dataset_version, case_id)
            if not sigs.empty:
                app.state.sig_cache[key] = sigs
            return sigs
        return app.state.sig_cache[key]

    def get_value_defaults(dataset_version: str) -> dict:
        if dataset_version not in app.state.value_defaults_cache:
            with app.state.db_lock:
                val = compute_value_defaults(conn, dataset_version)
            if val["purchase_count"] > 0:
                app.state.value_defaults_cache[dataset_version] = val
            return val
        return app.state.value_defaults_cache[dataset_version]

    def get_timeline_changes(dataset_version: str, case_id: str) -> dict:
        key = (dataset_version, case_id)
        if key not in app.state.timeline_cache:
            with app.state.db_lock:
                tl = compute_edge_timeline_changes(conn, dataset_version, case_id)
            if tl.get("changes"):
                app.state.timeline_cache[key] = tl
            return tl
        return app.state.timeline_cache[key]

    def _warmup():
        """Fill every cold-start cache in the background so the first UI request
        isn't blocked on PM4Py import + 1.7M-row scans. On fresh cloud deploys
        (e.g. Railway where the 318MB .duckdb file is gitignored), seed the DB
        automatically before warming caches."""
        try:
            if db_path != ":memory:":
                with app.state.db_lock:
                    cust_cnt = conn.execute("SELECT COUNT(*) FROM CUSTOMERS").fetchone()[0]
                if cust_cnt == 0:
                    from src.generator import SyntheticDatasetGenerator
                    sessions_per_ver = int(os.environ.get("CJ_SESSIONS_PER_VERSION", "300000"))
                    with app.state.db_lock:
                        gen = SyntheticDatasetGenerator(conn, seed=42)
                        gen.generate_shared_dimensions(num_customers=20000, num_products=500)
                        for ver in ("PRE", "POST"):
                            gen.generate_event_stream(dataset_version=ver, total_sessions=sessions_per_ver)
            for ver in ("PRE", "POST"):
                get_variants_cached(ver, "user_session")
                get_pd_log(ver, "user_session")
                get_sigs(ver, "user_session")
                get_value_defaults(ver)
                get_timeline_changes(ver, "user_session")
                base_graph = build_dfg(app.state.pd_log_cache[(ver, "user_session")], 1.0, "full")
                base_graph["value_defaults"] = get_value_defaults(ver)
                base_graph["timeline"] = get_timeline_changes(ver, "user_session")
                app.state.dfg_cache[(ver, "user_session", 1.0, "full")] = base_graph
            app.state.warm = True
        except Exception:
            pass  # caches still fill lazily on demand

    threading.Thread(target=_warmup, daemon=True).start()

    @app.get("/api/health")
    def health():
        return {"status": "ok", "db_path": db_path, "warm": app.state.warm}

    @app.get("/api/variants")
    def variants(dataset_version: str = "PRE", case_id: str = "user_session", top_n: int = 10):
        if case_id not in CASE_IDS:
            return {"error": f"case_id must be one of {sorted(CASE_IDS)}"}
        return rank_variants(get_variants_cached(dataset_version, case_id), top_n)

    @app.get("/api/dfg")
    def dfg(dataset_version: str = "PRE", case_id: str = "user_session",
            coverage: float = 0.60, granularity: str = "full"):
        if case_id not in CASE_IDS:
            return {"error": f"case_id must be one of {sorted(CASE_IDS)}"}
        key = (dataset_version, case_id, coverage, granularity)
        if key not in app.state.dfg_cache:
            graph = build_dfg(get_pd_log(dataset_version, case_id), coverage, granularity)
            graph["value_defaults"] = get_value_defaults(dataset_version)
            graph["timeline"] = get_timeline_changes(dataset_version, case_id)
            if graph["nodes"]:  # never cache an empty result: data may still be loading
                app.state.dfg_cache[key] = graph
            return graph
        cached = app.state.dfg_cache[key]
        if "value_defaults" not in cached:
            cached["value_defaults"] = get_value_defaults(dataset_version)
        if "timeline" not in cached:
            cached["timeline"] = get_timeline_changes(dataset_version, case_id)
        return cached

    @app.get("/api/funnel")
    def funnel(dataset_version: str = "PRE", case_id: str = "user_session",
               path: list[str] = Query(default=[])):
        if not path:
            return {"error": "path is required"}
        return compute_funnel(get_variants_cached(dataset_version, case_id), path)

    @app.get("/api/segment")
    def segment(dataset_version: str = "PRE", case_id: str = "user_session",
                path: list[str] = Query(default=[]),
                edge_source: str | None = None,
                edge_target: str | None = None):
        if case_id not in CASE_IDS:
            return {"error": f"case_id must be one of {sorted(CASE_IDS)}"}
        # Feature 2: Extend /api/segment so it can break down a selected edge
        # by WHO (device_type, traffic_source, membership_status, country, age_bucket),
        # either standalone or alongside a path cohort profile.
        edge_breakdown = None
        if edge_source and edge_target:
            ekey = (dataset_version, case_id, edge_source, edge_target)
            if ekey not in app.state.edge_segment_cache:
                with app.state.db_lock:
                    eb = profile_edge_segment(conn, dataset_version, edge_source, edge_target, case_id)
                if eb["source_total"] > 0:
                    app.state.edge_segment_cache[ekey] = eb
                edge_breakdown = eb
            else:
                edge_breakdown = app.state.edge_segment_cache[ekey]
            if not path:
                return edge_breakdown

        if not path:
            return {"error": "path or (edge_source, edge_target) is required"}
        key = (dataset_version, case_id, tuple(path))
        if key not in app.state.segment_cache:
            with app.state.db_lock:
                profile = profile_segment(
                    conn, dataset_version, path, case_id, get_sigs(dataset_version, case_id))
            if profile["cohort_size"] > 0:
                app.state.segment_cache[key] = profile
        else:
            profile = app.state.segment_cache[key]
        if edge_breakdown is not None:
            return {**profile, "edge_breakdown": edge_breakdown}
        return profile

    @app.post("/api/animate")
    def animate(req: AnimateRequest):
        with app.state.db_lock:
            return anim_client.animate(conn, req.dataset_version,
                                       req.start_date, req.end_date)

    @app.get("/api/starred")
    def starred(dataset_version: str = "PRE", pm_user_id: str = "pm_default"):
        with app.state.db_lock:
            rows = conn.execute(
                "SELECT path_id, dataset_version, starred_at FROM STARRED_PATHS WHERE pm_user_id = ?",
                [pm_user_id],
            ).fetchall()
        return {"starred": [
            {"path_id": r[0], "dataset_version": r[1], "starred_at": str(r[2])}
            for r in rows
        ]}

    @app.post("/api/star")
    def star(req: StarRequest):
        with app.state.db_lock:
            conn.execute(
                "INSERT OR REPLACE INTO STARRED_PATHS (pm_user_id, path_id, dataset_version, starred_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                [req.pm_user_id, req.path_id, req.dataset_version],
            )
        return {"starred": True, "path_id": req.path_id, "dataset_version": req.dataset_version}

    @app.delete("/api/star")
    def unstar(req: StarRequest):
        with app.state.db_lock:
            conn.execute(
                "DELETE FROM STARRED_PATHS WHERE pm_user_id = ? AND path_id = ? AND dataset_version = ?",
                [req.pm_user_id, req.path_id, req.dataset_version],
            )
        return {"starred": False, "path_id": req.path_id, "dataset_version": req.dataset_version}

    @app.get("/api/hypotheses")
    def list_hypotheses(dataset_version: str = "PRE", pm_user_id: str = "pm_default"):
        with app.state.db_lock:
            rows = conn.execute(
                """
                SELECT edge_key, dataset_version, hypothesis, experiment_url,
                       baseline_lost_volume, baseline_edge_count, baseline_dropoff_pct, updated_at
                FROM LEAK_HYPOTHESES
                WHERE pm_user_id = ? AND dataset_version = ?
                ORDER BY updated_at DESC
                """,
                [pm_user_id, dataset_version],
            ).fetchall()

        # Enrich with current PRE vs POST ("before vs after") numbers from DFG
        pre_dfg = app.state.dfg_cache.get(("PRE", "user_session", 1.0, "full"))
        post_dfg = app.state.dfg_cache.get(("POST", "user_session", 1.0, "full"))
        pre_map = {f"{e['source']}→{e['target']}": e for e in (pre_dfg or {}).get("edges", [])}
        post_map = {f"{e['source']}→{e['target']}": e for e in (post_dfg or {}).get("edges", [])}

        items = []
        for r in rows:
            ekey = r[0]
            pre_e = pre_map.get(ekey, {})
            post_e = post_map.get(ekey, {})
            before_lost = r[4] if r[4] is not None else pre_e.get("lost_volume")
            before_cnt = r[5] if r[5] is not None else pre_e.get("count")
            before_drop_pct = r[6] if r[6] is not None else pre_e.get("dropoff_pct")
            after_lost = post_e.get("lost_volume", pre_e.get("lost_volume"))
            after_cnt = post_e.get("count", pre_e.get("count"))
            after_drop_pct = post_e.get("dropoff_pct", pre_e.get("dropoff_pct"))
            items.append({
                "edge_key": ekey,
                "dataset_version": r[1],
                "hypothesis": r[2],
                "experiment_url": r[3] or f"https://experiments.internal/exp/{ekey.replace('→', '-')}",
                "before_lost_volume": before_lost,
                "before_edge_count": before_cnt,
                "before_dropoff_pct": before_drop_pct,
                "after_lost_volume": after_lost,
                "after_edge_count": after_cnt,
                "after_dropoff_pct": after_drop_pct,
                "updated_at": str(r[7]),
            })
        return {"hypotheses": items}

    @app.post("/api/hypotheses")
    def save_hypothesis(req: HypothesisRequest):
        exp_url = req.experiment_url.strip() if req.experiment_url else (
            f"https://experiments.internal/exp/{req.edge_key.replace('→', '-')}"
        )
        with app.state.db_lock:
            conn.execute(
                """
                INSERT OR REPLACE INTO LEAK_HYPOTHESES (
                    pm_user_id, edge_key, dataset_version, hypothesis, experiment_url,
                    baseline_lost_volume, baseline_edge_count, baseline_dropoff_pct, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                [
                    req.pm_user_id,
                    req.edge_key,
                    req.dataset_version,
                    req.hypothesis,
                    exp_url,
                    req.baseline_lost_volume,
                    req.baseline_edge_count,
                    req.baseline_dropoff_pct,
                ],
            )
        return {"saved": True, "edge_key": req.edge_key, "experiment_url": exp_url}

    @app.delete("/api/hypotheses")
    def delete_hypothesis(req: HypothesisDeleteRequest):
        with app.state.db_lock:
            conn.execute(
                "DELETE FROM LEAK_HYPOTHESES WHERE pm_user_id = ? AND edge_key = ? AND dataset_version = ?",
                [req.pm_user_id, req.edge_key, req.dataset_version],
            )
        return {"deleted": True, "edge_key": req.edge_key}

    # Serve the React build when present
    dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    if os.path.isdir(dist):
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    return app


# Lazy factory: run with: python -m src.server (or uvicorn --factory src.server:create_app)
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8001"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(create_app(), host=host, port=port)
