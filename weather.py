#!/usr/bin/env python3
"""Skycast: current weather in your terminal, using only Python's standard library."""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
from http.client import HTTPException
import json
import math
import os
import re
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VERSION = "1.0.0"
MAX_RESPONSE_BYTES = 2_000_000
CURRENT_FIELDS = (
    "temperature_2m", "apparent_temperature", "relative_humidity_2m",
    "weather_code", "wind_speed_10m",
)
CONDITIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Light rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light rain showers", 81: "Moderate rain showers", 82: "Heavy rain showers",
    85: "Light snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
    96: "Thunderstorm with light hail", 99: "Thunderstorm with heavy hail",
}


class WeatherError(Exception):
    """An expected user-facing service or data error."""


def clean_text(value):
    """Keep remote text from inserting terminal escape codes or extra lines."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value).strip()


def number(value, field, low=None, high=None):
    try:
        finite = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise WeatherError(f"The service returned invalid {field} data. Please try again.")
    if (low is not None and value < low) or (high is not None and value > high):
        raise WeatherError(f"The service returned out-of-range {field} data. Please try again.")
    return value


@dataclass(frozen=True)
class Location:
    name: str
    region: str
    country: str
    latitude: float
    longitude: float

    @property
    def label(self):
        return ", ".join(dict.fromkeys(part for part in (self.name, self.region, self.country) if part))


@dataclass(frozen=True)
class Weather:
    temperature: float
    feels_like: float
    humidity: float
    wind_speed: float
    weather_code: int
    condition: str
    local_time: str
    timezone: str
    utc_offset_seconds: int
    temperature_unit: str
    wind_unit: str


class OpenMeteoClient:
    def __init__(self, timeout=15):
        self.timeout = timeout

    def get_json(self, base, params):
        request = Request(f"{base}?{urlencode(params)}", headers={
            "User-Agent": f"SkycastCLI/{VERSION}", "Accept": "application/json",
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            error.close()
            if error.code == 429:
                raise WeatherError("The weather service's request limit was reached. Please try again later.") from error
            if error.code >= 500:
                raise WeatherError("The weather service is temporarily unavailable. Please try again later.") from error
            raise WeatherError(f"The weather service rejected the request (HTTP {error.code}). Check your location and try again.") from error
        except (TimeoutError, socket.timeout) as error:
            raise WeatherError("The request timed out. Try again or increase --timeout (up to 60 seconds).") from error
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise WeatherError("The request timed out. Try again or increase --timeout (up to 60 seconds).") from error
            raise WeatherError("Could not connect to the weather service. Check your internet connection and try again.") from error
        except (OSError, HTTPException) as error:
            raise WeatherError("The network connection failed. Check your internet connection and try again.") from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise WeatherError("The weather service returned an unexpectedly large response. Please try again.")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise WeatherError("The weather service returned unreadable data. Please try again.") from error
        if not isinstance(data, dict) or data.get("error"):
            raise WeatherError("The weather service returned an unexpected response. Please try again.")
        return data

    def search(self, name, country=None):
        params = {"name": name, "count": 10, "language": "en", "format": "json"}
        if country:
            params["countryCode"] = country
        data = self.get_json("https://geocoding-api.open-meteo.com/v1/search", params)
        results = data.get("results", [])
        if not isinstance(results, list):
            raise WeatherError("The location service returned invalid results. Please try again.")
        locations = []
        for result in results:
            if not isinstance(result, dict):
                raise WeatherError("The location service returned an invalid location. Please try again.")
            name = result.get("name")
            region, country_name = result.get("admin1", ""), result.get("country", "")
            if not isinstance(name, str) or not clean_text(name) or not isinstance(region, str) or not isinstance(country_name, str):
                raise WeatherError("The location service returned an invalid location name. Please try again.")
            locations.append(Location(
                clean_text(name), clean_text(region), clean_text(country_name),
                number(result.get("latitude"), "latitude", -90, 90),
                number(result.get("longitude"), "longitude", -180, 180),
            ))
        return locations

    def current(self, location, units):
        imperial = units == "imperial"
        data = self.get_json("https://api.open-meteo.com/v1/forecast", {
            "latitude": location.latitude, "longitude": location.longitude,
            "current": ",".join(CURRENT_FIELDS), "timezone": "auto", "forecast_days": 1,
            "temperature_unit": "fahrenheit" if imperial else "celsius",
            "wind_speed_unit": "mph" if imperial else "kmh",
        })
        current = data.get("current")
        if not isinstance(current, dict):
            raise WeatherError("Current weather is unavailable for this location. Please try again.")
        code = number(current.get("weather_code"), "weather code", 0)
        if int(code) != code:
            raise WeatherError("The service returned an invalid weather code. Please try again.")
        time, timezone = current.get("time"), data.get("timezone")
        if not isinstance(time, str) or "T" not in time or not isinstance(timezone, str) or not clean_text(timezone):
            raise WeatherError("The service returned invalid local time data. Please try again.")
        try:
            datetime.fromisoformat(time)
        except ValueError as error:
            raise WeatherError("The service returned an invalid weather timestamp. Please try again.") from error
        offset = number(data.get("utc_offset_seconds"), "timezone offset", -86400, 86400)
        if int(offset) != offset:
            raise WeatherError("The service returned an invalid timezone offset. Please try again.")
        return Weather(
            temperature=number(current.get("temperature_2m"), "temperature"),
            feels_like=number(current.get("apparent_temperature"), "feels-like temperature"),
            humidity=number(current.get("relative_humidity_2m"), "humidity", 0, 100),
            wind_speed=number(current.get("wind_speed_10m"), "wind speed", 0),
            weather_code=int(code), condition=CONDITIONS.get(int(code), f"Unknown conditions (code {int(code)})"),
            local_time=time, timezone=clean_text(timezone), utc_offset_seconds=int(offset),
            temperature_unit="F" if imperial else "C", wind_unit="mph" if imperial else "km/h",
        )


def render_weather(location, weather, *, color=False):
    title = "SKYCAST / CURRENT WEATHER"
    if color:
        title = f"\033[1;36m{title}\033[0m"
    timestamp = datetime.fromisoformat(weather.local_time).strftime("%Y-%m-%d %H:%M")
    return "\n".join([
        "", f"  {title}", "  " + "-" * 48,
        f"  {location.label}", f"  {location.latitude:.4f}, {location.longitude:.4f}", "",
        f"  {weather.temperature:.1f} {weather.temperature_unit}  |  {weather.condition}", "",
        f"  Feels like   {weather.feels_like:.1f} {weather.temperature_unit}",
        f"  Humidity     {weather.humidity:g}%",
        f"  Wind         {weather.wind_speed:.1f} {weather.wind_unit}", "",
        f"  Local time   {timestamp} ({weather.timezone})",
        "  Source       Open-Meteo (https://open-meteo.com/)",
        "  Data         Current model-based conditions", "",
    ])


def build_parser():
    parser = argparse.ArgumentParser(
        description="Current weather in your terminal. No API key or extra packages needed.",
        epilog='Examples: python3 weather.py Vancouver --country CA | python3 weather.py "New York" --units imperial',
    )
    parser.add_argument("city", nargs="*", help="city or postal code; multiword names do not need quotes")
    parser.add_argument("--country", metavar="CODE", help="two-letter country filter, such as CA, US, or GB")
    parser.add_argument("--list", action="store_true", help="list up to 10 matching locations without fetching weather")
    parser.add_argument("--match", type=int, default=None, metavar="N", help="use match N from --list (default: first result)")
    parser.add_argument("--lat", type=float, metavar="LAT", help="latitude; use with --lon instead of a city")
    parser.add_argument("--lon", type=float, metavar="LON", help="longitude; use with --lat instead of a city")
    parser.add_argument("--units", choices=["metric", "imperial"], default="metric", help="C and km/h, or F and mph (default: metric)")
    parser.add_argument("--json", action="store_true", help="write structured JSON to stdout")
    parser.add_argument("--timeout", type=float, default=15, metavar="SECONDS", help="network timeout per request, 1-60 seconds (default: 15)")
    parser.add_argument("--no-color", action="store_true", help="disable terminal colors (also honors NO_COLOR)")
    parser.add_argument("--version", action="version", version=f"Skycast {VERSION}")
    return parser


def main(argv=None, *, client=None, stdout=None, stderr=None):
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)
    coordinates = args.lat is not None or args.lon is not None
    city = " ".join(args.city).strip()
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 60:
        parser.error("--timeout must be between 1 and 60 seconds")
    if args.country and not re.fullmatch(r"[A-Za-z]{2}", args.country):
        parser.error("--country must be a two-letter country code, such as CA")
    if args.match is not None and not 1 <= args.match <= 10:
        parser.error("--match must be between 1 and 10")
    if args.list and args.match is not None:
        parser.error("use --list to view matches, then --match N in a separate command")
    if coordinates:
        if args.lat is None or args.lon is None:
            parser.error("--lat and --lon must be provided together")
        if not math.isfinite(args.lat) or not -90 <= args.lat <= 90 or not math.isfinite(args.lon) or not -180 <= args.lon <= 180:
            parser.error("latitude must be -90 to 90 and longitude -180 to 180")
        if city or args.country or args.list or args.match is not None:
            parser.error("coordinate lookup cannot be combined with a city, --country, --list, or --match")
    elif not city or len(city) < 2 or len(city) > 200 or clean_text(city) != city:
        parser.error("provide a city or postal code (2-200 characters), or use --lat and --lon")
    client = client if client is not None else OpenMeteoClient(args.timeout)
    try:
        if coordinates:
            location = Location("Coordinate lookup", "", "", args.lat, args.lon)
        else:
            locations = client.search(city, args.country.upper() if args.country else None)
            if not locations:
                raise WeatherError(f'No locations found for "{city}". Check the spelling or try a nearby city.')
            if args.list:
                if args.json:
                    print(json.dumps({"locations": [{"match": index, **asdict(item)} for index, item in enumerate(locations, 1)], "source": "GeoNames via Open-Meteo", "source_url": "https://open-meteo.com/"}, ensure_ascii=True, indent=2), file=stdout)
                else:
                    print(f"\n  Locations matching {city}:\n", file=stdout)
                    for index, item in enumerate(locations, 1):
                        print(f"  {index:2}. {item.label} ({item.latitude:.4f}, {item.longitude:.4f})", file=stdout)
                    print("\n  Run the same city search with --match N to choose a location.\n", file=stdout)
                    print("  Locations: GeoNames via Open-Meteo (https://open-meteo.com/)\n", file=stdout)
                return 0
            match = args.match or 1
            if match > len(locations):
                raise WeatherError(f"Only {len(locations)} location(s) matched. Use --list to view them.")
            location = locations[match - 1]
            if len(locations) > 1 and args.match is None and not args.json:
                print(f"Note: using the first of {len(locations)} matches. Add --country CODE or use --list and --match N to narrow it down.", file=stderr)
        weather = client.current(location, args.units)
        if args.json:
            print(json.dumps({"location": asdict(location), "weather": asdict(weather), "source": "Open-Meteo", "source_url": "https://open-meteo.com/", "data_type": "current model-based conditions"}, ensure_ascii=True, indent=2, allow_nan=False), file=stdout)
        else:
            color = not args.no_color and "NO_COLOR" not in os.environ and getattr(stdout, "isatty", lambda: False)()
            print(render_weather(location, weather, color=color), file=stdout)
        return 0
    except WeatherError as error:
        print(f"Skycast: {error}", file=stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSkycast: cancelled.", file=stderr)
        return 130


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # Avoid a traceback when a downstream command (such as head) exits early.
        os._exit(0)
