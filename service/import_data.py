"""Validate user-supplied OHLCV snapshots without fetching or trusting their source.

Timestamps remain naive exchange-local bar starts. Original UTF-8 bytes are
fingerprinted for provenance; canonical CSV is a separate immutable snapshot.
"""
from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import io
import re

MAX_CSV_BYTES = 8 * 1024 * 1024
MAX_ROWS = 100_000
MAX_PRICE = Decimal("10000000")
MIN_PRICE = Decimal("0.005")
MAX_VOLUME = Decimal("1000000000000000")
ADJUSTMENTS = frozenset({"unknown", "unadjusted", "split_adjusted", "split_and_dividend_adjusted"})
ALLOWED = frozenset({
    "name", "symbol", "source", "timezone", "currency", "price_adjustment",
    "bar_minutes", "session_open_minute", "session_close_minute", "csv",
})
REQUIRED = frozenset({"name", "symbol", "source", "timezone", "currency", "price_adjustment", "csv"})
DECIMAL_TOKEN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
DAILY_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
INTRADAY_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}(?::00)?\Z")
TIMEZONE = re.compile(r"(?:UTC|Etc/[A-Za-z0-9_+-]+|[A-Za-z_+-]+(?:/[A-Za-z0-9_+-]+)+)\Z")


def _text(payload, name, maximum):
    value = payload[name]
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    if not value or len(value) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must contain 1-{maximum} printable characters")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must be valid UTF-8 text") from error
    return value


