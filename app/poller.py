import httpx
import json
import logging
from datetime import datetime, timezone
from database import get_conn

logger = logging.getLogger(__name__)

CITIES = [
    {"name": "Ottawa",    "lat": 45.42, "lon": -75.69},
    {"name": "Toronto",   "lat": 43.70, "lon": -79.42},
    {"name": "Vancouver", "lat": 49.25, "lon": -123.12},
]

def fetch_weather(city: dict) -> dict | None:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "current": "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code",
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    }
    try:
        resp = httpx.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning(f"Poll failed for {city['name']}: HTTP {e.response.status_code}")
        return None
    except Exception as e:
        logger.warning(f"Poll failed for {city['name']}: {e}")
        return None

def store_reading(city_name: str, data: dict) -> bool:
    current = data.get("current", {})
    timestamp = current.get("time")
    if not timestamp:
        return False

    conn = get_conn()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO readings
              (city, timestamp, temperature_2m, apparent_temperature,
               precipitation, wind_speed_10m, weather_code, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            city_name,
            timestamp,
            current.get("temperature_2m"),
            current.get("apparent_temperature"),
            current.get("precipitation"),
            current.get("wind_speed_10m"),
            current.get("weather_code"),
            datetime.now(timezone.utc).isoformat(),
        ))
        is_new = conn.execute("SELECT changes()").fetchone()[0] == 1
        conn.commit()
        return is_new
    finally:
        conn.close()

def poll_all():
    from events import check_for_events
    logger.info("Polling weather for all cities...")
    for city in CITIES:
        data = fetch_weather(city)
        if data:
            is_new = store_reading(city["name"], data)
            if is_new:
                logger.info(f"New reading stored for {city['name']}")
                check_for_events(city["name"], data)
            else:
                logger.info(f"Duplicate reading skipped for {city['name']}")