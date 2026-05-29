# WatchAgent

A weather-monitoring service that polls live conditions for Ottawa, Toronto, and Vancouver, detects notable events from the resulting time series, and exposes the data through an HTTP API.

## What it does

1. **Polls** Open-Meteo every 10 minutes for the three cities' current conditions.
2. **Deduplicates** readings by `(city, timestamp)` — Open-Meteo only updates hourly, so frequent polling produces duplicates that are silently dropped at the SQL layer (`INSERT OR IGNORE` plus a unique constraint).
3. **Detects events** on each newly-stored reading using five independent rules (see the [Event detection design](#event-detection-design) section).
4. **Exposes** the stored readings and events through three JSON endpoints.

## Architecture

```
+--------------+        +-------------------+        +-------------------+
|  Open-Meteo  |  HTTP  |  poller.py        |  write |  weather.db       |
|  (live API)  | <----- |  asyncio.to_thread| -----> |  (SQLite, on-disk)|
+--------------+        |  every 600s       |        +-------------------+
                        +-------------------+                  ^
                                  ^                            | read
                                  | invoked by                 |
                                  v                            |
                        +-------------------+        +-------------------+
                        |  main.py lifespan |        |  routes.py        |
                        |  (FastAPI)        |        |  (FastAPI router) |
                        +-------------------+        +-------------------+
                                                              |
                                                              v
                                                   GET /health
                                                   GET /readings
                                                   GET /events
```

The poller runs as an asyncio task launched from FastAPI's lifespan; both poller and API share the same SQLite file. On each successful poll, `store_reading` returns whether the row was new (vs. a duplicate of the most recent hourly bucket); only new rows trigger event detection.

## Technology choices

| Choice | Why |
| --- | --- |
| **FastAPI** | Built-in request validation via type hints, automatic OpenAPI docs at `/docs`, async lifespan to embed the poller in the same process, and a small dependency footprint. Heavier frameworks (Django) and lighter ones (Flask) both have tradeoffs that don't pay off at this scale. |
| **SQLite** | The data volume is tiny (one reading per city per hour ≈ 72 rows/day). A file-on-disk database gives us the required Docker-volume persistence without a separate database container, and SQLite's `UNIQUE` constraint is exactly the right tool for the dedup contract. |
| **httpx** | Modern HTTP client with both sync and async APIs, cleaner exceptions than `requests`, and the same client that FastAPI's `TestClient` uses — so production code and test code share the same HTTP semantics. |
| **`asyncio.create_task` + `asyncio.sleep`** | A simple `while True: await asyncio.to_thread(poll_all); await asyncio.sleep(600)` is enough for a 10-minute cadence. APScheduler would add a dependency and a thread pool for no functional gain. |

## API reference

### `GET /health`

Liveness + summary counts.

```bash
curl http://localhost:8000/health
```

```json
{ "status": "ok", "readings_stored": 42, "events_stored": 7 }
```

### `GET /readings`

Most recent readings, newest first. Optional filters: `city` and `limit` (default 50).

```bash
curl 'http://localhost:8000/readings?city=Ottawa&limit=10'
```

```json
{
  "readings": [
    {
      "id": 1,
      "city": "Ottawa",
      "timestamp": "2026-05-28T11:00",
      "temperature_2m": 12.5,
      "apparent_temperature": 10.5,
      "precipitation": 0.0,
      "wind_speed_10m": 8.4,
      "weather_code": 1,
      "fetched_at": "2026-05-28T15:30:01+00:00"
    }
  ]
}
```

### `GET /events`

Most recent notable events, newest first. Optional filters: `city` and `limit` (default 50).

```bash
curl 'http://localhost:8000/events?city=Toronto&limit=10'
```

```json
{
  "events": [
    {
      "id": 1,
      "city": "Toronto",
      "event_type": "wind_advisory",
      "description": "High winds in Toronto: 78.0km/h",
      "severity": "medium",
      "reading_id": 14,
      "reading_data": "{\"wind_speed_kmh\": 78.0}",
      "detected_at": "2026-05-28T15:30:02+00:00"
    }
  ]
}
```

## Event detection design

The service distinguishes between **absolute** events (one reading is independently alarming) and **contextual** events (a reading is notable only against recent history). It implements both styles.

### 1. `temperature_anomaly` — contextual

Compares the current temperature to the mean and standard deviation of the last 48 stored readings *for that city*.

- `|z-score| > 2` → severity `medium`
- `|z-score| > 3` → severity `high`
- Requires at least 10 readings of history; below that, the detector returns silently.

**Why this approach.** Absolute temperature thresholds don't generalise across cities. 25 °C in February is alarming in Ottawa and unremarkable in Vancouver. Using each city's own recent distribution as the baseline makes the detector *implicitly* city-aware without hand-coding per-city thresholds — the city's own climate determines what counts as anomalous. The 48-reading window (~48 hours of observation) is short enough to track seasonal drift but long enough to span a full diurnal cycle in the baseline.

**Known limitation.** Because the window mixes day and night readings, a hot afternoon can register as anomalous purely against the night-time portion of the window. A future improvement would compare against the same hour-of-day across previous days instead.

### 2. `rapid_temperature_change` — contextual

Compares the current reading's temperature to the immediately previous stored reading.

- `|delta| >= 4 °C` → severity `medium`
- `|delta| >= 7 °C` → severity `high`

**Why this approach.** Open-Meteo updates hourly, so consecutive stored readings are roughly an hour apart. Typical diurnal change moves at 1–2 °C per hour; 4 °C in one hour is meaningfully fast (a front or sharp clearing), 7 °C is unusual enough that even false-positive sensor noise is worth surfacing for review. The thresholds are uniform across cities because the *rate of change* (unlike the absolute temperature) is comparable across climates.

### 3. `precipitation_onset` and `precipitation_cessation` — transition

Fires when precipitation transitions between dry (0 mm/hr) and wet (> 0.5 mm/hr).

- onset: previous = 0, current > 0.5 → `low` severity
- cessation: previous > 0.5, current = 0 → `low` severity

**Why this approach.** Total accumulated precipitation isn't useful as an event on its own — the operational signal is the *start* and *stop* of a rain event. The 0.5 mm/hr floor filters trace amounts that don't change anyone's plans. Severity is `low` because the transition is the signal, not the magnitude; severe downpours show up under the WMO-code detector below.

### 4. `severe_weather` — absolute + transition

Maps WMO weather codes to severity bands and fires only when the severity *changes* from the previous reading.

| WMO codes | Description | Severity |
| --- | --- | --- |
| 51–67 | drizzle, rain | moderate |
| 71–77 | snow | moderate |
| 80–82 | rain showers | moderate |
| 85–86 | snow showers | moderate |
| 95–99 | thunderstorm | high |

**Why this approach.** The WMO code is a clean discrete signal — "what kind of weather is currently happening." Firing on every consecutive reading that shares the same severity would flood the event log; firing only on transitions (e.g. clear → drizzle, drizzle → thunderstorm) keeps the stream readable. Codes outside these bands (clear, mainly clear, fog) don't fire because they aren't operationally significant.

### 5. `wind_advisory` — absolute

Fires when wind speed crosses 60 km/h.

- `>= 60 km/h` → severity `medium`
- `>= 80 km/h` → severity `high`

**Dedup.** Once a wind_advisory has been recorded for a given city, no further advisory will fire for that city for the lifetime of the database. This is intentionally aggressive — sustained high winds would otherwise produce one event per poll. A future iteration should switch this to a time-windowed cooldown (e.g. don't refire within 6 hours of the previous advisory) so a second storm a week later still produces an event.

**Why 60 km/h.** Environment Canada issues wind warnings around 70 km/h sustained or 90 km/h gusts. 60 km/h gives the system a small lead time on the official advisory and is credible across all three cities — none of them are sheltered enough that 60 km/h is normal.

### What's deliberately out of scope (for now)

- **Per-city absolute thresholds.** The contextual detectors (`temperature_anomaly`) are already per-city by construction; the absolute ones (`wind_advisory`, `precipitation_onset`, `severe_weather`) currently use uniform thresholds. Overriding e.g. Vancouver's precipitation threshold upward (rain there is routine) would be a defensible future improvement.
- **Apparent-vs-actual temperature gap.** A "feels like" reading 5+ °C colder than the actual is a real winter-safety signal that isn't currently captured.
- **Cross-city comparisons.** "Toronto is 20 °C warmer than Ottawa right now" would be useful for regional pattern-tracking but isn't implemented.

## Setup

### Prerequisites

- Python 3.11+

### Local development (Windows / PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd app
uvicorn main:app --reload
```

The API is then at <http://localhost:8000>; the poller starts automatically and writes `weather.db` in the current directory. Override the database path with the `DB_PATH` environment variable.

### Docker — coming

A `Dockerfile`, `docker-compose.yml`, and `.env.example` are upcoming. The intended start command is `docker compose up --build` from a clean clone, with `weather.db` mounted on a named volume for persistence across container restarts.

## Running tests

```bash
pytest -v
```

Three test files:

| File | What it covers |
| --- | --- |
| `tests/test_dedup.py` | Mocks `httpx.get` to return the same payload twice; asserts only one row is stored. Also covers the SQL-layer dedup and the missing-timestamp guard. |
| `tests/test_events.py` | Constructs controlled reading sequences and asserts each implemented detector fires when expected and stays silent otherwise. Assertions are strict (`len(events) == 1` + event type + severity, or `events == []`) so a misfire, double-fire, or cross-detector noise will break the test. |
| `tests/test_api.py` | Builds a router-only FastAPI app (skipping `main.py`'s lifespan to keep the poller out of test runs), seeds the database, and asserts the shape of `/health`, `/readings`, and `/events` including filters, limits, and ordering. |

Every test uses `tmp_path` + `monkeypatch.setenv("DB_PATH", …)` for isolation, and no test touches the live Open-Meteo API or the development database.

## Cursor setup

The `.cursor/` folder (rules, agent definition, and data-analysis skill) is being built next. Once committed, this section will describe each rule, the agent's scoped purpose, and what the data-analysis skill answers.

## Project layout

```
app/
  main.py        FastAPI app + lifespan poller
  poller.py      Open-Meteo client, dedup-on-insert
  events.py      Five detectors + the orchestrating check_for_events()
  routes.py      /health, /readings, /events
  database.py    SQLite connection + schema
  models.py      Dataclasses (currently unused; kept as documentation)
tests/
  test_dedup.py
  test_events.py
  test_api.py
requirements.txt
README.md
```