def _integer(payload, name, default, low, high):
    value = payload.get(name, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def _number(value, column, row):
    value = value.strip()
    if not value or len(value) > 128 or not DECIMAL_TOKEN.fullmatch(value):
        raise ValueError(f"CSV row {row}: {column} must be a finite decimal number, not a formula")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"CSV row {row}: invalid {column}") from error
    if not number.is_finite():
        raise ValueError(f"CSV row {row}: {column} must be finite")
    if abs(number.as_tuple().exponent) > 100:
        raise ValueError(f"CSV row {row}: {column} exponent must be between -100 and 100")
    if column == "volume":
        if not 0 <= number <= MAX_VOLUME:
            raise ValueError(f"CSV row {row}: volume must be between 0 and {MAX_VOLUME}")
    elif not MIN_PRICE <= number <= MAX_PRICE:
        raise ValueError(f"CSV row {row}: {column} must be between {MIN_PRICE} and {MAX_PRICE} for cent-based prices")
    # Decimal.normalize() would round long input against the default context.
    canonical = format(number, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if number == 0:
        canonical = "0"
    if len(canonical) > 128:
        raise ValueError(f"CSV row {row}: canonical {column} exceeds 128 characters")
    return number, canonical


def _timestamp(value, intraday, row):
    value = value.strip()
    pattern = INTRADAY_TIMESTAMP if intraday else DAILY_TIMESTAMP
    if not pattern.fullmatch(value):
        kind = "naive exchange-local YYYY-MM-DDTHH:MM[:00]" if intraday else "YYYY-MM-DD"
        raise ValueError(f"CSV row {row}: expected {kind}; offsets, Z, nonzero seconds and mixed formats are unsupported")
    try:
        stamp = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError as error:
        raise ValueError(f"CSV row {row}: invalid Gregorian timestamp {value!r}") from error
    if not 1900 <= stamp.year <= 9999:
        raise ValueError(f"CSV row {row}: year must be from 1900 to 9999")
    return stamp, stamp.isoformat(timespec="seconds") if intraday else stamp.date().isoformat()


def validate_import(payload):
    """Return metadata and canonical UTF-8 CSV, or raise ValueError.

    Imported means supplied by the user. It does not establish that prices are
    genuine, licensed, exchange-complete, or suitable for trading.
    """
    if not isinstance(payload, dict):
        raise ValueError("import payload must be an object")
    unknown = set(payload) - ALLOWED
    missing = REQUIRED - set(payload)
    if unknown:
        raise ValueError("unsupported import field(s): " + ", ".join(sorted(map(str, unknown))))
    if missing:
        raise ValueError("missing import field(s): " + ", ".join(sorted(missing)))

    name = _text(payload, "name", 80)
    source = _text(payload, "source", 500)
    symbol = _text(payload, "symbol", 24)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,24}", symbol):
        raise ValueError("symbol must contain 1-24 ASCII letters, digits, dots, underscores or hyphens")
    symbol = symbol.upper()
    timezone = _text(payload, "timezone", 64)
    if not TIMEZONE.fullmatch(timezone):
        raise ValueError("timezone must be UTC or an IANA-style exchange-local timezone name")
    currency = _text(payload, "currency", 3).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must contain three ASCII letters")
    adjustment = _text(payload, "price_adjustment", 40)
    if adjustment not in ADJUSTMENTS:
        raise ValueError("price_adjustment must be unknown, unadjusted, split_adjusted or split_and_dividend_adjusted")
    interval = _integer(payload, "bar_minutes", 0, 0, 30)
    session_open = _integer(payload, "session_open_minute", 570, 0, 1438)
    session_close = _integer(payload, "session_close_minute", 960, 1, 1439)
    if session_open >= session_close:
        raise ValueError("session_open_minute must be earlier than session_close_minute")
    if interval and (session_close - session_open) % interval:
        raise ValueError("bar_minutes must divide the declared regular-session duration")

    contents = payload["csv"]
    if not isinstance(contents, str):
        raise ValueError("csv must be a UTF-8 string")
    try:
        original = contents.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError("csv must contain valid UTF-8 text") from error
    if len(original) > MAX_CSV_BYTES:
        raise ValueError("CSV exceeds the 8 MiB import limit")
    if not original:
        raise ValueError("CSV is empty")
    reader = csv.reader(io.StringIO(contents.removeprefix("\ufeff"), newline=""), strict=True)
    rows = ["timestamp,open,high,low,close,volume\n"]
    output_size = len(rows[0])
    previous = first = last = None
    gap_intervals = zero_volume = extended_rows = count = 0
    sessions = {}
    observed_months = set()
    try:
        header = next(reader, None)
        expected = ["open", "high", "low", "close", "volume"]
        if header is None or len(header) != 6:
            raise ValueError("CSV header must contain exactly timestamp,open,high,low,close,volume")
        header = [column.strip().lower() for column in header]
        if header[0] not in {"timestamp", "date", "datetime"} or header[1:] != expected:
            raise ValueError("CSV header must contain exactly timestamp/date/datetime,open,high,low,close,volume")
        for raw in reader:
            if not raw or (len(raw) == 1 and not raw[0].strip()):
                continue
            row = reader.line_num
            if len(raw) != 6:
                raise ValueError(f"CSV row {row}: expected exactly six columns")
            count += 1
            if count > MAX_ROWS:
                raise ValueError("CSV exceeds the 100000-row import limit")
            stamp, canonical_time = _timestamp(raw[0], bool(interval), row)
            if previous is not None and stamp <= previous:
                raise ValueError(f"CSV row {row}: timestamps must be unique and strictly increasing")
            values, encoded = [], []
            for column, value in zip(expected, raw[1:]):
                number, normalized = _number(value, column, row)
                values.append(number)
                encoded.append(normalized)
            open_, high, low, close, volume = values
            if high < max(open_, close) or low > min(open_, close) or low > high:
                raise ValueError(f"CSV row {row}: inconsistent OHLC high/low bounds")
            if not volume:
                zero_volume += 1
            if interval:
                minute = stamp.hour * 60 + stamp.minute
                if (minute - session_open) % interval:
                    raise ValueError(f"CSV row {row}: bar start is not aligned to session open and bar_minutes")
                if previous is not None and stamp.date() == previous.date():
                    step = int((stamp - previous).total_seconds()) // 60
                    if step % interval:
                        raise ValueError(f"CSV row {row}: same-day interval is not a multiple of bar_minutes")
                    gap_intervals += step // interval - 1
                regular = sessions.setdefault(stamp.date(), [])
                if session_open <= minute < session_close:
                    regular.append(minute)
                else:
                    extended_rows += 1
            observed_months.add((stamp.year, stamp.month))
            if first is None:
                first = canonical_time
            last, previous = canonical_time, stamp
            line = ",".join([canonical_time, *encoded]) + "\n"
            output_size += len(line)
            if output_size > MAX_CSV_BYTES:
                raise ValueError("Canonical CSV exceeds the 8 MiB import limit")
            rows.append(line)
    except csv.Error as error:
        raise ValueError(f"CSV row {reader.line_num}: malformed CSV: {error}") from error
    if not count:
        raise ValueError("CSV must contain at least one market-data row")

    warnings = [
        "Imported data is user-provided; its source, licensing and market accuracy are not independently verified.",
        "No exchange-calendar validation or timezone/DST conversion is performed. The timezone is a user-declared label; timestamps must already be exchange-local. Missing whole sessions and holidays cannot be distinguished.",
    ]
    if adjustment == "unknown":
        warnings.append("Price adjustment is unknown; splits and dividends can distort indicators and returns.")
    if zero_volume:
        warnings.append(f"{zero_volume} zero-volume row(s) retained. Completed zero-volume candles cannot generate a VWAP entry; simulated fills otherwise assume an executable observed open without a liquidity model.")
    if interval:
        if gap_intervals:
            warnings.append(f"Observed same-day gaps omit {gap_intervals} declared bar interval(s). Missing bars are not fabricated.")
        expected_count = (session_close - session_open) // interval
        incomplete = sum(
            len(minutes) != expected_count or not minutes or
            minutes[0] != session_open or minutes[-1] != session_close - interval
            for minutes in sessions.values()
        )
        if incomplete:
            warnings.append(f"{incomplete} observed session(s) have incomplete regular-session bars. VWAP and completed weekly/monthly trend warmup may be unavailable.")
        if extended_rows:
            warnings.append(f"{extended_rows} extended-hours row(s) retained; the opening VWAP strategy excludes bars outside the declared regular session.")
        if len(observed_months) < 5:
            warnings.append("Short monthly warmup: fewer than five observed calendar months. Default opening VWAP needs three completed monthly observations after discarding the initial partial month; supply more complete history.")
    else:
        warnings.append("Daily bars support SMA, EMA and RSI. Opening VWAP requires fixed-minute intraday bars.")
    kind = "intraday" if interval else "daily"
    metadata = {
        "name": name, "symbol": symbol, "source": source, "timezone": timezone,
        "currency": currency, "price_adjustment": adjustment, "bar_minutes": interval,
        "session_open_minute": session_open, "session_close_minute": session_close,
        "origin": "imported", "kind": kind, "interval_kind": kind,
        "row_count": count, "first_timestamp": first, "last_timestamp": last,
        "original_sha256": hashlib.sha256(original).hexdigest(), "warnings": warnings,
        "description": f"User-imported {symbol}: {count} {kind} bars. Source and market accuracy are not independently verified.",
    }
    return metadata, "".join(rows).encode("utf-8")

