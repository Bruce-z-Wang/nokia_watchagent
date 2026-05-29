import json
import logging
from datetime import datetime, timezone
from app.database import get_conn

logger = logging.getLogger(__name__)


# helper function to create an event

def get_recent_readings(city: str, limit: int = 48) -> list[dict]:
    conn = get_conn()
    try:
        readings = conn.execute("SELECT * FROM readings WHERE city = ? ORDER BY timestamp DESC LIMIT ?", (city, limit)).fetchall()
        return [dict(r) for r in readings]
    finally:
        conn.close()
def get_latest_reading(city: str) -> int | None:
    conn = get_conn()
    try:
        reading = conn.execute("SELECT id FROM readings WHERE city = ? ORDER BY timestamp DESC LIMIT 1",(city,), ).fetchone()
        return reading[0] if reading else None
    finally:
        conn.close()

def store_event(city, event_type, description, severity, reading_id, metadata: dict):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO events
                (city, event_type, description, severity, reading_id, reading_data, detected_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city,
                event_type,
                description,
                severity,
                reading_id,
                json.dumps(metadata),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        logger.info(f"Event stored: {event_type} for city {city}")
    finally:
        conn.close()

def check_temperature_anomaly(city:str, current:dict, reading_id:int):
    readings = get_recent_readings(city)
    if len(readings) < 10:
        return
    temps = [r["temperature_2m"] for r in readings if r["temperature_2m"] is not None]
    mean = sum(temps) / len(temps)
    std = (sum((t - mean) ** 2 for t in temps) / len(temps)) ** 0.5
    if std == 0:
        return
    current_temp = current.get("temperature_2m")
    z_score = abs(current_temp - mean) / std
    if z_score > 2:
        severity = "high" if z_score > 3 else "medium"
        store_event(
            city=city,
            event_type="temperature_anomaly",
            description=f"{city} temperature {current_temp}°C is {z_score:.1f} standard deviations from its average of {mean:.1f}°C",
            severity=severity,
            reading_id=reading_id,
            metadata={"temperature": current_temp, "mean": round(mean, 2), "std": round(std, 2), "z_score": round(z_score, 2)},
        )
# 4 degrees celsius is significant weather change 7 degrees celsius earns a high severity and these are my design choices for severity
def check_rapid_temperature_change(city:str, current: dict, reading_id: int):
    readings = get_recent_readings(city, limit=2)
    if len(readings) < 2:
        return
    previous_temp = readings[1]["temperature_2m"]
    current_temp = current.get("temperature_2m")
    delta = abs(current_temp - previous_temp)
    if delta >= 4:
        severity = "high" if delta >= 7 else "medium"
        store_event(
            city=city,
            event_type="rapid_temperature_change",
            description=f"{city} temperature changed {delta:.1f}°C in one hour (from {previous_temp}°C to {current_temp}°C)",
            severity=severity,
            reading_id=reading_id,
            metadata={"previous": previous_temp, "current": current_temp, "delta": round(delta, 2)},
        )
def check_precipitation_onset(city: str, current: dict, reading_id: int):
    readings = get_recent_readings(city, limit=2)
    if len(readings) < 2:
        return
    prev_precip = readings[1]["precipitation"]
    curr_precip = current.get("precipitation", 0)

    if prev_precip is None:
        return

    if prev_precip == 0 and curr_precip > 0.5:
        store_event(
            city=city,
            event_type="precipitation_onset",
            description=f"Precipitation started in {city}: {curr_precip}mm recorded after dry period",
            severity="low",
            reading_id=reading_id,
            metadata={"previous_mm": prev_precip, "current_mm": curr_precip},
        )
    elif prev_precip > 0.5 and curr_precip == 0:
        store_event(
            city=city,
            event_type="precipitation_cessation",
            description=f"Precipitation stopped in {city}: was {prev_precip}mm, now dry",
            severity="low",
            reading_id=reading_id,
            metadata={"previous_mm": prev_precip, "current_mm": curr_precip},
        )

SEVERE_CODES = {
    range(51, 68): "moderate",
    range(71, 78): "moderate",
    range(80, 83): "moderate",
    range(85, 87): "moderate",
    range(95, 100): "high",
}

def get_severity_for_code(code: int) -> str | None:
    for code_range, severity in SEVERE_CODES.items():
        if code in code_range:
            return severity
    return None
def check_severe_weather(city: str, current: dict, reading_id: int):
    readings = get_recent_readings(city, limit=2)
    current_code = current.get("weather_code")
    prev_code = readings[1]["weather_code"] if len(readings) >= 2 else None
    severity = get_severity_for_code(current_code)
    prev_severity = get_severity_for_code(prev_code) if prev_code else None
    if severity and severity != prev_severity:
        store_event(
            city=city,
            event_type="severe_weather",
            description=f"Severe weather in {city}: WMO code {current_code}",
            severity=severity,
            reading_id = reading_id,
            metadata={"weather_code": current_code, "previous_code": prev_code},
        )
def check_wind_advisory(city: str, current:dict, reading_id: int):
    wind = current.get("wind_speed_10m", 0)
    if wind < 60:
        return
    conn = get_conn()
    try:
        recent = conn.execute("""
            SELECT * FROM events WHERE city = ? AND event_type = 'wind_advisory' 
            ORDER BY detected_at DESC LIMIT 1
         """, (city,),).fetchone()
    finally:
        conn.close()
    if recent:
        return
    store_event(
        city=city,
        event_type="wind_advisory",
        description=f"High winds in {city}: {wind}km/h",
        severity="medium" if wind < 80 else "high",
        reading_id=reading_id,
        metadata={"wind_speed_kmh": wind}  
    )

def check_for_events(city: str, data: dict):
    current = data.get("current", {})
    reading_id = get_latest_reading(city)

    check_temperature_anomaly(city, current, reading_id)
    check_rapid_temperature_change(city, current, reading_id)
    check_precipitation_onset(city, current, reading_id)
    check_severe_weather(city, current, reading_id)
    check_wind_advisory(city, current, reading_id)

