---
name: analyze-weather-data
description: Query the WatchAgent SQLite database to answer analytical questions about stored readings and events - per-city statistics, recent activity, temperature trends, cross-city comparisons, event counts, and time-window summaries. Use when the user asks about trends, summaries, the hottest or coldest city, how many events fired, or anything else that requires querying weather.db.
---

# Analyze weather data

Run analytical queries against `weather.db` (the SQLite file written by the
poller). Invoke `scripts/analyze.py` with a natural-language question and
optional time-window flag.

## Available questions

```bash
# Overall counts and date range
python .cursor/skills/analyze-weather-data/scripts/analyze.py --question summary

# Per-city statistics (min/max/mean temp, total precip, max wind)
python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "average temperature per city"

# Per-city event counts grouped by event_type + severity
python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "which city had the most events"

# Most recent events; optional time window
python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "recent events"
python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "recent events" --hours 24

# Last 12 readings per city with delta from previous reading
python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "temperature trend"

# Latest reading per city, side-by-side
python .cursor/skills/analyze-weather-data/scripts/analyze.py --question "compare cities"
```

The script outputs JSON to stdout. Read the JSON and explain the result to the
user in plain English. If the JSON contains an `error` key, surface that error
verbatim - it usually means the database does not exist yet or the poller has
not run.

## Database path

The script reads `DB_PATH` from the environment, defaulting to `weather.db`
in the current working directory. Inside the Docker container, the compose
file sets `DB_PATH=/data/weather.db`.

## Scope

This skill is read-only. It does not modify the database, trigger event
detection, or call the live Open-Meteo API. For event-detection logic
changes, use the `event-reviewer` agent in `.cursor/agents/`.
