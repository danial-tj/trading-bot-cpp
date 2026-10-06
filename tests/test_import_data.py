"""Deterministic offline checks for bounded historical CSV imports."""
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service import import_data
from service.import_data import validate_import

HEADER = "timestamp,open,high,low,close,volume\n"


def row(stamp="2025-01-02", open_="100", high="102", low="99", close="101", volume="1000"):
    return ",".join([stamp, open_, high, low, close, volume]) + "\n"


def payload(contents=None, **overrides):
    value = {
        "name": "Historical example",
        "symbol": "aapl",
        "source": "User-supplied broker export",
        "timezone": "America/New_York",
        "currency": "usd",
        "price_adjustment": "unknown",
        "csv": HEADER + row() if contents is None else contents,
    }
    value.update(overrides)
    return value


def warnings(metadata):
    return " ".join(metadata["warnings"])


class ImportDataTests(unittest.TestCase):
    def test_daily_defaults_and_origin_do_not_claim_verification(self):
        metadata, canonical = validate_import(payload())
        self.assertEqual(canonical, (HEADER + row()).encode())
        self.assertEqual(metadata["origin"], "imported")
        self.assertEqual(metadata["kind"], "daily")
        self.assertEqual(metadata["interval_kind"], "daily")
        self.assertEqual(metadata["symbol"], "AAPL")
        self.assertEqual(metadata["currency"], "USD")
        self.assertEqual(metadata["bar_minutes"], 0)
        self.assertEqual(metadata["row_count"], 1)
        self.assertEqual(metadata["first_timestamp"], "2025-01-02")
        self.assertIn("not independently verified", metadata["description"])
        self.assertIn("No exchange-calendar validation", warnings(metadata))
        self.assertIn("user-declared label", warnings(metadata))

    def test_intraday_normalizes_timestamp_header_bom_and_numeric_spellings(self):
        contents = '\ufeffDateTime,OPEN,High,Low,Close,Volume\r\n"2025-01-02 09:30",1e2,102.000,99.0,101.00,-0\r\n'
        metadata, canonical = validate_import(payload(contents, bar_minutes=2))
        self.assertEqual(canonical, (HEADER + row("2025-01-02T09:30:00", volume="0")).encode())
        self.assertEqual(metadata["kind"], "intraday")
        self.assertEqual(metadata["original_sha256"], hashlib.sha256(contents.encode("utf-8")).hexdigest())
        self.assertIn("zero-volume", warnings(metadata))
        self.assertIn("incomplete regular-session", warnings(metadata))
        self.assertIn("Short monthly warmup", warnings(metadata))

    def test_normalization_does_not_round_against_decimal_context(self):
        precise = "1.123456789012345678901234567890123456789"
        _, canonical = validate_import(payload(HEADER + row(open_=precise, high=precise, low=precise, close=precise)))
        self.assertEqual(canonical.decode().count(precise), 4)

    def test_exact_source_fingerprint_is_separate_from_canonical_csv(self):
        a = payload(HEADER + row())
        b = payload((HEADER + row()).replace("\n", "\r\n"))
        ma, ca = validate_import(a)
        mb, cb = validate_import(b)
        self.assertEqual(ca, cb)
        self.assertNotEqual(ma["original_sha256"], mb["original_sha256"])
        self.assertEqual(validate_import(a), (ma, ca))

    def test_requires_explicit_provenance_fields(self):
        for key in import_data.REQUIRED:
            with self.subTest(key=key):
                value = payload()
                del value[key]
                with self.assertRaisesRegex(ValueError, "missing"):
                    validate_import(value)

    def test_rejects_unknown_fields_and_non_object(self):
        for value in (None, [], "file.csv"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "object"):
                validate_import(value)
        for key in ("path", "url", "origin", "sha256", "api_key"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "unsupported"):
                validate_import(payload(**{key: "ignored"}))

    def test_metadata_lengths_controls_and_symbols_are_bounded(self):
        bad = [
            {"name": ""}, {"source": "\n"}, {"source": "x\nhidden"},
            {"name": "x" * 81}, {"source": "x" * 501},
            {"symbol": "../AAPL"}, {"symbol": "=AAPL"}, {"symbol": "AAPL/US"},
            {"symbol": "A" * 25}, {"currency": "US"}, {"currency": "12$"},
            {"name": "\ud800"},
        ]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate_import(payload(**overrides))
        metadata, _ = validate_import(payload(symbol="brk.b", name=" My data "))
        self.assertEqual(metadata["symbol"], "BRK.B")
        self.assertEqual(metadata["name"], "My data")

    def test_timezone_is_a_label_with_no_offset_or_path_syntax(self):
        for zone in ("UTC", "America/New_York", "America/Argentina/Buenos_Aires", "Etc/GMT+5"):
            metadata, _ = validate_import(payload(timezone=zone))
            self.assertEqual(metadata["timezone"], zone)
        for zone in ("", "-04:00", "Z", "../../etc", r"C:\market", "America/New York", "https://example.com"):
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                validate_import(payload(timezone=zone))

    def test_adjustment_enum_and_unknown_warning(self):
        for adjustment in import_data.ADJUSTMENTS:
            metadata, _ = validate_import(payload(price_adjustment=adjustment))
            self.assertEqual("Price adjustment is unknown" in warnings(metadata), adjustment == "unknown")
        with self.assertRaises(ValueError):
            validate_import(payload(price_adjustment="verified_adjusted"))

    def test_interval_and_session_values_are_strict_integers(self):
        for interval in (-1, 31, 2.0, True, "2"):
            with self.subTest(interval=interval), self.assertRaisesRegex(ValueError, "bar_minutes"):
                validate_import(payload(bar_minutes=interval))
        for overrides in (
            {"session_open_minute": 570.0}, {"session_close_minute": False},
            {"session_open_minute": 960}, {"session_close_minute": 570},
            {"session_open_minute": -1}, {"session_close_minute": 1440},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate_import(payload(**overrides))
        with self.assertRaisesRegex(ValueError, "divide"):
            validate_import(payload(HEADER + row("2025-01-02T09:30"), bar_minutes=4))

    def test_csv_must_be_utf8_string_with_bounded_encoded_bytes(self):
        for contents in (b"binary", None, 42, "\ud800"):
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                validate_import(payload(csv=contents))
        with self.assertRaisesRegex(ValueError, "8 MiB"):
            validate_import(payload("x" * (import_data.MAX_CSV_BYTES + 1)))
        with patch.object(import_data, "MAX_CSV_BYTES", 2):
            with self.assertRaisesRegex(ValueError, "8 MiB"):
                validate_import(payload("\u20ac"))

    def test_empty_header_only_and_blank_rows_cannot_be_valid_data(self):
        for contents in ("", HEADER, HEADER + "\n \n", "\n"):
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                validate_import(payload(contents))
        metadata, _ = validate_import(payload(HEADER + "\n" + row() + "\n"))
        self.assertEqual(metadata["row_count"], 1)

    def test_header_and_column_count_are_exact(self):
        for contents in (
            "time,open,high,low,close,volume\n" + row(),
            "timestamp,high,open,low,close,volume\n" + row(),
            HEADER + "2025-01-02,100,102,99,101\n",
            HEADER + "2025-01-02,100,102,99,101,1000,extra\n",
            HEADER + "2025-01-02;100;102;99;101;1000\n",
            HEADER + '"2025-01-02,100,102,99,101,1000\n',
        ):
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                validate_import(payload(contents))

    def test_invalid_dates_and_leap_years(self):
        for stamp in ("2025-02-29", "1900-02-29", "2025-13-01", "2025-00-01", "2025-01-00", "1899-12-31", "2025-1-02"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                validate_import(payload(HEADER + row(stamp)))
        metadata, _ = validate_import(payload(HEADER + row("2000-02-29")))
        self.assertEqual(metadata["first_timestamp"], "2000-02-29")

    def test_intraday_requires_zero_seconds_and_naive_time(self):
        for stamp in (
            "2025-01-02T25:00", "2025-01-02T09:60", "2025-01-02T09:30:01",
            "2025-01-02T09:30:00.000", "2025-01-02T09:30:00Z",
            "2025-01-02T09:30:00-05:00", "2025-01-02T09:30+00:00",
        ):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                validate_import(payload(HEADER + row(stamp), bar_minutes=2))

    def test_daily_and_intraday_cannot_be_mixed_or_misdeclared(self):
        for contents, interval in (
            (HEADER + row("2025-01-02T09:30"), 0),
            (HEADER + row(), 2),
            (HEADER + row("2025-01-02T09:30") + row("2025-01-03"), 2),
            (HEADER + row("2025-01-02") + row("2025-01-03T09:30"), 0),
        ):
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                validate_import(payload(contents, bar_minutes=interval))

    def test_duplicate_and_unordered_timestamps_are_rejected(self):
        for contents in (
            HEADER + row() + row(),
            HEADER + row("2025-01-03") + row("2025-01-02"),
        ):
            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                validate_import(payload(contents))
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            validate_import(payload(HEADER + row("2025-01-02 09:30") + row("2025-01-02T09:30:00"), bar_minutes=2))

    def test_bar_start_must_align_with_declared_interval(self):
        with self.assertRaisesRegex(ValueError, "aligned"):
            validate_import(payload(HEADER + row("2025-01-02T09:31"), bar_minutes=2))
        with self.assertRaisesRegex(ValueError, "aligned"):
            validate_import(payload(HEADER + row("2025-01-02T09:30") + row("2025-01-02T09:33"), bar_minutes=2))
        _, canonical = validate_import(payload(HEADER + row("2025-01-02T09:31"), bar_minutes=1))
        self.assertIn(b"09:31:00", canonical)

    def test_same_day_gaps_warn_without_fabricating_rows(self):
        contents = HEADER + row("2025-01-02T09:30") + row("2025-01-02T09:36")
        metadata, canonical = validate_import(payload(contents, bar_minutes=2))
        self.assertEqual(metadata["row_count"], 2)
        self.assertIn("omit 2 declared bar interval", warnings(metadata))
        self.assertEqual(len(canonical.splitlines()), 3)

    def test_missing_whole_sessions_are_not_invented_by_calendar_guessing(self):
        contents = HEADER + row("2025-01-02T09:30") + row("2025-01-06T09:30")
        metadata, _ = validate_import(payload(contents, bar_minutes=2))
        self.assertNotIn("Observed same-day gaps", warnings(metadata))
        self.assertIn("Missing whole sessions", warnings(metadata))

    def test_complete_observed_regular_session_has_no_incomplete_warning(self):
        start = datetime(2025, 1, 2, 9, 30)
        contents = HEADER + "".join(row((start + timedelta(minutes=minute)).isoformat()) for minute in range(0, 390, 2))
        metadata, _ = validate_import(payload(contents, bar_minutes=2))
        self.assertEqual(metadata["row_count"], 195)
        self.assertNotIn("incomplete regular-session", warnings(metadata))
        self.assertNotIn("Observed same-day gaps", warnings(metadata))

    def test_extended_hours_remain_in_snapshot_but_are_labeled(self):
        contents = HEADER + row("2025-01-02T09:28") + row("2025-01-02T09:30") + row("2025-01-02T16:00")
        metadata, canonical = validate_import(payload(contents, bar_minutes=2))
        self.assertIn("2 extended-hours row", warnings(metadata))
        self.assertIn("excludes bars outside", warnings(metadata))
        self.assertIn(b"09:28:00", canonical)
        self.assertIn(b"16:00:00", canonical)

    def test_monthly_warmup_warning_is_bounded_and_not_a_completeness_claim(self):
        contents = HEADER + "".join(row(f"2025-{month:02}-02T09:30") for month in range(1, 6))
        metadata, _ = validate_import(payload(contents, bar_minutes=2))
        self.assertNotIn("Short monthly warmup", warnings(metadata))
        self.assertIn("incomplete regular-session", warnings(metadata))
        self.assertLess(len(warnings(metadata)), 4000)

    def test_nonfinite_formulas_and_unsafe_number_lexemes_fail(self):
        for number in ("NaN", "Infinity", "-inf", "1_000", "=100", "+SUM(1)", "0x64", "100oops", "", "--1"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                validate_import(payload(HEADER + row(open_=number)))
        with self.assertRaises(ValueError):
            validate_import(payload(HEADER + row(volume="=1+1")))

    def test_engine_safe_numeric_bounds_and_expansion_limits(self):
        for number in ("0", "-1", ".004", "10000001", "1e300", "1e-100000"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                validate_import(payload(HEADER + row(open_=number)))
        for volume in ("-1", "1000000000000001", "0e99999", "1e-101"):
            with self.subTest(volume=volume), self.assertRaises(ValueError):
                validate_import(payload(HEADER + row(volume=volume)))
        metadata, _ = validate_import(payload(HEADER + row(
            open_="10000000", high="10000000", low=".005", close=".005", volume="1e15")))
        self.assertEqual(metadata["row_count"], 1)

    def test_ohlc_bounds_are_checked_before_snapshot_publication(self):
        for overrides in (
            {"high": "99"}, {"low": "102"}, {"open_": "103"},
            {"close": "103"}, {"high": "98", "low": "99"},
        ):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, "OHLC"):
                validate_import(payload(HEADER + row(**overrides)))

    def test_zero_volume_is_normalized_retained_and_explained(self):
        for number in ("0.00000", "-0", "+0", "0e5"):
            metadata, canonical = validate_import(payload(HEADER + row(volume=number)))
            self.assertTrue(canonical.endswith(b",0\n"))
            self.assertIn("zero-volume", warnings(metadata))

    def test_row_limit_is_enforced_without_returning_partial_metadata(self):
        self.assertEqual(import_data.MAX_ROWS, 100000)
        contents = HEADER + row("2025-01-01") + row("2025-01-02") + row("2025-01-03")
        with patch.object(import_data, "MAX_ROWS", 2):
            with self.assertRaisesRegex(ValueError, "100000-row"):
                validate_import(payload(contents))

    def test_canonical_expansion_cannot_exceed_snapshot_byte_limit(self):
        contents = HEADER + row(volume="1e-100")
        with patch.object(import_data, "MAX_CSV_BYTES", len(contents) + 1):
            with self.assertRaisesRegex(ValueError, "Canonical CSV"):
                validate_import(payload(contents))

    def test_canonical_snapshot_contains_no_formulas_quotes_or_external_locations(self):
        contents = '"Date","Open","High","Low","Close","Volume"\n"2025-01-02","100","102","99","101","1e3"\n'
        metadata, canonical = validate_import(payload(contents, source="https://example.invalid/user-declared-export"))
        self.assertEqual(canonical, (HEADER + row()).encode())
        self.assertNotIn(b"https", canonical)
        self.assertNotIn(b'"', canonical)
        self.assertEqual(metadata["source"], "https://example.invalid/user-declared-export")


if __name__ == "__main__":
    unittest.main(verbosity=2)

