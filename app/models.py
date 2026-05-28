from dataclasses import dataclass

@dataclass
class Reading:
    city: str
    timestamp: str
    temperature_2m: float
    apparent_temperature: float
    precipitation: float
    wind_speed_10m: float
    weather_code: int
    fetched_at: str

@dataclass
class Event:
    city: str
    timestamp: str
    event_type: str
    description: str
    severity: str        # "low", "medium", "high"
    reading_data: str    # JSON string of the raw reading
    detected_at: str

