import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.database import init_db, get_conn
from app.routes import router



def _make_app():
    a = FastAPI()
    a.include_router(router)
    return a


client = TestClient(_make_app())


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db()
    return db_file


def seed_reading(city, timestamp, temp=20.0):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO readings
               (city, timestamp, temperature_2m, apparent_temperature,
                precipitation, wind_speed_10m, weather_code, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (city, timestamp, temp, temp - 2, 0.0, 10.0, 1, "2026-05-28T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def seed_event(
    city,
    event_type="rapid_temperature_change",
    severity="medium",
    detected_at="2026-05-28T00:00:00+00:00",
):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO events
               (city, event_type, description, severity, reading_id, reading_data, detected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (city, event_type, f"Test event for {city}", severity, None, "{}", detected_at),
        )
        conn.commit()
    finally:
        conn.close()


# /health

def test_health_empty_db():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "readings_stored": 0,
        "events_stored": 0,
    }


def test_health_counts_match_db():
    seed_reading("Ottawa", "2026-05-28T10:00")
    seed_reading("Toronto", "2026-05-28T10:00")
    seed_event("Ottawa")
    data = client.get("/health").json()
    assert data["status"] == "ok"
    assert data["readings_stored"] == 2
    assert data["events_stored"] == 1


# /readings

def test_readings_empty():
    response = client.get("/readings")
    assert response.status_code == 200
    assert response.json() == {"readings": []}


def test_readings_returns_all_fields():
    seed_reading("Ottawa", "2026-05-28T10:00", temp=12.5)
    body = client.get("/readings").json()
    assert len(body["readings"]) == 1
    row = body["readings"][0]
    for field in (
        "id",
        "city",
        "timestamp",
        "temperature_2m",
        "apparent_temperature",
        "precipitation",
        "wind_speed_10m",
        "weather_code",
        "fetched_at",
    ):
        assert field in row, f"missing field: {field}"
    assert row["city"] == "Ottawa"
    assert row["timestamp"] == "2026-05-28T10:00"
    assert row["temperature_2m"] == 12.5


def test_readings_city_filter():
    seed_reading("Ottawa", "2026-05-28T10:00")
    seed_reading("Toronto", "2026-05-28T10:00")
    body = client.get("/readings", params={"city": "Ottawa"}).json()
    assert len(body["readings"]) == 1
    assert body["readings"][0]["city"] == "Ottawa"


def test_readings_limit():
    for i in range(10):
        seed_reading("Ottawa", f"2026-05-28T{i:02d}:00")
    body = client.get("/readings", params={"limit": 3}).json()
    assert len(body["readings"]) == 3


def test_readings_most_recent_first():
    seed_reading("Ottawa", "2026-05-28T10:00", temp=10.0)
    seed_reading("Ottawa", "2026-05-28T11:00", temp=20.0)
    seed_reading("Ottawa", "2026-05-28T12:00", temp=30.0)
    body = client.get("/readings", params={"city": "Ottawa"}).json()
    assert [r["timestamp"] for r in body["readings"]] == [
        "2026-05-28T12:00",
        "2026-05-28T11:00",
        "2026-05-28T10:00",
    ]


# /events

def test_events_empty():
    response = client.get("/events")
    assert response.status_code == 200
    assert response.json() == {"events": []}


def test_events_returns_all_fields():
    seed_event("Ottawa", event_type="wind_advisory", severity="high")
    body = client.get("/events").json()
    assert len(body["events"]) == 1
    row = body["events"][0]
    for field in (
        "id",
        "city",
        "event_type",
        "description",
        "severity",
        "reading_id",
        "reading_data",
        "detected_at",
    ):
        assert field in row, f"missing field: {field}"
    assert row["city"] == "Ottawa"
    assert row["event_type"] == "wind_advisory"
    assert row["severity"] == "high"


def test_events_city_filter():
    seed_event("Ottawa")
    seed_event("Toronto")
    body = client.get("/events", params={"city": "Ottawa"}).json()
    assert len(body["events"]) == 1
    assert body["events"][0]["city"] == "Ottawa"


def test_events_limit():
    for _ in range(10):
        seed_event("Ottawa")
    body = client.get("/events", params={"limit": 3}).json()
    assert len(body["events"]) == 3


def test_events_most_recent_first():
    seed_event("Ottawa", detected_at="2026-05-28T10:00:00+00:00")
    seed_event("Ottawa", detected_at="2026-05-28T12:00:00+00:00")
    seed_event("Ottawa", detected_at="2026-05-28T11:00:00+00:00")
    body = client.get("/events").json()
    assert [e["detected_at"] for e in body["events"]] == [
        "2026-05-28T12:00:00+00:00",
        "2026-05-28T11:00:00+00:00",
        "2026-05-28T10:00:00+00:00",
    ]
