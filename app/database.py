import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "weather.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                temperature_2m REAL,
                apparent_temperature REAL,
                precipitation REAL,
                wind_speed_10m REAL,
                weather_code INTEGER,
                fetched_at TEXT NOT NULL,
                UNIQUE(city, timestamp)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                event_type TEXT NOT NULL,
                description TEXT NOT NULL,
                severity TEXT NOT NULL,
                reading_id INTEGER REFERENCES readings(id),
                reading_data TEXT NOT NULL,
                detected_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()