#!/usr/bin/env python3
"""
Skill: analyze.py

Query the WatchAgent SQLite database to answer analytical questions about
stored readings and events: per-city statistics, time-window summaries,
event counts, temperature trends, and cross-city comparisons.

Usage:
    python .cursor/skills/analyze-weather-data/scripts/analyze.py
    python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "summary"
    python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "average temperature per city"
    python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "which city had the most events"
    python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "recent events" --hours 24
    python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "temperature trend"
    python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "compare cities"

The script honours DB_PATH from the environment, defaulting to weather.db in
the current working directory.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone


def get_conn():
    db_path = os.getenv("DB_PATH", "weather.db")
    if not os.path.exists(db_path):
        print(json.dumps({
            "error": f"Database not found at {db_path}",
            "hint": "Has the poller run yet? Set DB_PATH if the database lives elsewhere.",
        }, indent=2))
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def summary():
    conn = get_conn()
    try:
        return {
            "total_readings": conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0],
            "total_events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "cities_monitored": [r[0] for r in conn.execute(
                "SELECT DISTINCT city FROM readings ORDER BY city"
            )],
            "earliest_reading": conn.execute(
                "SELECT MIN(timestamp) FROM readings"
            ).fetchone()[0],
            "latest_reading": conn.execute(
                "SELECT MAX(timestamp) FROM readings"
            ).fetchone()[0],
            "events_by_type": dict(conn.execute(
                "SELECT event_type, COUNT(*) FROM events GROUP BY event_type"
            ).fetchall()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()


def avg_temperature_per_city():
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT city,
                   ROUND(AVG(temperature_2m), 2) AS avg_temp,
                   ROUND(MIN(temperature_2m), 2) AS min_temp,
                   ROUND(MAX(temperature_2m), 2) AS max_temp,
                   ROUND(SUM(precipitation), 2) AS total_precip_mm,
                   ROUND(MAX(wind_speed_10m), 2) AS max_wind_kmh,
                   COUNT(*) AS reading_count
            FROM readings
            GROUP BY city
            ORDER BY avg_temp DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def events_per_city():
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT city, event_type, severity, COUNT(*) AS count
            FROM events
            GROUP BY city, event_type, severity
            ORDER BY city, count DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def recent_readings(limit=10):
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT city, timestamp, temperature_2m, precipitation,
                   wind_speed_10m, weather_code
            FROM readings
            ORDER BY timestamp DESC, city
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def recent_events(hours=None, limit=20):
    """If `hours` is set, return all events in that window. Otherwise the last `limit` events."""
    conn = get_conn()
    try:
        if hours is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            rows = conn.execute("""
                SELECT city, event_type, severity, description, detected_at
                FROM events
                WHERE detected_at >= ?
                ORDER BY detected_at DESC
            """, (cutoff,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT city, event_type, severity, description, detected_at
                FROM events
                ORDER BY detected_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def temperature_trend_per_city(per_city_limit=12):
    """Last N readings per city with delta from previous reading."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            WITH ranked AS (
                SELECT city, timestamp, temperature_2m,
                       ROW_NUMBER() OVER (PARTITION BY city ORDER BY timestamp DESC) AS rn,
                       LAG(temperature_2m) OVER (PARTITION BY city ORDER BY timestamp) AS prev_temp
                FROM readings
            )
            SELECT city, timestamp, temperature_2m,
                   CASE WHEN prev_temp IS NULL THEN NULL
                        ELSE ROUND(temperature_2m - prev_temp, 2) END AS delta_from_prev
            FROM ranked
            WHERE rn <= ?
            ORDER BY city, timestamp DESC
        """, (per_city_limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def compare_cities():
    """Latest reading per city, side-by-side."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT r.city, r.timestamp, r.temperature_2m, r.apparent_temperature,
                   r.precipitation, r.wind_speed_10m, r.weather_code
            FROM readings r
            INNER JOIN (
                SELECT city, MAX(timestamp) AS latest
                FROM readings
                GROUP BY city
            ) m ON r.city = m.city AND r.timestamp = m.latest
            ORDER BY r.city
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def route(question, hours):
    q = (question or "").lower()
    if "summary" in q:
        return {"summary": summary()}
    if "trend" in q and "temperature" in q:
        return {"temperature_trends": temperature_trend_per_city()}
    if "compare" in q or "side" in q or "latest" in q:
        return {"compare_cities": compare_cities()}
    if "temperature" in q or "avg" in q or "average" in q:
        return {"avg_temperature_per_city": avg_temperature_per_city()}
    if "event" in q and ("most" in q or "count" in q or "per city" in q):
        return {"events_per_city": events_per_city()}
    if "event" in q:
        return {"recent_events": recent_events(hours=hours)}
    if "recent" in q or "reading" in q:
        return {"recent_readings": recent_readings()}
    return {
        "matched_question": "fallback - no specific keyword matched, returning all sections",
        "summary": summary(),
        "avg_temperature_per_city": avg_temperature_per_city(),
        "events_per_city": events_per_city(),
        "recent_events": recent_events(hours=hours),
    }


def main():
    parser = argparse.ArgumentParser(description="Analyze WatchAgent weather data")
    parser.add_argument(
        "--question",
        type=str,
        default="summary",
        help='Natural-language question, e.g. "summary", "average temperature per city", "recent events", "temperature trend", "compare cities"',
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="Optional time window in hours (applies to event queries)",
    )
    args = parser.parse_args()
    print(json.dumps(route(args.question, args.hours), indent=2, default=str))


if __name__ == "__main__":
    main()
