"""Read-only Questrade historical candles; credentials live in this process only.

Official protocol: https://www.questrade.com/api/documentation/getting-started
and /rest-operations/market-calls/{symbols-search,symbols-id,markets-candles-id}.
No account, order, quote, or credential persistence operations exist here.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import re
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .import_data import MAX_CSV_BYTES, MAX_ROWS, validate_import

UTC = timezone.utc
INTERVALS = {0: "OneDay", 1: "OneMinute", 2: "TwoMinutes", 5: "FiveMinutes",
             10: "TenMinutes", 15: "FifteenMinutes", 30: "HalfHour"}
EXCHANGES = frozenset({"TSX", "TSXV", "CNSX", "NASDAQ", "NYSE", "NYSEAM", "ARCA"})
TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{1,4096}\Z")
API_SERVER = re.compile(r"https://api[0-9]+\.iq\.questrade\.com/?\Z")
SYMBOL = re.compile(r"[A-Za-z0-9._-]{1,24}\Z")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CHUNK_BARS = 1900


class QuestradeError(ValueError):
    """A deliberately credential-free message suitable for the local UI."""


class QuestradeCancelled(QuestradeError):
    """The operation was cancelled, disconnected, or its session was replaced."""


class _HTTPFailure(QuestradeError):
    def __init__(self, status):
        self.status = status
        messages = {401: "Questrade authorization expired. Reconnect with a fresh refresh token.",
                    403: "Questrade denied market-data access. Check your API permissions and data entitlement.",
                    429: "Questrade's request limit was reached. Wait briefly, then try again."}
        super().__init__(messages.get(status, "Questrade could not complete the market-data request. Try again later."))


def _ny_zone():
    try:
        return ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        raise QuestradeError("New York timezone data is unavailable. Install the project's Python requirements (tzdata), then restart the service.") from None


def _utc_now(value=None):
    value = value() if callable(value) else value
    value = value if value is not None else datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise QuestradeError("The local clock must provide an offset-aware time.")
    return value.astimezone(UTC)


def validate_history_params(params, now=None):
    """Pure preflight; no credentials or requests. End date is inclusive."""
    keys = {"symbol_id", "start_date", "end_date", "bar_minutes"}
    if not isinstance(params, dict) or set(params) != keys:
        raise QuestradeError("Historical download needs only symbol_id, start_date, end_date and bar_minutes.")
    if type(params["symbol_id"]) is not int or not 1 <= params["symbol_id"] <= 2_147_483_647:
        raise QuestradeError("Select a valid Questrade symbol before downloading.")
    if type(params["bar_minutes"]) is not int or params["bar_minutes"] not in INTERVALS:
        raise QuestradeError("Use daily bars or a supported interval: 1, 2, 5, 10, 15 or 30 minutes.")
    dates = []
    for key in ("start_date", "end_date"):
        value = params[key]
        if not isinstance(value, str) or not DATE.fullmatch(value):
            raise QuestradeError("Download dates must use YYYY-MM-DD.")
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise QuestradeError("Download dates must be valid calendar dates.") from None
        if parsed.year < 1900:
            raise QuestradeError("Download dates must be from 1900 onwards.")
        dates.append(parsed)
    if dates[0] > dates[1] or (dates[1] - dates[0]).days >= 366:
        raise QuestradeError("Choose an ordered date range of at most 366 calendar days.")
    if dates[1] > _utc_now(now).astimezone(_ny_zone()).date():
        raise QuestradeError("The end date cannot be in the future in New York time.")
    return dict(params)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _http_json(url, headers, timeout, max_bytes):
    """HTTPS verification stays enabled; errors never include URLs or bodies."""
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(Request(url, headers=headers, method="GET"), timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > max_bytes):
                raise QuestradeError("Questrade returned an oversized or invalid response.")
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise QuestradeError("Questrade returned an oversized response.")
    except HTTPError as error:
        # Never read the response body: OAuth errors may contain credentials.
        status = error.code
        error.close()
        raise _HTTPFailure(status) from None
    except (URLError, TimeoutError, OSError):
        raise QuestradeError("Could not reach Questrade securely. Check your connection and try again.") from None
    try:
        result = json.loads(raw.decode("utf-8"), parse_float=Decimal,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                            object_pairs_hook=_json_pairs)
    except (UnicodeError, ValueError, RecursionError):
        raise QuestradeError("Questrade returned an invalid JSON response.") from None
    if not isinstance(result, dict):
        raise QuestradeError("Questrade returned an invalid response object.")
    return result


def _credential(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise QuestradeError("The Questrade token must be a nonempty ASCII token without spaces or line breaks (maximum 4096 characters).")
    return value


class QuestradeClient:
    """A reconnect/disconnect generation fences all in-flight network responses.

    State locks are short; status/disconnect never wait for a socket or a download.
    The injected transport is a test seam, not a configurable HTTP endpoint.
    """
    def __init__(self, http_transport=None, now=None, request_interval=.25):
        self._transport = http_transport or _http_json
        self._now = now or (lambda: datetime.now(UTC))
        if not isinstance(request_interval, (float, int)) or not math.isfinite(request_interval) or request_interval < 0:
            raise ValueError("request_interval must be finite and nonnegative")
        self._request_interval = request_interval
        self._state_lock = threading.RLock()
        self._auth_lock = threading.Lock()
        self._network_lock = threading.Lock()
        self._history_lock = threading.Lock()
        self._generation = 0
        self._session = None
        self._connecting = False
        self._history_progress = None
        self._last_request = 0.0

    def status(self):
        now = _utc_now(self._now)
        with self._state_lock:
            session = self._session
            connected = session is not None and session["expires_at"] > now
            return {"provider": "Questrade", "connected": connected,
                    "state": "connecting" if self._connecting else ("connected" if connected else ("expired" if session else "disconnected")),
                    "expires_at": session["expires_at"].isoformat() if session else None,
                    "can_refresh": session is not None,
                    "history_progress": dict(self._history_progress) if self._history_progress else None}

    def disconnect(self):
        with self._state_lock:
            self._generation += 1
            self._session = None
            self._connecting = False
            self._history_progress = None
        return self.status()

    def _check(self, generation, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            raise QuestradeCancelled("Historical download was cancelled. No dataset was imported.")
        with self._state_lock:
            if generation != self._generation:
                raise QuestradeCancelled("The Questrade session changed. No dataset was imported.")

    @contextmanager
    def _locked(self, lock, generation, cancel_event=None):
        while not lock.acquire(timeout=.05):
            self._check(generation, cancel_event)
        try:
            self._check(generation, cancel_event)
            yield
        finally:
            lock.release()

    def _perform(self, url, headers, generation, cancel_event=None, maximum=MAX_RESPONSE_BYTES):
        with self._locked(self._network_lock, generation, cancel_event):
            while True:
                delay = self._last_request + self._request_interval - time.monotonic()
                if delay <= 0:
                    break
                self._check(generation, cancel_event)
                time.sleep(min(delay, .05))
            self._check(generation, cancel_event)
            self._last_request = time.monotonic()
            try:
                result = self._transport(url, headers, 15, maximum)
            except QuestradeError:
                self._check(generation, cancel_event)
                raise
            except Exception:
                # Unknown transports/libraries can put secrets in their errors.
                self._check(generation, cancel_event)
                raise QuestradeError("The Questrade request failed. Check your connection and try again.") from None
            self._check(generation, cancel_event)
            if not isinstance(result, dict):
                raise QuestradeError("Questrade returned an invalid response object.")
            return result

    def _exchange(self, refresh_token, generation, cancel_event=None):
        url = "https://login.questrade.com/oauth2/token?" + urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token})
        data = self._perform(url, {"Accept": "application/json"}, generation, cancel_event, 64 * 1024)
        try:
            access = _credential(data.get("access_token"))
            refresh = _credential(data.get("refresh_token"))
            expires = data.get("expires_in")
            server = data.get("api_server")
            if (data.get("token_type", "Bearer") != "Bearer" or type(expires) is not int or
                    not 1 <= expires <= 86400 or not isinstance(server, str) or not API_SERVER.fullmatch(server)):
                raise ValueError()
        except (ValueError, TypeError):
            raise QuestradeError("Questrade returned invalid authorization details. Reconnect with a fresh refresh token.") from None
        return {"access": access, "refresh": refresh, "server": server.rstrip("/") + "/",
                "expires_at": _utc_now(self._now) + timedelta(seconds=expires)}

    def connect(self, refresh_token):
        refresh_token = _credential(refresh_token)
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            self._session = None
            self._connecting = True
            self._history_progress = None
        try:
            with self._locked(self._auth_lock, generation):
                session = self._exchange(refresh_token, generation)
                with self._state_lock:
                    self._check(generation)
                    self._session = session
                    self._connecting = False
        except Exception:
            with self._state_lock:
                if generation == self._generation:
                    self._connecting = False
                    self._session = None
            raise
        return self.status()

    def _session_for(self, generation, cancel_event=None, rejected_access=None):
        def reusable(session):
            if session is None:
                raise QuestradeError("Connect Questrade with a fresh refresh token first.")
            return (session["expires_at"] > _utc_now(self._now) + timedelta(seconds=30)
                    and session["access"] != rejected_access)
        self._check(generation, cancel_event)
        with self._state_lock:
            if reusable(self._session):
                return dict(self._session)
        with self._locked(self._auth_lock, generation, cancel_event):
            with self._state_lock:
                if reusable(self._session):
                    return dict(self._session)
                refresh = self._session["refresh"]
            try:
                session = self._exchange(refresh, generation, cancel_event)
                with self._state_lock:
                    self._check(generation, cancel_event)
                    self._session = session
                    return dict(session)
            except Exception:
                # A rotated refresh token may have been consumed even when its
                # response was lost. Reconnection is safer than guessing/retry.
                with self._state_lock:
                    if generation == self._generation:
                        self._session = None
                raise

    def _api(self, path, params, generation, cancel_event=None):
        if not re.fullmatch(r"(?:symbols/search|symbols/[1-9][0-9]*|markets/candles/[1-9][0-9]*)", path):
            raise QuestradeError("Unsupported Questrade market-data operation.")
        session = self._session_for(generation, cancel_event)
        for attempt in range(2):
            url = session["server"] + "v1/" + path
            if params:
                url += "?" + urlencode(params)
            try:
                return self._perform(url, {"Accept": "application/json", "Authorization": "Bearer " + session["access"]}, generation, cancel_event)
            except _HTTPFailure as error:
                if error.status != 401:
                    raise
                if attempt:
                    with self._state_lock:
                        if generation == self._generation and self._session and self._session["access"] == session["access"]:
                            self._session = None
                    raise
                session = self._session_for(generation, cancel_event, session["access"])

    @staticmethod
    def _symbol_rows(data):
        # The official search example uses singular "symbol"; its field table
        # and the detail endpoint use "symbols". Accept either documented form.
        rows = data.get("symbols", data.get("symbol"))
        if not isinstance(rows, list) or len(rows) > 500:
            raise QuestradeError("Questrade returned an invalid symbol list.")
        return rows

    @staticmethod
    def _symbol(row, require_currency=False):
        if not isinstance(row, dict):
            raise QuestradeError("Questrade returned invalid symbol information.")
        symbol, ident = row.get("symbol"), row.get("symbolId")
        description = row.get("description", "")
        currency = row.get("currency")
        if (type(ident) is not int or not 1 <= ident <= 2_147_483_647 or
                not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or
                not isinstance(description, str) or len(description) > 500 or
                any(ord(char) < 32 or ord(char) == 127 for char in description)):
            raise QuestradeError("Questrade returned invalid symbol information.")
        if row.get("securityType") != "Stock" or row.get("listingExchange") not in EXCHANGES:
            raise QuestradeError("This download supports listed US and Canadian stocks and ETFs only.")
        if (currency is not None and currency not in {"CAD", "USD"}) or (require_currency and currency is None):
            raise QuestradeError("Questrade must identify the symbol's currency as USD or CAD before import.")
        result = {"id": ident, "symbol": symbol.upper(), "description": description}
        if currency is not None:
            result["currency"] = currency
        return result

    def search_symbols(self, prefix):
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9._ -]{1,40}", prefix) or not prefix.strip():
            raise QuestradeError("Enter 1-40 letters, digits, spaces, dots, underscores or hyphens to search symbols.")
        with self._state_lock:
            generation = self._generation
        rows = self._symbol_rows(self._api("symbols/search", {"prefix": prefix.strip()}, generation))
        result, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise QuestradeError("Questrade returned invalid symbol information.")
            if row.get("securityType") != "Stock" or row.get("listingExchange") not in EXCHANGES:
                continue
            item = self._symbol(row)
            if item["id"] not in seen:
                result.append(item)
                seen.add(item["id"])
            if len(result) == 30:
                break
        self._check(generation)
        return result

    @staticmethod
    def _stamp(value):
        if not isinstance(value, str) or len(value) > 64:
            raise QuestradeError("Questrade returned an invalid candle timestamp.")
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() not in {timedelta(0), timedelta(hours=-4), timedelta(hours=-5)}:
                raise ValueError()
            if stamp.second or stamp.microsecond:
                raise ValueError()
            return stamp.astimezone(UTC)
        except (ValueError, OverflowError):
            raise QuestradeError("Questrade candle timestamps must be whole-minute times with UTC or Eastern offsets.") from None

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise QuestradeError("Questrade returned an invalid candle number.")
        try:
            number = Decimal(str(value))
            if not number.is_finite() or abs(number.as_tuple().exponent) > 100 or len(str(value)) > 128:
                raise ValueError()
            result = format(number, "f")
            if "." in result:
                result = result.rstrip("0").rstrip(".")
            if len(result) > 128:
                raise ValueError()
            return "0" if number == 0 else result
        except (ValueError, InvalidOperation, OverflowError):
            raise QuestradeError("Questrade returned an invalid candle number.") from None

    def historical_import(self, params, cancel_event=None):
        as_of = _utc_now(self._now)
        params = validate_history_params(params, as_of)
        zone = _ny_zone()
        with self._state_lock:
            generation = self._generation
        if not self._history_lock.acquire(blocking=False):
            raise QuestradeError("A Questrade historical download is already running.")
        try:
            with self._state_lock:
                self._check(generation, cancel_event)
                self._history_progress = None
            rows = self._symbol_rows(self._api("symbols/" + str(params["symbol_id"]), {}, generation, cancel_event))
            selected = [row for row in rows if isinstance(row, dict) and row.get("symbolId") == params["symbol_id"]]
            if len(selected) != 1:
                raise QuestradeError("Questrade did not identify the selected symbol unambiguously.")
            symbol = self._symbol(selected[0], require_currency=True)
            start = datetime.combine(date.fromisoformat(params["start_date"]), datetime.min.time(), zone).astimezone(UTC)
            end = min(datetime.combine(date.fromisoformat(params["end_date"]) + timedelta(days=1), datetime.min.time(), zone).astimezone(UTC), as_of)
            interval = params["bar_minutes"]
            step = timedelta(minutes=interval * MAX_CHUNK_BARS) if interval else timedelta(days=MAX_CHUNK_BARS)
            cursor, candles, skipped_incomplete, empty_chunks = start, {}, 0, 0
            completed_chunks = 0
            total_chunks = math.ceil((end - start) / step)
            with self._state_lock:
                self._check(generation, cancel_event)
                self._history_progress = {"completed_chunks": 0, "total_chunks": total_chunks, "rows": 0}
            csv_size = len("timestamp,open,high,low,close,volume\n")
            while cursor < end:
                self._check(generation, cancel_event)
                chunk_end = min(end, cursor + step)
                data = self._api("markets/candles/" + str(symbol["id"]),
                                 {"startTime": cursor.astimezone(zone).isoformat(),
                                  "endTime": chunk_end.astimezone(zone).isoformat(),
                                  "interval": INTERVALS[interval]}, generation, cancel_event)
                batch = data.get("candles")
                if not isinstance(batch, list):
                    raise QuestradeError("Questrade returned an invalid candles response.")
                if len(batch) >= 2000:
                    raise QuestradeError("Questrade returned its 2000-candle limit. Download a smaller date range; incomplete data was not imported.")
                empty_chunks += not batch
                for candle in batch:
                    self._check(generation, cancel_event)
                    if not isinstance(candle, dict):
                        raise QuestradeError("Questrade returned an invalid candle.")
                    begin, finish = self._stamp(candle.get("start")), self._stamp(candle.get("end"))
                    local = begin.astimezone(zone)
                    if interval:
                        valid_span = finish - begin == timedelta(minutes=interval)
                    else:
                        valid_span = (local.time().replace(tzinfo=None) == datetime.min.time() and
                                      finish.astimezone(zone).date() == local.date() + timedelta(days=1) and
                                      finish.astimezone(zone).time().replace(tzinfo=None) == datetime.min.time())
                    if not valid_span:
                        raise QuestradeError("Questrade returned a candle with an unexpected interval.")
                    # Providers may include an aligned boundary candle on both
                    # adjacent requests. Anything farther away is a bad response.
                    tolerance = timedelta(minutes=interval) if interval else timedelta(hours=25)
                    if begin < cursor - tolerance or begin > chunk_end:
                        raise QuestradeError("Questrade returned a candle outside the requested window.")
                    if begin < start or begin >= end:
                        continue
                    if finish > end or finish > as_of:
                        skipped_incomplete += 1
                        continue
                    encoded = tuple(self._number(candle.get(key)) for key in ("open", "high", "low", "close", "volume"))
                    stamp = local.strftime("%Y-%m-%dT%H:%M:%S") if interval else local.date().isoformat()
                    signature = (begin, finish, encoded)
                    if stamp in candles:
                        if candles[stamp] != signature:
                            raise QuestradeError("Questrade returned conflicting duplicate candles. No dataset was imported.")
                    else:
                        if len(candles) >= MAX_ROWS:
                            raise QuestradeError("Historical data exceeds the 100000-row limit. Choose fewer days or a larger interval.")
                        csv_size += len(stamp) + sum(map(len, encoded)) + 6
                        if csv_size > MAX_CSV_BYTES:
                            raise QuestradeError("Historical CSV exceeds the 8 MiB limit. Choose fewer days or a larger interval.")
                        candles[stamp] = signature
                cursor = chunk_end
                completed_chunks += 1
                with self._state_lock:
                    self._check(generation, cancel_event)
                    self._history_progress = {"completed_chunks": completed_chunks,
                                              "total_chunks": total_chunks, "rows": len(candles)}
            if not candles:
                raise QuestradeError("Questrade returned no completed candles for this symbol and date range.")
            csv = "timestamp,open,high,low,close,volume\n" + "".join(
                ",".join((stamp, *candles[stamp][2])) + "\n" for stamp in sorted(candles))
            payload = {"name": f"{symbol['symbol']} · Questrade · {params['start_date']}–{params['end_date']}",
                       "symbol": symbol["symbol"], "source": f"Questrade historical candles API; symbol ID {symbol['id']}",
                       "timezone": "America/New_York", "currency": symbol["currency"], "price_adjustment": "unknown",
                       "bar_minutes": interval, "session_open_minute": 570, "session_close_minute": 960, "csv": csv}
            self._check(generation, cancel_event)
            try:
                metadata, content = validate_import(payload)
            except ValueError:
                raise QuestradeError("Questrade candles failed OHLCV or session-alignment validation. No dataset was imported.") from None
            metadata["provider"] = "Questrade"
            metadata["provider_symbol_id"] = symbol["id"]
            metadata["listing_exchange"] = selected[0]["listingExchange"]
            metadata["requested_start_date"] = params["start_date"]
            metadata["requested_end_date"] = params["end_date"]
            metadata["description"] = f"Questrade historical {symbol['symbol']}: {metadata['row_count']} {metadata['kind']} bars. No independent accuracy or exchange-calendar verification."
            metadata["warnings"][:2] = [
                "Fetched from Questrade's historical candles API. Data entitlement, source accuracy and price adjustments are not independently verified. The original fingerprint covers the generated provider CSV, not raw HTTP responses.",
                "Offset-aware provider timestamps were converted to America/New_York. No exchange-calendar completeness check is performed; missing whole sessions or months may be unavailable data, holidays or omitted history. No bars are invented.",
            ]
            if skipped_incomplete:
                metadata["warnings"].append(f"Excluded {skipped_incomplete} unfinished or request-end-crossing candle(s).")
            if empty_chunks:
                metadata["warnings"].append(f"Questrade returned no candles for {empty_chunks} requested window(s); this download does not establish complete coverage.")
            self._check(generation, cancel_event)
            return metadata, content
        finally:
            self._history_lock.release()
