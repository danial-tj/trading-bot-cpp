"""Offline tests for user-exported TradingView CSV adaptation."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service import tradingview
from service.tradingview import validate_tradingview_import

HEADER = "time,open,high,low,close,Volume\n"
CANONICAL_HEADER = "timestamp,open,high,low,close,volume\n"


def row(stamp="2025-01-02T09:30:00-05:00", prices="100,102,99,101,1000"):
    return stamp + "," + prices + "\n"


def payload(contents=None, **overrides):
    return {"name": "TradingView fixture", "symbol": "TEST", "source": "User-exported TradingView CSV",
            "timezone": "America/New_York", "currency": "USD", "price_adjustment": "unknown",
            "bar_minutes": 5, "csv": HEADER + row() if contents is None else contents, **overrides}


def stamp_seconds(stamp):
    return str(int(datetime.fromisoformat(stamp).timestamp()))


class TradingViewTests(unittest.TestCase):
    def test_reorders_ohlcv_ignores_indicators_and_preserves_raw_fingerprint(self):
        contents = '\ufeffVWAP,Close,time,Volume,Low,OPEN,HIGH,EMA 20\r\n=1+2,101,2025-01-02T14:30:00Z,1000,99,1e2,102,NaN\r\n'
        metadata, canonical = validate_tradingview_import(payload(contents))
        self.assertEqual(canonical, (CANONICAL_HEADER + row("2025-01-02T09:30:00")).encode())
        self.assertEqual(metadata["original_sha256"], hashlib.sha256(contents.encode()).hexdigest())
        self.assertNotEqual(metadata["original_sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(metadata["import_format"], "tradingview")
        self.assertEqual(metadata["ignored_columns"], ["VWAP", "EMA 20"])
        warnings = " ".join(metadata["warnings"])
        self.assertIn("not evaluated", warnings)
        self.assertIn("'VWAP', 'EMA 20'", warnings)
        self.assertIn("not independently verified", warnings)
        self.assertNotIn("No exchange-calendar validation or timezone/DST conversion", warnings)

    def test_unix_seconds_convert_with_actual_new_york_dst_rules(self):
        contents = HEADER + row(stamp_seconds("2025-01-02T14:30:00+00:00")) + row(stamp_seconds("2025-07-02T13:30:00+00:00"))
        metadata, canonical = validate_tradingview_import(payload(contents))
        self.assertEqual(canonical, (CANONICAL_HEADER + row("2025-01-02T09:30:00") + row("2025-07-02T09:30:00")).encode())
        self.assertEqual(metadata["timestamp_format"], "unix_seconds")
        self.assertIn("converted to America/New_York", " ".join(metadata["warnings"]))

    def test_offset_iso_forms_convert_to_the_declared_zone(self):
        contents = HEADER + row("2025-01-02T14:30:00Z") + row("2025-01-02 10:35:00-0400")
        metadata, canonical = validate_tradingview_import(payload(contents))
        self.assertEqual(canonical, (CANONICAL_HEADER + row("2025-01-02T09:30:00") + row("2025-01-02T09:35:00")).encode())
        self.assertEqual(metadata["timestamp_format"], "offset_iso")

    def test_naive_iso_stays_local_with_explicit_warning(self):
        metadata, canonical = validate_tradingview_import(payload(HEADER + row("2025-01-02 09:30")))
        self.assertEqual(canonical, (CANONICAL_HEADER + row("2025-01-02T09:30:00")).encode())
        self.assertEqual(metadata["timestamp_format"], "naive_iso")
        self.assertIn("had no UTC offset", " ".join(metadata["warnings"]))

    def test_daily_dates_are_not_shifted_back_by_utc_midnight_conversion(self):
        with patch.object(tradingview, "ZoneInfo", side_effect=AssertionError("date-only input needs no conversion")):
            metadata, canonical = validate_tradingview_import(payload(HEADER + row("2025-01-02"), bar_minutes=0))
        self.assertEqual(canonical, (CANONICAL_HEADER + row("2025-01-02")).encode())
        self.assertEqual(metadata["timestamp_format"], "date")
        self.assertIn("without timezone conversion", " ".join(metadata["warnings"]))

    def test_daily_instant_uses_declared_local_date(self):
        _, canonical = validate_tradingview_import(payload(HEADER + row("2025-01-03T01:00:00Z"), bar_minutes=0))
        self.assertEqual(canonical, (CANONICAL_HEADER + row("2025-01-02")).encode())

    def test_nonexistent_and_ambiguous_naive_dst_times_are_rejected(self):
        for stamp, message in (("2025-03-09T02:30:00", "nonexistent"), ("2025-11-02T01:30:00", "ambiguous")):
            with self.subTest(stamp=stamp), self.assertRaisesRegex(ValueError, message):
                validate_tradingview_import(payload(HEADER + row(stamp)))
        # The offset disambiguates one fold. No guessed timezone offset is used.
        _, canonical = validate_tradingview_import(payload(HEADER + row("2025-11-02T01:30:00-04:00")))
        self.assertIn(b"2025-11-02T01:30:00", canonical)

    def test_repeated_local_fold_times_fail_canonical_uniqueness(self):
        contents = HEADER + row("2025-11-02T01:30:00-04:00") + row("2025-11-02T01:30:00-05:00")
        with self.assertRaisesRegex(ValueError, "unique and strictly increasing"):
            validate_tradingview_import(payload(contents))

    def test_missing_timezone_database_produces_actionable_error(self):
        with patch.object(tradingview, "ZoneInfo", side_effect=ZoneInfoNotFoundError("fixture")):
            with self.assertRaisesRegex(ValueError, "python -m pip install tzdata"):
                validate_tradingview_import(payload())
            _, canonical = validate_tradingview_import(payload(HEADER + row("2025-01-02T09:30:00Z"), timezone="UTC"))
            self.assertIn(b"2025-01-02T09:30:00", canonical)

    def test_duplicate_ambiguous_missing_or_ragged_headers_rejected(self):
        invalid = ["time,open,high,low,close,Volume,volume\n" + row()[:-1] + ",2\n",
                   "time,date,open,high,low,close,Volume\n" + row()[:-1] + ",2\n",
                   "time,open,high,low,close,Volume MA\n" + row(),
                   "time,open,high,low,close,Volume,EMA,ema\n" + row()[:-1] + ",1,2\n",
                   HEADER + row()[:-1] + ",unexpected\n", HEADER + "2025-01-02T09:30:00,100,102\n",
                   "time,open,high,low,close,Volume,\n" + row()[:-1] + ",2\n"]
        for contents in invalid:
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                validate_tradingview_import(payload(contents))

    def test_mixed_timestamp_modes_and_milliseconds_rejected(self):
        for times in (("2025-01-02T09:30:00-05:00", "1735828500"),
                      ("2025-01-02T09:30:00", "2025-01-02T09:35:00-05:00")):
            with self.subTest(times=times), self.assertRaisesRegex(ValueError, "mixed timestamp"):
                validate_tradingview_import(payload(HEADER + "".join(row(stamp) for stamp in times)))
        with self.assertRaisesRegex(ValueError, "epoch milliseconds"):
            validate_tradingview_import(payload(HEADER + row("1735828200000")))

    def test_bad_times_and_non_minute_starts_rejected(self):
        for stamp in ("01/02/2025 09:30", "09:30", "2025-02-30T09:30:00Z", "NaN", "1735828200.5",
                      "2025-01-02T09:30:01-05:00", "2025-01-02T09:30:00.100-05:00", "2025-01-02"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                validate_tradingview_import(payload(HEADER + row(stamp)))

    def test_standard_validation_still_rejects_ohlcv_formulas_bounds_and_grid(self):
        for values in ("=100,102,99,101,1000", "NaN,102,99,101,1000", "100,98,99,101,1000",
                       "100,102,99,101,-1", "10000001,10000002,99,101,1000"):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_tradingview_import(payload(HEADER + row(prices=values)))
        with self.assertRaisesRegex(ValueError, "aligned"):
            validate_tradingview_import(payload(HEADER + row("2025-01-02T09:31:00-05:00")))

    def test_input_size_row_count_utf8_and_provenance_limits(self):
        with patch.object(tradingview, "MAX_CSV_BYTES", 10), self.assertRaisesRegex(ValueError, "8 MiB"):
            validate_tradingview_import(payload())
        with patch.object(tradingview, "MAX_ROWS", 1), self.assertRaisesRegex(ValueError, "100000-row"):
            validate_tradingview_import(payload(HEADER + row() + row("2025-01-02T09:35:00-05:00")))
        for contents in ("", HEADER, HEADER + row("\ud800")):
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                validate_tradingview_import(payload(contents))
        extras = [f"indicator-{index}-" + "x" * 100 for index in range(80)]
        contents = HEADER.rstrip() + "," + ",".join(extras) + "\n" + row().rstrip() + "," + ",".join("1" for _ in extras) + "\n"
        with self.assertRaisesRegex(ValueError, "provenance limit"):
            validate_tradingview_import(payload(contents))

    def test_bad_payload_metadata_and_unknown_fields_are_rejected(self):
        for value in (None, [], {"csv": HEADER + row()}, payload(path="secret"), payload(format="tradingview"),
                      payload(csv=b"bytes"), payload(bar_minutes=True), payload(currency="US"),
                      payload(timezone="NoSuch/Zone")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_tradingview_import(value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
