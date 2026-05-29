import pytest
from app.database import init_db, get_conn
from app.poller import store_reading


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db()
    return db_file


def test_duplicate_reading_not_stored(db):
    fake_data = {
        "current": {
            "time": "2026-05-28T12:00",
            "temperature_2m": 20.0,
            "apparent_temperature": 18.0,
            "precipitation": 0.0,
            "wind_speed_10m": 10.0,
            "weather_code": 1,
        }
    }

    first = store_reading("Ottawa", fake_data)
    second = store_reading("Ottawa", fake_data)

    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    conn.close()

    assert first == True   # first insert succeeded
    assert second == False  # duplicate was ignored
    assert count == 1       #should be only one row in DB

def test_poller_dedupes_when_api_returns_same_reading_twice(db, monkeypatch):
    fake_response = {"current": {
        "time": "2026-05-28T12:00",
        "temperature_2m": 20.0, "apparent_temperature": 18.0,
        "precipitation": 0.0, "wind_speed_10m": 10.0, "weather_code": 1,
    }}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return fake_response

    calls = {"n": 0}
    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr("app.poller.httpx.get", fake_get)


    from app.poller import fetch_weather, store_reading, CITIES
    ottawa = CITIES[0]
    store_reading(ottawa["name"], fetch_weather(ottawa))
    store_reading(ottawa["name"], fetch_weather(ottawa))

    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM readings WHERE city = 'Ottawa'").fetchone()[0]
    conn.close()
    assert calls["n"] == 2       
    assert count == 1
def test_missing_timestamp_returns_false_and_stores_nothing(db):
    bad = {"current": {"temperature_2m": 20.0}}  # no 'time' key
    assert store_reading("Ottawa", bad) is False
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    conn.close()
    assert count == 0