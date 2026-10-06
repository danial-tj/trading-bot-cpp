"""Credential-free provider tests: all network responses are simulated."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import ssl
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service import questrade as q

UTC = timezone.utc
NOW = datetime(2025, 12, 1, 18, tzinfo=UTC)


def details(**changes):
    result = {"symbolId": 8049, "symbol": "AAPL", "description": "APPLE INC",
              "securityType": "Stock", "listingExchange": "NASDAQ", "currency": "USD"}
    result.update(changes)
    return result


def candle(start="2025-01-02T09:30:00-05:00", minutes=2, **changes):
    end = datetime.fromisoformat(start) + timedelta(minutes=minutes)
    result = {"start": start, "end": end.isoformat(), "open": 100, "high": 102,
              "low": 99, "close": 101, "volume": 1000}
    result.update(changes)
    return result


def params(**changes):
    result = {"symbol_id": 8049, "start_date": "2025-01-02", "end_date": "2025-01-02", "bar_minutes": 2}
    result.update(changes)
    return result


class Transport:
    def __init__(self, bars=None, symbol=None):
        self.calls = []
        self.auth_count = 0
        self.api_count = 0
        self.bars = bars if bars is not None else [candle()]
        self.symbol = symbol if symbol is not None else details()
        self.auth_override = None
        self.api_override = None

    def __call__(self, url, headers, timeout, maximum):
        self.calls.append((url, dict(headers), timeout, maximum))
        if urlsplit(url).hostname == "login.questrade.com":
            self.auth_count += 1
            if self.auth_override:
                return self.auth_override(url, headers)
            return {"access_token": f"access-{self.auth_count}", "refresh_token": f"refresh-{self.auth_count}",
                    "token_type": "Bearer", "expires_in": 300,
                    "api_server": "https://api01.iq.questrade.com"}
        self.api_count += 1
        if self.api_override:
            response = self.api_override(url, headers)
            if response is not None:
                return response
        if urlsplit(url).path.endswith("symbols/search"):
            return {"symbols": [self.symbol]}
        if "/symbols/" in urlsplit(url).path:
            return {"symbols": [self.symbol]}
        if "/markets/candles/" in urlsplit(url).path:
            bars = self.bars(url) if callable(self.bars) else self.bars
            return {"candles": bars}
        raise AssertionError("Unexpected endpoint")


def client(transport=None, now=None):
    transport = transport or Transport()
    result = q.QuestradeClient(transport, now or (lambda: NOW), request_interval=0)
    result.connect("manual-refresh-secret")
    return result, transport


class AuthorizationTests(unittest.TestCase):
    def test_status_and_search_never_expose_credentials(self):
        connection, transport = client()
        status = connection.status()
        self.assertEqual(set(status), {"provider", "connected", "state", "expires_at", "can_refresh", "history_progress"})
        self.assertTrue(status["connected"])
        self.assertEqual(connection.search_symbols("AAP"), [{"id": 8049, "symbol": "AAPL", "description": "APPLE INC", "currency": "USD"}])
        encoded = json.dumps(status)
        for secret in ("manual-refresh-secret", "access-1", "refresh-1", "api01"):
            self.assertNotIn(secret, encoded)
        self.assertTrue(all(call[2] == 15 for call in transport.calls))
        self.assertTrue(all(call[3] <= q.MAX_RESPONSE_BYTES for call in transport.calls))
        self.assertEqual(urlsplit(transport.calls[-1][0]).path, "/v1/symbols/search")

    def test_bad_input_tokens_do_not_reach_network_or_replace_session(self):
        connection, transport = client()
        for bad in (None, "", "abc def", "abc\n", "abc\r", "ä", "abc&secret=oops", "x" * 4097):
            with self.subTest(token_type=type(bad).__name__), self.assertRaises(q.QuestradeError):
                connection.connect(bad)
        self.assertEqual(transport.auth_count, 1)
        self.assertTrue(connection.status()["connected"])

    def test_refresh_rotates_once_across_concurrent_requests(self):
        clock = [NOW]
        connection, transport = client(now=lambda: clock[0])
        clock[0] += timedelta(seconds=280)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: connection.search_symbols("AAPL"), range(16)))
        self.assertEqual(len(results), 16)
        self.assertEqual(transport.auth_count, 2)
        auth = [url for url, *_ in transport.calls if "oauth2/token" in url]
        self.assertEqual(parse_qs(urlsplit(auth[-1]).query)["refresh_token"], ["refresh-1"])
        self.assertEqual(transport.calls[-1][1]["Authorization"], "Bearer access-2")

    def test_expired_status_is_honest_before_refresh(self):
        clock = [NOW]
        connection, _ = client(now=lambda: clock[0])
        clock[0] += timedelta(seconds=301)
        self.assertEqual(connection.status()["state"], "expired")
        self.assertFalse(connection.status()["connected"])
        self.assertTrue(connection.status()["can_refresh"])
        connection.search_symbols("AAPL")
        self.assertTrue(connection.status()["connected"])

    def test_401_refreshes_and_retries_only_once(self):
        connection, transport = client()
        def unauthorised(url, headers):
            if headers["Authorization"] == "Bearer access-1":
                raise q._HTTPFailure(401)
        transport.api_override = unauthorised
        self.assertEqual(connection.search_symbols("AAPL")[0]["symbol"], "AAPL")
        self.assertEqual(transport.auth_count, 2)
        transport.api_override = lambda *_: (_ for _ in ()).throw(q._HTTPFailure(401))
        with self.assertRaises(q.QuestradeError):
            connection.search_symbols("AAPL")
        self.assertEqual(transport.auth_count, 3)
        self.assertFalse(connection.status()["connected"])

    def test_refresh_failure_discards_potentially_consumed_credentials(self):
        clock = [NOW]
        connection, transport = client(now=lambda: clock[0])
        clock[0] += timedelta(seconds=280)
        transport.auth_override = lambda *_: (_ for _ in ()).throw(RuntimeError("LEAK refresh-1"))
        with self.assertRaises(q.QuestradeError) as caught:
            connection.search_symbols("AAP")
        self.assertNotIn("LEAK", str(caught.exception))
        self.assertNotIn("refresh-1", str(caught.exception))
        self.assertFalse(connection.status()["can_refresh"])

    def test_cancel_after_rotated_response_does_not_retain_consumed_refresh_token(self):
        clock = [NOW]
        connection, transport = client(now=lambda: clock[0])
        clock[0] += timedelta(seconds=280)
        cancel = threading.Event()
        original = connection._exchange
        def rotate_then_cancel(*args):
            session = original(*args)
            cancel.set()
            return session
        with patch.object(connection, "_exchange", rotate_then_cancel):
            with self.assertRaises(q.QuestradeCancelled):
                connection.historical_import(params(), cancel)
        self.assertEqual(transport.auth_count, 2)
        self.assertFalse(connection.status()["can_refresh"])
        self.assertFalse(connection.status()["connected"])

    def test_authentication_host_is_strictly_allowlisted(self):
        for server in ("http://api01.iq.questrade.com/", "https://api01.iq.questrade.com.evil.test/",
                       "https://api01.iq.questrade.com:443/", "https://x@api01.iq.questrade.com/",
                       "https://api01.iq.questrade.com/v1/", "https://api01.iq.questrade.com/?x=1",
                       "https://api01.iq.questrade.com/#x", "https://127.0.0.1/", "https://api.iq.questrade.com/"):
            transport = Transport()
            transport.auth_override = lambda *_, value=server: {"access_token": "a", "refresh_token": "r", "expires_in": 300, "api_server": value}
            connection = q.QuestradeClient(transport, lambda: NOW, 0)
            with self.subTest(server=server), self.assertRaises(q.QuestradeError):
                connection.connect("manual")
            self.assertFalse(connection.status()["connected"])
            self.assertEqual(transport.api_count, 0)

    def test_invalid_authorization_payload_rejected_without_secret_errors(self):
        for field, value in (("access_token", "secret\n"), ("refresh_token", "secret\r"),
                             ("expires_in", True), ("expires_in", 0), ("expires_in", 999999),
                             ("token_type", "not-Bearer")):
            data = {"access_token": "a", "refresh_token": "r", "expires_in": 300,
                    "api_server": "https://api123.iq.questrade.com/", "token_type": "Bearer"}
            data[field] = value
            connection = q.QuestradeClient(lambda *_: data, lambda: NOW, 0)
            with self.subTest(field=field), self.assertRaises(q.QuestradeError) as caught:
                connection.connect("manual")
            self.assertNotIn("secret", str(caught.exception))

    def test_status_disconnect_do_not_wait_for_network_and_fence_response(self):
        entered, release = threading.Event(), threading.Event()
        connection, transport = client()
        def blocked(url, headers):
            entered.set()
            self.assertTrue(release.wait(3))
            return {"symbols": [details()]}
        transport.api_override = blocked
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(connection.search_symbols, "AAPL")
            self.assertTrue(entered.wait(1))
            before = time.monotonic()
            self.assertTrue(connection.status()["connected"])
            self.assertFalse(connection.disconnect()["connected"])
            self.assertLess(time.monotonic() - before, .25)
            release.set()
            with self.assertRaises(q.QuestradeCancelled):
                request.result(2)

    def test_reconnect_cannot_be_overwritten_by_stale_connect(self):
        entered, release = threading.Event(), threading.Event()
        transport = Transport()
        calls = [0]
        def auth(url, headers):
            calls[0] += 1
            if calls[0] == 1:
                entered.set()
                self.assertTrue(release.wait(3))
            return {"access_token": f"access-{calls[0]}", "refresh_token": f"refresh-{calls[0]}",
                    "expires_in": 300, "api_server": "https://api01.iq.questrade.com"}
        transport.auth_override = auth
        connection = q.QuestradeClient(transport, lambda: NOW, 0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(connection.connect, "old")
            self.assertTrue(entered.wait(1))
            new = pool.submit(connection.connect, "new")
            # Wait for the second connect to fence the first, without assuming
            # which worker the executor schedules first after release.
            deadline = time.monotonic() + 1
            while connection._generation < 2 and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(connection._generation, 2)
            release.set()
            with self.assertRaises(q.QuestradeCancelled):
                old.result(2)
            self.assertTrue(new.result(2)["connected"])
        connection.search_symbols("AAP")
        self.assertEqual(transport.calls[-1][1]["Authorization"], "Bearer access-2")

    def test_private_api_rejects_account_or_order_paths(self):
        connection, transport = client()
        for path in ("accounts", "accounts/1/orders", "markets/quotes/8049", "../accounts"):
            with self.assertRaises(q.QuestradeError):
                connection._api(path, {}, connection._generation)
        self.assertEqual(transport.api_count, 0)


class TransportTests(unittest.TestCase):
    class Response:
        def __init__(self, raw, length=None):
            self.raw = raw
            self.headers = {} if length is None else {"Content-Length": length}
            self.requested = None
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self, limit):
            self.requested = limit
            return self.raw[:limit]

    def test_tls_verified_redirects_refused_and_read_is_bounded(self):
        response = self.Response(b'{"price":1.25}')
        with patch.object(q, "build_opener") as opener:
            opener.return_value.open.return_value = response
            data = q._http_json("https://api01.iq.questrade.com/v1/symbols/1", {}, 15, 100)
        self.assertEqual(data["price"], Decimal("1.25"))
        self.assertEqual(response.requested, 101)
        redirect, https = opener.call_args.args
        self.assertIsNone(redirect.redirect_request(None, None, 302, "", {}, "https://evil.test"))
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)

    def test_oversize_and_malformed_json_fail_safely(self):
        for response in (self.Response(b"x" * 101), self.Response(b"{}", "999"), self.Response(b"{}", "bad"),
                         self.Response(b'{"a":1,"a":2}'), self.Response(b'{"a":NaN}'),
                         self.Response(b"[]"), self.Response(b"\xff")):
            with self.subTest(raw=response.raw[:20]), patch.object(q, "build_opener") as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(q.QuestradeError):
                    q._http_json("https://api01.iq.questrade.com/", {}, 15, 100)

    def test_http_and_network_errors_never_echo_url_body_or_credentials(self):
        secret = "SECRET-TOKEN"
        failures = [HTTPError("https://host/?token=" + secret, code, secret, {}, io.BytesIO(secret.encode()))
                    for code in (302, 401, 403, 429, 500)]
        failures += [URLError(secret), TimeoutError(secret), OSError(secret)]
        for failure in failures:
            with self.subTest(type=type(failure).__name__), patch.object(q, "build_opener") as opener:
                opener.return_value.open.side_effect = failure
                with self.assertRaises(q.QuestradeError) as caught:
                    q._http_json("https://host/?token=" + secret, {}, 15, 100)
                self.assertNotIn(secret, str(caught.exception))


class HistoricalTests(unittest.TestCase):
    def test_preflight_is_pure_and_rejects_invalid_dates_bounds_and_types(self):
        good = params(start_date="2024-12-01", end_date="2025-12-01", bar_minutes=0)
        self.assertEqual(q.validate_history_params(good, NOW), good)
        bad = [params(symbol_id=True), params(symbol_id=0), params(symbol_id=2**31), params(bar_minutes=3),
               params(bar_minutes=True), params(end_date="2025-12-02"), params(start_date="2025-01-03"),
               params(start_date="2024-11-30", end_date="2025-12-01"), params(start_date="1899-01-01"),
               params(start_date="2025-02-30"), params(start_date="2025-1-2"), params(extra="x")]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(q.QuestradeError):
                q.validate_history_params(value, NOW)

    def test_timezone_database_is_required(self):
        with patch.object(q, "ZoneInfo", side_effect=q.ZoneInfoNotFoundError):
            with self.assertRaisesRegex(q.QuestradeError, "tzdata"):
                q.validate_history_params(params(), NOW)

    def test_full_import_metadata_csv_zero_volume_and_progress(self):
        connection, _ = client(Transport([candle(volume=0), candle("2025-01-02T09:32:00-05:00", open=101)]))
        metadata, content = connection.historical_import(params())
        self.assertEqual(metadata["row_count"], 2)
        self.assertEqual(metadata["provider"], "Questrade")
        self.assertEqual(metadata["provider_symbol_id"], 8049)
        self.assertEqual(metadata["origin"], "imported")
        self.assertEqual(metadata["currency"], "USD")
        self.assertEqual(metadata["timezone"], "America/New_York")
        self.assertEqual(metadata["price_adjustment"], "unknown")
        self.assertEqual(metadata["requested_start_date"], "2025-01-02")
        self.assertIn(b"2025-01-02T09:30:00,100,102,99,101,0\n", content)
        self.assertTrue(any("zero-volume" in warning for warning in metadata["warnings"]))
        self.assertTrue(any("converted" in warning for warning in metadata["warnings"]))
        self.assertFalse(any("user-provided" in warning for warning in metadata["warnings"]))
        self.assertEqual(connection.status()["history_progress"], {"completed_chunks": 1, "total_chunks": 1, "rows": 2})
        self.assertEqual(connection.historical_import(params()), (metadata, content))

    def test_symbol_and_currency_come_from_detail_response(self):
        connection, _ = client(Transport(symbol=details(symbol="BMO.TO", currency="CAD", listingExchange="TSX")))
        metadata, _ = connection.historical_import(params())
        self.assertEqual(metadata["symbol"], "BMO.TO")
        self.assertEqual(metadata["currency"], "CAD")
        with self.assertRaises(q.QuestradeError):
            connection.historical_import(params(symbol="UNTRUSTED", currency="USD"))
        for change in ({"currency": None}, {"currency": "EUR"}, {"securityType": "Option"},
                       {"listingExchange": "PinkSheets"}, {"symbolId": 123}, {"symbol": "bad,formula"}):
            connection, _ = client(Transport(symbol=details(**change)))
            with self.subTest(change=change), self.assertRaises(q.QuestradeError):
                connection.historical_import(params())

    def test_search_filters_unsupported_securities_and_bounds_results(self):
        connection, transport = client()
        transport.api_override = lambda *_: {"symbols": [details(securityType="Option"), details(listingExchange="OTCBB")] +
                                            [details(symbolId=number) for number in range(1, 100)]}
        self.assertEqual(len(connection.search_symbols("A")), 30)
        self.assertEqual(set(connection.search_symbols("A")[0]), {"id", "symbol", "description", "currency"})
        for value in ("", "a\n", "a&b", "x" * 41, None):
            with self.assertRaises(q.QuestradeError):
                connection.search_symbols(value)

    def test_one_and_five_minute_windows_stay_below_provider_limit(self):
        for interval, count in ((1, 3), (5, 1)):
            bars = [candle(f"2025-01-0{day}T09:30:00-05:00", minutes=interval) for day in (2, 3, 4)]
            def windows(url):
                query = parse_qs(urlsplit(url).query)
                start = datetime.fromisoformat(query["startTime"][0])
                end = datetime.fromisoformat(query["endTime"][0])
                self.assertLessEqual((end - start).total_seconds(), interval * 60 * 1900)
                return [bar for bar in bars if start <= datetime.fromisoformat(bar["start"]) <= end]
            connection, transport = client(Transport(windows))
            metadata, _ = connection.historical_import(params(bar_minutes=interval, end_date="2025-01-04"))
            candle_calls = [call for call in transport.calls if "/candles/" in call[0]]
            self.assertEqual(len(candle_calls), count)
            self.assertEqual(metadata["row_count"], 3)
            self.assertEqual(parse_qs(urlsplit(candle_calls[0][0]).query)["interval"], [q.INTERVALS[interval]])

    def test_dst_offsets_convert_to_the_same_regular_session_wall_time(self):
        for starts, first, last in ((["2025-03-07T14:30:00Z", "2025-03-10T13:30:00Z"], "2025-03-07", "2025-03-10"),
                                    (["2025-10-31T13:30:00Z", "2025-11-03T14:30:00Z"], "2025-10-31", "2025-11-03")):
            connection, _ = client(Transport([candle(start, minutes=5) for start in starts]))
            metadata, content = connection.historical_import(params(bar_minutes=5, start_date=first, end_date=last))
            self.assertEqual(metadata["row_count"], 2)
            self.assertIn((first + "T09:30:00").encode(), content)
            self.assertIn((last + "T09:30:00").encode(), content)

    def test_daily_candles_across_dst_keep_exchange_date(self):
        bars = [candle("2025-03-09T00:00:00-05:00", end="2025-03-10T00:00:00-04:00"),
                candle("2025-03-10T00:00:00-04:00", end="2025-03-11T00:00:00-04:00")]
        connection, _ = client(Transport(bars))
        metadata, content = connection.historical_import(params(bar_minutes=0, start_date="2025-03-09", end_date="2025-03-10"))
        self.assertEqual(metadata["kind"], "daily")
        self.assertIn(b"2025-03-09,100", content)
        self.assertNotIn(b"T00:00", content)

    def test_today_is_clipped_and_incomplete_candle_is_excluded(self):
        now = datetime(2025, 1, 2, 14, 33, tzinfo=UTC)
        bars = [candle(), candle("2025-01-02T09:32:00-05:00")]
        connection, transport = client(Transport(bars), lambda: now)
        metadata, _ = connection.historical_import(params())
        self.assertEqual(metadata["row_count"], 1)
        self.assertTrue(any("unfinished" in warning for warning in metadata["warnings"]))
        query = parse_qs(urlsplit(transport.calls[-1][0]).query)
        self.assertEqual(datetime.fromisoformat(query["endTime"][0]).astimezone(UTC), now)

    def test_identical_overlapping_candles_deduplicate_but_conflicts_fail(self):
        # First window of 1900 minutes ends at 07:40 on day two.
        bar = candle("2025-01-03T07:40:00-05:00", minutes=1)
        connection, _ = client(Transport([bar]))
        metadata, _ = connection.historical_import(params(bar_minutes=1, end_date="2025-01-03"))
        self.assertEqual(metadata["row_count"], 1)
        count = [0]
        def conflicting(url):
            count[0] += 1
            return [{**bar, "volume": count[0]}]
        connection, _ = client(Transport(conflicting))
        with self.assertRaisesRegex(q.QuestradeError, "conflicting duplicate"):
            connection.historical_import(params(bar_minutes=1, end_date="2025-01-03"))

    def test_empty_windows_warn_and_no_bars_fail(self):
        count = [0]
        def windows(url):
            count[0] += 1
            return [candle(minutes=1)] if count[0] == 1 else []
        connection, _ = client(Transport(windows))
        metadata, _ = connection.historical_import(params(bar_minutes=1, end_date="2025-01-03"))
        self.assertTrue(any("no candles" in warning for warning in metadata["warnings"]))
        connection, _ = client(Transport([]))
        with self.assertRaisesRegex(q.QuestradeError, "no completed candles"):
            connection.historical_import(params())

    def test_provider_limit_and_storage_limits_abort(self):
        connection, _ = client(Transport([candle()] * 2000))
        with self.assertRaisesRegex(q.QuestradeError, "2000-candle"):
            connection.historical_import(params())
        connection, _ = client(Transport([candle(), candle("2025-01-02T09:32:00-05:00")]))
        with patch.object(q, "MAX_ROWS", 1), self.assertRaisesRegex(q.QuestradeError, "100000-row"):
            connection.historical_import(params())
        with patch.object(q, "MAX_CSV_BYTES", 40), self.assertRaisesRegex(q.QuestradeError, "8 MiB"):
            connection.historical_import(params())

    def test_invalid_candles_fail_without_partial_dataset(self):
        bad = [candle(low=200), candle(volume=-1), candle(open=Decimal("NaN")), candle(close="101"),
               candle(open=True), candle(start="2025-01-02T09:30:01-05:00"),
               candle(start="2025-01-02T09:30:00"), candle(start="2025-01-02T09:30:00+09:00"),
               candle(end="2025-01-02T09:35:00-05:00"), candle(start="2024-12-30T09:30:00-05:00")]
        for bar in bad:
            connection, _ = client(Transport([candle(), bar]))
            with self.subTest(bar=bar), self.assertRaises(q.QuestradeError):
                connection.historical_import(params())

    def test_partial_network_failure_never_returns_dataset(self):
        count = [0]
        def windows(url):
            count[0] += 1
            if count[0] == 2:
                raise q._HTTPFailure(429)
            return [candle(minutes=1)]
        connection, _ = client(Transport(windows))
        with self.assertRaisesRegex(q.QuestradeError, "request limit"):
            connection.historical_import(params(bar_minutes=1, end_date="2025-01-03"))
        self.assertEqual(connection.status()["history_progress"]["completed_chunks"], 1)

    def test_cancellation_and_disconnect_prevent_returning_download(self):
        for disconnect in (False, True):
            entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
            def blocked(url):
                entered.set()
                self.assertTrue(release.wait(3))
                return [candle()]
            connection, _ = client(Transport(blocked))
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(connection.historical_import, params(), cancel)
                self.assertTrue(entered.wait(1))
                if disconnect:
                    connection.disconnect()
                else:
                    cancel.set()
                release.set()
                with self.assertRaises(q.QuestradeCancelled):
                    result.result(2)

    def test_only_one_download_runs_per_client(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(url):
            entered.set()
            self.assertTrue(release.wait(3))
            return [candle()]
        connection, _ = client(Transport(blocked))
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(connection.historical_import, params())
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(q.QuestradeError, "already running"):
                connection.historical_import(params())
            release.set()
            self.assertEqual(first.result(2)[0]["row_count"], 1)


if __name__ == "__main__":
    unittest.main()
