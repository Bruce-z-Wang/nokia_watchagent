import pytest
from app.database import init_db, get_conn
from app.events import check_for_events


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db()
    return db_file


def make_reading(city, timestamp, temp, precip=0.0, wind=10.0, weather_code=1):
    """Helper to insert a reading directly into the DB."""
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO readings
               (city, timestamp, temperature_2m, apparent_temperature,
                precipitation, wind_speed_10m, weather_code, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (city, timestamp, temp, temp - 2, precip, wind, weather_code, "2026-05-28T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def get_events(city=None):
    conn = get_conn()
    try:
        if city:
            rows = conn.execute(
                "SELECT * FROM events WHERE city = ?", (city,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM events").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


#rapid temperature change

def test_rapid_temperature_change_fires(db):
    # Mirror production: poller inserts the current reading before running detectors,
    # so the DB has both previous (10:00) and current (11:00) when check_for_events runs.
    make_reading("Ottawa", "2026-05-28T10:00", temp=10.0)
    make_reading("Ottawa", "2026-05-28T11:00", temp=15.5)
    fake_data = {"current": {
        "time": "2026-05-28T11:00",
        "temperature_2m": 15.5,   # 5.5C jump - above 4C threshold, below 7C high-severity threshold
        "apparent_temperature": 13.0,
        "precipitation": 0.0,
        "wind_speed_10m": 10.0,
        "weather_code": 1,
    }}
    check_for_events("Ottawa", fake_data)
    events = get_events("Ottawa")
    assert len(events) == 1
    assert events[0]["event_type"] == "rapid_temperature_change"
    assert events[0]["severity"] == "medium"


def test_rapid_temperature_change_does_not_fire_below_threshold(db):
    make_reading("Ottawa", "2026-05-28T10:00", temp=10.0)
    make_reading("Ottawa", "2026-05-28T11:00", temp=11.5)
    fake_data = {"current": {
        "time": "2026-05-28T11:00",
        "temperature_2m": 11.5,   # only 1.5C - below 4C threshold
        "apparent_temperature": 10.0,
        "precipitation": 0.0,
        "wind_speed_10m": 10.0,
        "weather_code": 1,
    }}
    check_for_events("Ottawa", fake_data)
    events = get_events("Ottawa")
    assert events == []


#precipitation onset

def test_precipitation_onset_fires(db):
    # weather_code held constant at 1 (clear) so the severe_weather detector
    # cannot fire on an unrelated code change; this isolates the precipitation logic.
    make_reading("Vancouver", "2026-05-28T10:00", temp=15.0, precip=0.0, weather_code=1)
    make_reading("Vancouver", "2026-05-28T11:00", temp=15.0, precip=1.2, weather_code=1)
    fake_data = {"current": {
        "time": "2026-05-28T11:00",
        "temperature_2m": 15.0,
        "apparent_temperature": 13.0,
        "precipitation": 1.2,
        "wind_speed_10m": 10.0,
        "weather_code": 1,
    }}
    check_for_events("Vancouver", fake_data)
    events = get_events("Vancouver")
    assert len(events) == 1
    assert events[0]["event_type"] == "precipitation_onset"
    assert events[0]["severity"] == "low"


def test_precipitation_onset_does_not_fire_when_already_raining(db):
    make_reading("Vancouver", "2026-05-28T10:00", temp=15.0, precip=2.0, weather_code=1)
    make_reading("Vancouver", "2026-05-28T11:00", temp=15.0, precip=1.5, weather_code=1)
    fake_data = {"current": {
        "time": "2026-05-28T11:00",
        "temperature_2m": 15.0,
        "apparent_temperature": 13.0,
        "precipitation": 1.5,
        "wind_speed_10m": 10.0,
        "weather_code": 1,
    }}
    check_for_events("Vancouver", fake_data)
    events = get_events("Vancouver")
    assert events == []


#wind advisory

def test_wind_advisory_fires(db):
    fake_data = {"current": {
        "time": "2026-05-28T11:00",
        "temperature_2m": 15.0,
        "apparent_temperature": 13.0,
        "precipitation": 0.0,
        "wind_speed_10m": 75.0,   # above 60 km/h, below 80 km/h - medium severity
        "weather_code": 1,
    }}
    check_for_events("Toronto", fake_data)
    events = get_events("Toronto")
    assert len(events) == 1
    assert events[0]["event_type"] == "wind_advisory"
    assert events[0]["severity"] == "medium"


def test_wind_advisory_does_not_fire_below_threshold(db):
    fake_data = {"current": {
        "time": "2026-05-28T11:00",
        "temperature_2m": 15.0,
        "apparent_temperature": 13.0,
        "precipitation": 0.0,
        "wind_speed_10m": 30.0,
        "weather_code": 1,
    }}
    check_for_events("Toronto", fake_data)
    events = get_events("Toronto")
    assert events == []


#severe weather

def test_severe_weather_fires_on_thunderstorm(db):
    fake_data = {"current": {
        "time": "2026-05-28T11:00",
        "temperature_2m": 20.0,
        "apparent_temperature": 18.0,
        "precipitation": 5.0,
        "wind_speed_10m": 40.0,
        "weather_code": 95,
    }}
    check_for_events("Ottawa", fake_data)
    events = get_events("Ottawa")
    assert len(events) == 1
    assert events[0]["event_type"] == "severe_weather"
    assert events[0]["severity"] == "high"


def test_severe_weather_does_not_fire_on_clear(db):
    fake_data = {"current": {
        "time": "2026-05-28T11:00",
        "temperature_2m": 20.0,
        "apparent_temperature": 18.0,
        "precipitation": 0.0,
        "wind_speed_10m": 10.0,
        "weather_code": 1,
    }}
    check_for_events("Ottawa", fake_data)
    events = get_events("Ottawa")
    assert events == []


#temperature anomaly
#
# The other tests above filter assertions to the detector under test because a
# strong anomaly will also trigger rapid_temperature_change (a 20C jump is both
# fast AND statistically extreme). That overlap is real production behaviour
# and not the thing we're testing here.

def test_temperature_anomaly_does_not_fire_with_insufficient_history(db):
    # 9 readings total < the detector's 10-reading guard, so even an extreme
    # current value should produce zero temperature_anomaly events.
    for i in range(8):
        make_reading("Ottawa", f"2026-05-28T{i:02d}:00", temp=10.0)
    make_reading("Ottawa", "2026-05-28T08:00", temp=30.0)
    fake_data = {"current": {
        "time": "2026-05-28T08:00",
        "temperature_2m": 30.0,
        "apparent_temperature": 28.0,
        "precipitation": 0.0,
        "wind_speed_10m": 10.0,
        "weather_code": 1,
    }}
    check_for_events("Ottawa", fake_data)
    anomaly_events = [e for e in get_events("Ottawa") if e["event_type"] == "temperature_anomaly"]
    assert anomaly_events == []


def test_temperature_anomaly_does_not_fire_when_reading_is_near_mean(db):
    # 11 stable readings around 10C with the current value also at 10C - z-score
    # is far below the 2.0 threshold so no anomaly should fire.
    for i in range(10):
        make_reading("Ottawa", f"2026-05-28T{i:02d}:00", temp=10.0 + (i % 2) * 0.1)
    make_reading("Ottawa", "2026-05-28T10:00", temp=10.0)
    fake_data = {"current": {
        "time": "2026-05-28T10:00",
        "temperature_2m": 10.0,
        "apparent_temperature": 8.0,
        "precipitation": 0.0,
        "wind_speed_10m": 10.0,
        "weather_code": 1,
    }}
    check_for_events("Ottawa", fake_data)
    events = get_events("Ottawa")
    assert events == []


def test_temperature_anomaly_fires_on_extreme_reading(db):
    # 20 stable readings around 10C followed by a 30C current value. With that
    # much history dominating the mean, z >> 3 and the detector should fire at
    # high severity.
    for i in range(20):
        make_reading("Ottawa", f"2026-05-28T{i:02d}:00", temp=10.0 + (i % 3) * 0.1)
    make_reading("Ottawa", "2026-05-28T20:00", temp=30.0)
    fake_data = {"current": {
        "time": "2026-05-28T20:00",
        "temperature_2m": 30.0,
        "apparent_temperature": 28.0,
        "precipitation": 0.0,
        "wind_speed_10m": 10.0,
        "weather_code": 1,
    }}
    check_for_events("Ottawa", fake_data)
    anomaly_events = [e for e in get_events("Ottawa") if e["event_type"] == "temperature_anomaly"]
    assert len(anomaly_events) == 1
    assert anomaly_events[0]["severity"] == "high"