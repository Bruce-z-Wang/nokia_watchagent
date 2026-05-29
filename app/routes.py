from fastapi import APIRouter, Query
from app.database import get_conn


router = APIRouter()

@router.get("/health")
def health():
    conn = get_conn()
    try:
        readings = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return {"status": "ok", "readings_stored": readings, "events_stored": events}
    finally:
        conn.close()

@router.get("/readings")
def get_readings(city: str | None = Query(None), limit: int = Query(50, ge=1)):
    conn = get_conn()
    try:
        if city:
            readings = conn.execute("SELECT * FROM readings WHERE city = ? ORDER BY timestamp DESC LIMIT ?", (city, limit)).fetchall()
        else:
            readings = conn.execute("SELECT * FROM readings ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return {"readings": [dict(row) for row in readings]}
    finally:
        conn.close()

@router.get("/events")
def get_events(city: str | None = Query(None), limit: int = Query(50, ge=1)):
    conn = get_conn()
    try:
        if city:
            events = conn.execute("SELECT * FROM events WHERE city = ? ORDER BY detected_at DESC LIMIT ?", (city, limit)).fetchall()
        else:
            events = conn.execute("SELECT * FROM events ORDER BY detected_at DESC LIMIT ?", (limit,)).fetchall()
        return {"events": [dict(row) for row in events]}
    finally:
        conn.close()
