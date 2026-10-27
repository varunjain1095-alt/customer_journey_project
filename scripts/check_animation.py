"""
Render one combined-process animation and verify the embedded widget payload.

Usage:
    python scripts/check_animation.py PRE [outfile]

Prints: distinct session count, map activities, per-case start-time spread,
and total animated time span — the four acceptance numbers.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.animation_client import AnimationClient
from src.db import DatabaseManager

VERSION = sys.argv[1] if len(sys.argv) > 1 else "PRE"
OUT = sys.argv[2] if len(sys.argv) > 2 else "anim_check.html"


def main() -> int:
    conn = DatabaseManager("data/journeys.duckdb").get_connection()
    client = AnimationClient(timeout=300)
    res = client.animate(conn, VERSION)
    if res["status"] != "ok":
        print("RENDER FAILED:", res.get("message"))
        return 1
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(res["html"])
    print(f"wrote {OUT}  token_count={res['token_count']} "
          f"total_cases={res['total_cases']} sampled={res['sampled']}")

    blobs = re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', res["html"], re.S)
    for blob in blobs:
        try:
            x = json.loads(blob)
        except json.JSONDecodeError:
            continue
        inner = x.get("x", x)
        if "sizes" not in inner:
            continue
        cases = inner["sizes"]["case"]
        times = inner["sizes"]["time"]
        colors = sorted(set(inner["colors"]["value"]))
        tk = inner["tokens"]
        end = [s + d for s, d in zip(tk["token_start"], tk["token_duration"])]
        print("distinct sessions:", len(set(cases)))
        print("map activities:", inner["activities"]["act"])
        print("token colors:", colors)
        print("case starts: min %.2f  max %.2f  distinct %d" % (min(times), max(times), len(set(times))))
        print("animated span: %.2f s" % (max(end) - min(tk["token_start"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
