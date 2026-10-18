"""Deterministic tests: no live service is required to run the suite."""

from contextlib import redirect_stderr
from copy import deepcopy
from io import BytesIO, StringIO
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import weather

LOCATION = weather.Location("Vancouver", "British Columbia", "Canada", 49.24966, -123.11934)
GEOCODING = {"results": [{"name": "Vancouver", "admin1": "British Columbia", "country": "Canada", "latitude": 49.24966, "longitude": -123.11934}]}
CURRENT = {
    "timezone": "America/Vancouver", "utc_offset_seconds": -25200,
    "current": {"time": "2026-10-17T11:15", "temperature_2m": 12.4,
                "apparent_temperature": 11.2, "relative_humidity_2m": 79,
                "weather_code": 3, "wind_speed_10m": 8.5},
}


def fake_client(locations=None):
    client = Mock()
    client.search.return_value = [LOCATION] if locations is None else locations
    parser = weather.OpenMeteoClient()
    parser.get_json = Mock(return_value=deepcopy(CURRENT))
    client.current.return_value = parser.current(LOCATION, "metric")
    return client


def run_main(args, client=None):
    out, err = StringIO(), StringIO()
    code = weather.main(args, client=client or fake_client(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.client = weather.OpenMeteoClient(timeout=7)

    def response(self, content):
        return patch("weather.urlopen", return_value=BytesIO(content))

    def test_https_encoding_headers_and_timeout(self):
        with self.response(b'{"ok": true}') as opener:
            self.assertEqual(self.client.get_json("https://example.test/search", {"name": "Sao Paulo & coast"}), {"ok": True})
        request = opener.call_args.args[0]
        self.assertEqual(parse_qs(urlparse(request.full_url).query)["name"], ["Sao Paulo & coast"])
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(opener.call_args.kwargs["timeout"], 7)

    def test_http_failures_are_actionable(self):
        for status, expected in [(429, "limit"), (503, "temporarily unavailable"), (400, "HTTP 400")]:
            with self.subTest(status=status), patch("weather.urlopen", side_effect=HTTPError("https://example.test", status, "error", {}, None)):
                with self.assertRaisesRegex(weather.WeatherError, expected):
                    self.client.get_json("https://example.test", {})

    def test_dns_and_connection_errors(self):
        for error in [URLError("DNS failure"), ConnectionResetError("reset"), IncompleteRead(b"partial")]:
            with self.subTest(error=error), patch("weather.urlopen", side_effect=error):
                with self.assertRaisesRegex(weather.WeatherError, "internet connection"):
                    self.client.get_json("https://example.test", {})

    def test_direct_and_wrapped_timeouts(self):
        for error in [TimeoutError(), socket.timeout(), URLError(socket.timeout())]:
            with self.subTest(error=error), patch("weather.urlopen", side_effect=error):
                with self.assertRaisesRegex(weather.WeatherError, "timed out"):
                    self.client.get_json("https://example.test", {})

    def test_invalid_json_encoding_and_envelopes(self):
        for content in [b"not JSON", b"\xff", b"[]", b"null", b'{"error":true}']:
            with self.subTest(content=content), self.response(content):
                with self.assertRaises(weather.WeatherError):
                    self.client.get_json("https://example.test", {})

    def test_limits_response_size(self):
        with self.response(b"x" * (weather.MAX_RESPONSE_BYTES + 1)):
            with self.assertRaisesRegex(weather.WeatherError, "large response"):
                self.client.get_json("https://example.test", {})


class DataTests(unittest.TestCase):
    def setUp(self):
        self.client = weather.OpenMeteoClient()

    def test_search_country_and_coordinates(self):
        self.client.get_json = Mock(return_value=deepcopy(GEOCODING))
        self.assertEqual(self.client.search("Vancouver", "CA"), [LOCATION])
        params = self.client.get_json.call_args.args[1]
        self.assertEqual(params["countryCode"], "CA")
        self.assertEqual(params["count"], 10)

    def test_no_search_results(self):
        self.client.get_json = Mock(return_value={})
        self.assertEqual(self.client.search("Imaginary place"), [])

    def test_missing_optional_location_fields(self):
        self.client.get_json = Mock(return_value={"results": [{"name": "Small town", "latitude": 0, "longitude": 0}]})
        self.assertEqual(self.client.search("Small town")[0].label, "Small town")

    def test_rejects_malformed_locations(self):
        for value in [None, {}, [None], [{"name": 42}], [{"name": "Town", "latitude": 91, "longitude": 0}]]:
            with self.subTest(value=value):
                self.client.get_json = Mock(return_value={"results": value})
                with self.assertRaises(weather.WeatherError):
                    self.client.search("Town")

    def test_sanitizes_terminal_control_characters(self):
        self.assertNotIn("\x1b", weather.clean_text("\x1b[31mTown\nCity"))
        data = deepcopy(GEOCODING)
        data["results"][0]["name"] = "Van\x1b[2J\ncouver"
        self.client.get_json = Mock(return_value=data)
        self.assertNotIn("\x1b", self.client.search("Vancouver")[0].label)

    def test_metric_current_weather(self):
        self.client.get_json = Mock(return_value=deepcopy(CURRENT))
        result = self.client.current(LOCATION, "metric")
        self.assertEqual(result.temperature, 12.4)
        self.assertEqual(result.condition, "Overcast")
        self.assertEqual(result.temperature_unit, "C")
        self.assertEqual(result.wind_unit, "km/h")
        params = self.client.get_json.call_args.args[1]
        self.assertEqual(params["timezone"], "auto")
        self.assertEqual(params["temperature_unit"], "celsius")
        self.assertEqual(params["latitude"], LOCATION.latitude)

    def test_imperial_requests_api_conversion(self):
        self.client.get_json = Mock(return_value=deepcopy(CURRENT))
        result = self.client.current(LOCATION, "imperial")
        self.assertEqual(result.temperature_unit, "F")
        self.assertEqual(result.wind_unit, "mph")
        params = self.client.get_json.call_args.args[1]
        self.assertEqual(params["temperature_unit"], "fahrenheit")
        self.assertEqual(params["wind_speed_unit"], "mph")

    def test_unknown_wmo_code_is_still_useful(self):
        data = deepcopy(CURRENT); data["current"]["weather_code"] = 123
        self.client.get_json = Mock(return_value=data)
        self.assertIn("code 123", self.client.current(LOCATION, "metric").condition)

    def test_rejects_missing_and_invalid_weather_values(self):
        for field, value in [("temperature_2m", None), ("temperature_2m", True), ("temperature_2m", float("nan")), ("temperature_2m", float("inf")), ("temperature_2m", 10 ** 999), ("apparent_temperature", "warm"), ("relative_humidity_2m", 101), ("wind_speed_10m", -1), ("weather_code", 1.5), ("time", "bad timestamp"), ("time", "2026-10-17")]:
            data = deepcopy(CURRENT); data["current"][field] = value
            with self.subTest(field=field, value=value):
                self.client.get_json = Mock(return_value=data)
                with self.assertRaises(weather.WeatherError):
                    self.client.current(LOCATION, "metric")

    def test_rejects_missing_current_and_timezone(self):
        for data in [{}, {**CURRENT, "current": None}, {**CURRENT, "timezone": ""}, {**CURRENT, "utc_offset_seconds": 1.5}]:
            with self.subTest(data=data):
                self.client.get_json = Mock(return_value=data)
                with self.assertRaises(weather.WeatherError):
                    self.client.current(LOCATION, "metric")


class CLITests(unittest.TestCase):
    def test_human_output(self):
        code, out, err = run_main(["Vancouver", "--country", "ca"])
        self.assertEqual(code, 0); self.assertEqual(err, "")
        for text in ["Vancouver, British Columbia, Canada", "12.4 C", "Overcast", "Humidity", "79%", "America/Vancouver", "2026-10-17 11:15"]:
            self.assertIn(text, out)
        self.assertNotIn("\x1b", out)

    def test_multiword_search_and_country_normalization(self):
        client = fake_client()
        run_main(["New", "York", "--country", "us"], client)
        client.search.assert_called_once_with("New York", "US")

    def test_json_is_parseable_without_ansi_or_notes(self):
        client = fake_client([LOCATION, LOCATION])
        code, out, err = run_main(["Vancouver", "--json"], client)
        self.assertEqual(code, 0); self.assertEqual(err, "")
        data = json.loads(out)
        self.assertEqual(data["weather"]["temperature_unit"], "C")
        self.assertEqual(data["location"]["latitude"], LOCATION.latitude)
        self.assertEqual(data["source"], "Open-Meteo")

    def test_coordinates_bypass_geocoding(self):
        client = fake_client()
        code, _, _ = run_main(["--lat", "0", "--lon", "0"], client)
        self.assertEqual(code, 0); client.search.assert_not_called()
        self.assertEqual(client.current.call_args.args[0].latitude, 0)

    def test_list_locations_and_json_list_do_not_fetch_weather(self):
        for flags in [[], ["--json"]]:
            client = fake_client()
            code, out, _ = run_main(["Vancouver", "--list", *flags], client)
            self.assertEqual(code, 0); client.current.assert_not_called()
            if flags:
                self.assertEqual(json.loads(out)["locations"][0]["match"], 1)
            else:
                self.assertIn("--match N", out)

    def test_ambiguous_location_note_and_explicit_match(self):
        other = weather.Location("Vancouver", "Washington", "United States", 45.63, -122.67)
        client = fake_client([LOCATION, other])
        _, _, err = run_main(["Vancouver"], client)
        self.assertIn("first of 2 matches", err)
        _, _, err = run_main(["Vancouver", "--match", "2"], client)
        self.assertEqual(err, ""); self.assertEqual(client.current.call_args.args[0], other)

    def test_unknown_city_and_out_of_range_match(self):
        code, out, err = run_main(["Atlantis"], fake_client([]))
        self.assertEqual(code, 1); self.assertEqual(out, ""); self.assertIn("No locations found", err)
        code, _, err = run_main(["Vancouver", "--match", "2"])
        self.assertEqual(code, 1); self.assertIn("Only 1", err)

    def test_errors_use_stderr_and_exit_one(self):
        client = fake_client(); client.current.side_effect = weather.WeatherError("Offline")
        code, out, err = run_main(["Vancouver", "--json"], client)
        self.assertEqual((code, out), (1, "")); self.assertIn("Offline", err)
        self.assertNotIn("Traceback", err)

    def test_interrupt_is_friendly(self):
        client = fake_client(); client.search.side_effect = KeyboardInterrupt()
        code, _, err = run_main(["Vancouver"], client)
        self.assertEqual(code, 130); self.assertIn("cancelled", err)

    def test_invalid_arguments_exit_two_before_network(self):
        cases = [[], [" "], ["A"], ["Van\nCity"], ["Vancouver", "--country", "Canada"], ["--lat", "49"], ["--lat", "91", "--lon", "0"], ["--lat", "nan", "--lon", "0"], ["Vancouver", "--lat", "49", "--lon", "0"], ["--lat", "0", "--lon", "0", "--list"], ["Vancouver", "--timeout", "nan"], ["Vancouver", "--timeout", "0"], ["Vancouver", "--match", "0"], ["Vancouver", "--list", "--match", "1"], ["Vancouver", "--units", "kelvin"]]
        for args in cases:
            client = fake_client()
            with self.subTest(args=args), redirect_stderr(StringIO()), self.assertRaises(SystemExit) as result:
                weather.main(args, client=client)
            self.assertEqual(result.exception.code, 2); client.search.assert_not_called(); client.current.assert_not_called()

    def test_color_only_when_requested_on_a_terminal(self):
        out = StringIO(); out.isatty = lambda: True
        with patch.dict(os.environ, {}, clear=True):
            weather.main(["Vancouver"], client=fake_client(), stdout=out)
        self.assertIn("\x1b", out.getvalue())
        for args, env in [(["Vancouver", "--no-color"], {}), (["Vancouver"], {"NO_COLOR": "1"})]:
            out = StringIO(); out.isatty = lambda: True
            with patch.dict(os.environ, env, clear=True):
                weather.main(args, client=fake_client(), stdout=out)
            self.assertNotIn("\x1b", out.getvalue())

    def test_help_and_version_as_real_processes(self):
        script = Path(__file__).resolve().parents[1] / "weather.py"
        for flag, expected in [("--help", "--country"), ("--version", "Skycast 1.0.0")]:
            result = subprocess.run([sys.executable, str(script), flag], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0); self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
