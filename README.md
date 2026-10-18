# Skycast CLI

**A quick look outside, without leaving your terminal.** Fetch current weather by city, postal code, or coordinates using Python and the free Open-Meteo public API. No API key, third-party packages, or background server required.

## Quick start

Requires **Python 3.10 or newer** and an internet connection.

```bash
git clone https://github.com/Soumil-Makhija/skycast-cli.git
cd skycast-cli
python3 weather.py Vancouver --country CA
```

Already using this workspace on your Mac?

```bash
cd ~/Desktop/gitHub_practice/skycast-cli
python3 weather.py Vancouver --country CA
```

On Windows, use `py` or `python` instead of `python3` if appropriate for your installation. Every command fetches fresh data, prints its result, and exits.

Illustrative output (these values are an example, not a live weather report):

```text
  SKYCAST / CURRENT WEATHER
  ------------------------------------------------
  Vancouver, British Columbia, Canada
  49.2497, -123.1193

  12.4 C  |  Overcast

  Feels like   11.2 C
  Humidity     79%
  Wind         8.5 km/h

  Local time   2026-10-17 11:15 (America/Vancouver)
  Source       Open-Meteo (https://open-meteo.com/)
  Data         Current model-based conditions
```

## Everyday commands

```bash
# Multiword city names work with or without quotes
python3 weather.py New York --country US

# Fahrenheit and miles per hour
python3 weather.py London --country GB --units imperial

# See which city the service will choose, then select a result
python3 weather.py Springfield --country US --list
python3 weather.py Springfield --country US --match 2

# Coordinates skip the location search entirely
python3 weather.py --lat 49.2827 --lon -123.1207

# Machine-readable output
python3 weather.py Tokyo --country JP --json

# A slower connection
python3 weather.py Paris --country FR --timeout 30

python3 weather.py --help
python3 weather.py --version
```

The first location match is used by default. Human-readable output warns when several locations match, and always displays the chosen location and coordinates. Use `--country` or inspect `--list` if the selected place is not the one you intended. Match numbers refer to the current API result order, which can change; coordinates are the best choice for a precise, repeatable lookup.

## Options

| Option | Behavior |
| --- | --- |
| `CITY` | City name or postal code, 2–200 characters |
| `--country CODE` | Two-letter country filter, e.g. `CA`, `US`, `GB`; case-insensitive |
| `--list` | List up to 10 locations without requesting weather |
| `--match N` | Select result 1–10 from the same location search |
| `--lat LAT --lon LON` | Use coordinates instead of a city |
| `--units metric` | Celsius and km/h (default) |
| `--units imperial` | Fahrenheit and mph |
| `--json` | JSON on stdout; works with weather and `--list` |
| `--timeout SECONDS` | Per-request network timeout, 1–60 seconds; default 15 |
| `--no-color` | Disable the colored terminal heading |

Coordinates cannot be combined with a city, country, list, or match selector. Latitude must be between -90 and 90; longitude between -180 and 180. `--list` and `--match` are separate commands.

## JSON and shell scripts

Weather JSON contains `location`, `weather`, `source`, and `data_type`. The weather object includes numeric temperature, feels-like temperature, humidity, wind speed, WMO weather code, condition text, local timestamp, timezone, UTC offset, and explicit temperature/wind units. Temperatures use `C` or `F`; wind uses `km/h` or `mph`. Local timestamps have no offset suffix: use the accompanying timezone and `utc_offset_seconds` when interpreting them. Location-list JSON contains a `locations` array with 1-based match numbers.

```bash
python3 weather.py Vancouver --country CA --json > weather.json
```

Errors go to stderr, so they do not corrupt JSON on stdout. Exit codes are `0` for success, `1` for network/data/location errors, `2` for invalid command-line arguments, and `130` for cancellation with Ctrl+C. Colors are automatically disabled when output is redirected; `NO_COLOR` is also supported. Output uses ASCII labels and units; place names may contain Unicode characters.

## Data source

The app uses the [Open-Meteo Geocoding API](https://open-meteo.com/en/docs/geocoding-api) for location lookup and its [Weather API](https://open-meteo.com/en/docs) for current conditions. The latter is based on weather model data at 15-minute resolution, rather than a direct live reading from a nearby weather station. The displayed time is the provider's data time in the selected location's timezone. A fresh request can return the same timestamp until the provider updates its data.

Weather data is attributed to **Open-Meteo**; geocoding uses **GeoNames** through Open-Meteo. See the [data attribution and licence](https://open-meteo.com/en/licence) and [API terms](https://open-meteo.com/en/terms). The free hosted API is intended for non-commercial use and has usage limits. This project sends one weather request per invocation, plus one geocoding request for city searches; `--list` only performs geocoding. It does not continuously poll, cache results, or automatically retry failed requests.

## Reliability

- Checks missing, invalid, and non-finite values before showing weather.
- Handles unknown weather codes without crashing.
- Gives helpful messages for offline connections, timeouts, rate limits, service failures, and missing locations.
- Keeps TLS certificate verification enabled and limits response sizes.
- Removes control characters from service-provided text before writing to the terminal.
- Uses only Python's standard library. No dependencies to install and no credentials to manage.

If you cannot connect, check internet access and retry. For a timeout, try `--timeout 30`. For an unknown location, check the spelling, remove a country filter, or use coordinates. If you see a certificate error, use an up-to-date Python installation with its trusted certificates configured.

## Test

```bash
python3 -m unittest discover -s tests -v
```

Tests use mocked API responses, so they run offline and do not consume public API capacity. They cover transport failures, invalid data, unit selection, city disambiguation, coordinate searches, JSON output, argument validation, safe terminal text, colors, cancellation, and actual command-line help/version execution. GitHub Actions runs the suite on Linux, macOS, and Windows with Python 3.10 and 3.14.

To separately check the real API, use the Quick start command and compare the chosen location, update time, and units. Live API calls are kept out of CI to avoid failures caused by third-party outages.

## Files

```text
weather.py                  CLI, API client, validation and terminal formatting
tests/test_weather.py       Offline automated tests
.github/workflows/test.yml  Cross-platform test workflow
```
