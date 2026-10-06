"""Adapt a user-exported TradingView CSV into the existing strict import contract.

No provider API or network access is involved. Indicator columns are discarded
as literal text; the standard importer validates the selected OHLCV values.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .import_data import ALLOWED, REQUIRED, MAX_CSV_BYTES, MAX_ROWS, validate_import

DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
EPOCH = re.compile(r"[+-]?[0-9]+\Z")
ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}"
                 r"(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?P<offset>Z|[+-][0-9]{2}:?[0-9]{2})?\Z")
TIME_HEADERS = frozenset({"time", "timestamp", "date", "datetime"})
OHLCV = ("open", "high", "low", "close", "volume")
MAX_COLUMNS = 256
MAX_HEADER_LENGTH = 128


def _zone(name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("timezone must name the intended exchange-local IANA timezone")
    name = name.strip()
    if name == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(
            f"Cannot load timezone {name!r}. Check its IANA name. If timezone data is missing "
            "(common on Windows), install it with: python -m pip install tzdata. "
            "Do not substitute a different timezone for the intended exchange clock."
        ) from error


def _check_naive_local(stamp, zone, row):
    """A naive input must map to exactly one real instant in its declared zone."""
    offsets = set()
    for fold in (0, 1):
        candidate = stamp.replace(tzinfo=zone, fold=fold)
        restored = candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if restored == stamp:
            offsets.add(candidate.utcoffset())
    if not offsets:
        raise ValueError(f"TradingView CSV row {row}: nonexistent local time during a DST transition; use an explicit UTC offset or Unix seconds")
    if len(offsets) > 1:
        raise ValueError(f"TradingView CSV row {row}: ambiguous local time during a DST transition; use an explicit UTC offset or Unix seconds")


def _time(value, interval, zone_name, row):
    value = value.strip()
    if len(value) > 64:
        raise ValueError(f"TradingView CSV row {row}: timestamp exceeds 64 characters")
    try:
        if DATE.fullmatch(value):
            if interval:
                raise ValueError(f"TradingView CSV row {row}: date-only timestamps require a daily interval")
            # A daily calendar date is not a UTC midnight instant.
            return datetime.fromisoformat(value).date().isoformat(), "date"
        if EPOCH.fullmatch(value):
            seconds = int(value)
            if abs(seconds) >= 1_000_000_000_000:
                raise ValueError(f"TradingView CSV row {row}: epoch milliseconds are unsupported; export Unix seconds")
            stamp = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).astimezone(_zone(zone_name))
            mode = "unix_seconds"
        else:
            match = ISO.fullmatch(value)
            if not match:
                raise ValueError(f"TradingView CSV row {row}: unsupported time format; use Unix seconds, ISO timestamps or daily YYYY-MM-DD dates")
            encoded = value.replace(" ", "T")
            offset = match.group("offset")
            if offset == "Z":
                encoded = encoded[:-1] + "+00:00"
            elif offset and ":" not in offset:
                encoded = encoded[:-5] + offset[:3] + ":" + offset[3:]
            stamp = datetime.fromisoformat(encoded)
            zone = _zone(zone_name)
            if offset:
                stamp = stamp.astimezone(zone)
                mode = "offset_iso"
            else:
                _check_naive_local(stamp, zone, row)
                mode = "naive_iso"
        if stamp.second or stamp.microsecond:
            raise ValueError(f"TradingView CSV row {row}: timestamps must identify whole-minute bar starts")
        return (stamp.replace(tzinfo=None).isoformat(timespec="seconds") if interval else stamp.date().isoformat()), mode
    except (OverflowError, OSError) as error:
        raise ValueError(f"TradingView CSV row {row}: timestamp is outside the supported calendar range") from error


def validate_tradingview_import(payload):
    """Return ordinary import metadata and canonical bytes from a native export.

    The caller handles its format selector before invoking this function. Source
    declarations remain user-provided and are not verified by this adapter.
    """
    if not isinstance(payload, dict):
        raise ValueError("import payload must be an object")
    unknown, missing = set(payload) - ALLOWED, REQUIRED - set(payload)
    if unknown:
        raise ValueError("unsupported import field(s): " + ", ".join(sorted(map(str, unknown))))
    if missing:
        raise ValueError("missing import field(s): " + ", ".join(sorted(missing)))
    contents = payload["csv"]
    if not isinstance(contents, str):
        raise ValueError("csv must be a UTF-8 string")
    try:
        original = contents.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError("csv must contain valid UTF-8 text") from error
    if not original or len(original) > MAX_CSV_BYTES:
        raise ValueError("TradingView CSV must be nonempty and at most 8 MiB")
    interval = payload.get("bar_minutes", 0)
    if type(interval) is not int or not 0 <= interval <= 30:
        raise ValueError("bar_minutes must be an integer from 0 to 30")
    reader = csv.reader(io.StringIO(contents.removeprefix("\ufeff"), newline=""), strict=True)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("timestamp", *OHLCV))
    mode = None
    count = 0
    try:
        header = next(reader, None)
        if header is None or not 6 <= len(header) <= MAX_COLUMNS:
            raise ValueError(f"TradingView CSV requires 6–{MAX_COLUMNS} columns including time, open, high, low, close and Volume")
        labels = [name.strip() for name in header]
        if any(not name or len(name) > MAX_HEADER_LENGTH or any(ord(char) < 32 or ord(char) == 127 for char in name) for name in labels):
            raise ValueError(f"TradingView CSV headers must contain 1–{MAX_HEADER_LENGTH} printable characters")
        normalized = [name.lower() for name in labels]
        if len(set(normalized)) != len(normalized):
            raise ValueError("TradingView CSV has duplicate headers; each OHLCV and indicator column must have a distinct name")
        time_columns = [index for index, name in enumerate(normalized) if name in TIME_HEADERS]
        if len(time_columns) != 1:
            raise ValueError("TradingView CSV must have exactly one time/timestamp/date/datetime column")
        if any(column not in normalized for column in OHLCV):
            raise ValueError("TradingView CSV requires exact open, high, low, close and one Volume header (case insensitive)")
        indices = [time_columns[0], *(normalized.index(column) for column in OHLCV)]
        ignored = [name for index, name in enumerate(labels) if index not in indices]
        # Bound the added provenance while still naming every ignored column.
        if sum(len(name.encode("utf-8")) for name in ignored) > 8000:
            raise ValueError("TradingView ignored column names exceed the 8,000-byte provenance limit")
        for row in reader:
            if not row or len(row) == 1 and not row[0].strip():
                continue
            if len(row) != len(header):
                raise ValueError(f"TradingView CSV row {reader.line_num}: column count does not match the header")
            count += 1
            if count > MAX_ROWS:
                raise ValueError("TradingView CSV exceeds the 100000-row import limit")
            timestamp, current_mode = _time(row[indices[0]], interval, payload["timezone"], reader.line_num)
            if mode is not None and mode != current_mode:
                raise ValueError(f"TradingView CSV row {reader.line_num}: mixed timestamp types are unsupported")
            mode = current_mode
            writer.writerow((timestamp, *(row[index] for index in indices[1:])))
    except csv.Error as error:
        raise ValueError(f"TradingView CSV row {reader.line_num}: malformed CSV: {error}") from error
    if not count:
        raise ValueError("TradingView CSV must contain at least one market-data row")

    transformed = {**payload, "csv": output.getvalue()}
    metadata, canonical = validate_import(transformed)
    metadata["original_sha256"] = hashlib.sha256(original).hexdigest()
    metadata["import_format"] = "tradingview"
    metadata["timestamp_format"] = mode
    metadata["ignored_columns"] = ignored
    # The standard parser receives already-local timestamps. Replace its generic
    # no-conversion note with the specific transformation actually performed.
    metadata["warnings"] = [warning for warning in metadata["warnings"]
                            if not warning.startswith("No exchange-calendar validation or timezone/DST conversion")]
    metadata["warnings"].append("No exchange-calendar validation is performed. Missing whole sessions and holidays cannot be distinguished.")
    if mode in ("unix_seconds", "offset_iso"):
        metadata["warnings"].append(f"TradingView {mode.replace('_', ' ')} timestamps were converted to {metadata['timezone']}; canonical timestamps are exchange-local bar starts.")
    elif mode == "naive_iso":
        metadata["warnings"].append(f"TradingView timestamps had no UTC offset and were interpreted as already local to {metadata['timezone']}. No clock conversion was applied; ambiguous or nonexistent DST times are rejected.")
    else:
        metadata["warnings"].append("TradingView date-only daily timestamps were preserved as calendar dates without timezone conversion.")
    if ignored:
        metadata["warnings"].append("Ignored TradingView columns (not evaluated): " + ", ".join(repr(name) for name in ignored) + ".")
    return metadata, canonical
