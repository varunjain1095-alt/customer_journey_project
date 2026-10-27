"""
Python client for the R Plumber animation microservice (Phase 3).

Combined-process replay: one animation per dataset_version animates sampled
sessions across the whole process map. Tokens are colored by outcome:
'converted' (reached purchase) vs 'dropped': so abandonment is visible.
"""
import hashlib
from collections import OrderedDict
from datetime import datetime

import requests
from duckdb import DuckDBPyConnection

DEFAULT_ANIMATE_URL = "http://localhost:8000"
REQUEST_TIMEOUT_SECONDS = 5
MAX_SESSIONS_PER_REQUEST = 4_000
FALLBACK_MESSAGE = "Animation engine offline. Displaying static process graph."


def animation_cache_key(dataset_version: str,
                        start_date: str | None = None, end_date: str | None = None) -> str:
    """LRU cache key including all filter parameters (edge-case 4.3)."""
    raw = "|".join([str(dataset_version), str(start_date or ""), str(end_date or "")])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class AnimationClient:
    def __init__(self, base_url: str = DEFAULT_ANIMATE_URL,
                 cache_size: int = 128, timeout: float = REQUEST_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cache_size = cache_size
        self._cache: OrderedDict[str, dict] = OrderedDict()

    def _fetch_events_payload(self, conn: DuckDBPyConnection, dataset_version: str,
                              max_sessions: int,
                              start_date=None, end_date=None) -> list[dict]:
        """
        All session events, deterministically sampled to `max_sessions`.
        'converted' = session reached purchase; 'dropped' = exited earlier.
        """
        params = [dataset_version] * 3
        date_filter = ""
        if start_date is not None:
            date_filter += " AND e.event_time >= ?"
            params.append(start_date)
        if end_date is not None:
            date_filter += " AND e.event_time <= ?"
            params.append(end_date)

        rows = conn.execute(f"""
            WITH all_events AS (
                SELECT user_session AS sess, event_type, event_time, event_id FROM TRANSACTION_EVENTS
                WHERE dataset_version = ?
                UNION ALL
                SELECT user_session AS sess, event_type, event_time, event_id FROM SEARCH_FILTER_EVENTS
                WHERE dataset_version = ?
                UNION ALL
                SELECT session_id AS sess, event_type, event_time, event_id FROM PAGE_ENGAGEMENT_EVENTS
                WHERE dataset_version = ?
            ),
            flags AS (
                SELECT sess, MAX(event_type = 'purchase') AS converted
                FROM all_events GROUP BY sess
            )
            SELECT e.sess, e.event_type, e.event_time, e.event_id, f.converted
            FROM all_events e JOIN flags f ON e.sess = f.sess
            WHERE e.sess IN (
                SELECT sess FROM flags ORDER BY hash(sess) LIMIT {int(max_sessions)}
            ){date_filter}
            ORDER BY e.sess, e.event_time, e.event_id
        """, params).fetchall()

        return [{"case_id": s,
                 "highlighted": "converted" if conv else "dropped",
                 "event_id": eid, "activity": et,
                 "timestamp": t.isoformat() if isinstance(t, datetime) else str(t)}
                for s, et, t, eid, conv in rows]

    def animate(self, conn: DuckDBPyConnection, dataset_version: str,
                start_date=None, end_date=None,
                max_sessions: int = MAX_SESSIONS_PER_REQUEST) -> dict:
        """
        Returns {'status': 'ok', 'html', 'token_count', 'sampled'} on success, or a
        graceful static-graph fallback payload on timeout/connection failure (edge-case 4.2).
        Results are cached by MD5(dataset_version | start | end).
        """
        key = animation_cache_key(dataset_version, start_date, end_date)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        payload = {
            "dataset_version": dataset_version,
            "events": self._fetch_events_payload(conn, dataset_version, max_sessions,
                                                 start_date, end_date),
        }
        try:
            resp = requests.post(f"{self.base_url}/animate", json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            result = {
                "status": "ok",
                "html": data["html"],
                "token_count": data["token_count"],
                "total_cases": data["total_cases"],
                "sampled": data["sampled"],
            }
        except requests.RequestException:
            # Graceful fallback: static DFG graph JSON instead of the animation
            from src.analytics import assemble_event_log, build_dfg
            static = build_dfg(assemble_event_log(conn, dataset_version))
            result = {"status": "fallback", "static_graph": static, "message": FALLBACK_MESSAGE}

        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return result
