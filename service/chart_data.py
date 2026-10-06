"""Read-only chart projections from saved inputs, configuration, and executions.

The overlays explain a completed simulation. They do not generate signals or
modify its persisted report. EMA history follows the active strategy's input
scope: regular-session bars for VWAP, all observed bars for other strategies.
The reference VWAP always uses regular-session observations only.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
import io
import math
import re

MAX_CHART_BARS = 1000


class EMA:
    def __init__(self, period):
        self.period = period
        self.count = 0
        self.seed_sum = 0.0
        self.value = None

    def push(self, close):
        self.count += 1
        if self.count <= self.period:
            self.seed_sum += close
            if self.count == self.period:
                self.value = self.seed_sum / self.period
        else:
            self.value += 2.0 / (self.period + 1.0) * (close - self.value)
        return self.value


def _integer(parameters, key, default, minimum, maximum):
    value = parameters.get(key, default)
    if type(value) not in (int, float) or not math.isfinite(value) or int(value) != value or not minimum <= value <= maximum:
        raise ValueError(f"invalid saved chart parameter: {key}")
    return int(value)


def _rows(content):
    rows = []
    previous = ""
    intraday = None
    for source in csv.DictReader(io.StringIO(content.decode("utf-8-sig"))):
        timestamp = source["timestamp"].strip().replace(" ", "T")
        is_intraday = len(timestamp) > 10
        if intraday is not None and is_intraday != intraday:
            raise ValueError("chart input mixes daily and intraday timestamps")
        intraday = is_intraday
        if is_intraday:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00", timestamp):
                raise ValueError("chart requires whole-minute exchange-local timestamps")
            parsed = datetime.fromisoformat(timestamp)
            minute = parsed.hour * 60 + parsed.minute
        else:
            date.fromisoformat(timestamp)
            minute = None
        if timestamp <= previous:
            raise ValueError("chart input timestamps must increase")
        previous = timestamp
        row = {key: float(source[key]) for key in ("open", "high", "low", "close", "volume")}
        if not all(math.isfinite(value) for value in row.values()):
            raise ValueError("chart input must contain finite values")
        if row["volume"] < 0 or min(row[key] for key in ("open", "high", "low", "close")) <= 0:
            raise ValueError("chart input has invalid price or volume")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
            raise ValueError("chart input has invalid OHLC bounds")
        row.update(timestamp=timestamp, _date=timestamp[:10], _minute=minute)
        rows.append(row)
    if not rows:
        raise ValueError("chart input is empty")
    return rows, intraday


def build_chart(run, report, content, session=None):
    """Build a bounded projection without reading mutable source CSV files."""
    if session is not None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", session):
            raise ValueError("session must use YYYY-MM-DD")
        date.fromisoformat(session)
    config = report.get("effective_config", {})
    backtest = config.get("backtesting", {})
    parameters = config.get("strategies", {}).get("VWAP_OPENING", {})
    strategy = report.get("strategy", run.get("strategy"))
    metadata = run.get("dataset_info") or report.get("provenance", {}).get("dataset_info") or {}
    if not isinstance(metadata, dict):
        raise ValueError("invalid saved dataset metadata")
    metadata = dict(metadata)
    origin = metadata.get("origin", "synthetic" if run["dataset"] in ("sample", "opening_demo") else "unknown")
    imported = origin == "imported"
    periods = {name: _integer(parameters, f"{name}_ema", default, 1, 100000)
               for name, default in (("fast", 20), ("medium", 50), ("slow", 200))}
    opening = _integer(parameters, "opening_window_minutes", 30, 1, 120)
    session_open = _integer(parameters, "session_open_minute", 570, 0, 1438)
    session_close = _integer(parameters, "session_close_minute", 960, session_open + 1, 1439)
    rows, intraday = _rows(content)
    declared_kind = metadata.get("interval_kind", metadata.get("kind"))
    if declared_kind is not None and declared_kind != ("intraday" if intraday else "daily"):
        raise ValueError("saved dataset interval kind does not match timestamps")
    if "kind" in metadata and "interval_kind" in metadata and metadata["kind"] != metadata["interval_kind"]:
        raise ValueError("saved dataset interval kinds disagree")
    if imported:
        required = {"bar_minutes", "session_open_minute", "session_close_minute"}
        if not required <= metadata.keys() or declared_kind is None:
            raise ValueError("imported chart requires saved interval and session metadata")
        if strategy == "VWAP_OPENING":
            if not intraday:
                raise ValueError("saved VWAP strategy requires an intraday dataset")
            for key, default, minimum, maximum in (("bar_minutes", 2, 1, 30),
                                                 ("session_open_minute", 570, 0, 1438),
                                                 ("session_close_minute", 960, 1, 1439)):
                if _integer(parameters, key, default, minimum, maximum) != metadata[key]:
                    raise ValueError(f"saved VWAP {key} does not match imported dataset metadata")
    # A crossover/RSI run still contains default VWAP settings in effective_config.
    # Dataset cadence and session boundaries must not inherit those unused settings.
    use_metadata_session = imported or bool(metadata) and strategy != "VWAP_OPENING"
    if use_metadata_session:
        session_open = _integer(metadata, "session_open_minute", session_open, 0, 1438)
        session_close = _integer(metadata, "session_close_minute", session_close, session_open + 1, 1439)
    interval = None
    regular_session_only = bool(intraday and strategy == "VWAP_OPENING")
    if intraday:
        differences = [later["_minute"] - earlier["_minute"] for earlier, later in zip(rows, rows[1:])
                       if earlier["_date"] == later["_date"]]
        inferred = math.gcd(*differences) if differences else 2
        interval = _integer(metadata if use_metadata_session else parameters, "bar_minutes", inferred, 1, 30)
        for row in rows:
            row["_regular"] = session_open <= row["_minute"] and row["_minute"] + interval <= session_close
        regular = [row for row in rows if row["_regular"]]
        if any((row["_minute"] - session_open) % interval for row in regular):
            raise ValueError("saved chart interval does not match dataset timestamps")
        if regular_session_only:
            rows = regular
    else:
        if metadata.get("bar_minutes", 0) != 0:
            raise ValueError("saved daily dataset interval must be zero")
        if session is not None:
            raise ValueError("daily charts do not accept a session")

    start = backtest.get("start_date", "")
    end = backtest.get("end_date", "")
    eligible = [row for row in rows if (not start or row["_date"] >= start) and (not end or row["_date"] <= end)]
    sessions = sorted({row["_date"] for row in eligible}) if intraday else []
    if not eligible:
        raise ValueError("selected run has no chart bars in its date range")
    results = report["results"]
    trades = results.get("trades", [])
    if intraday:
        if session is None:
            session = next((trade["timestamp"][:10] for trade in trades
                            if trade.get("action") == "BUY" and trade["timestamp"][:10] in sessions), sessions[-1])
        if session not in sessions:
            raise ValueError("session is not available in this run")

    indicators = {name: EMA(period) for name, period in periods.items()}
    selected = []
    current_date = None
    volume = weighted = 0.0
    for row in rows:
        if end and row["_date"] > end or intraday and row["_date"] > session:
            break
        if current_date != row["_date"]:
            current_date = row["_date"]
            volume = weighted = 0.0
        if intraday and row["_regular"] and row["volume"] > 0:
            volume += row["volume"]
            weighted += (row["high"] + row["low"] + row["close"]) / 3.0 * row["volume"]
        overlay = {f"ema_{name}": indicator.push(row["close"]) for name, indicator in indicators.items()}
        visible = row["_date"] == session if intraday else (not start or row["_date"] >= start)
        if visible:
            selected.append({key: value for key, value in row.items() if not key.startswith("_")}
                            | overlay | {"vwap": weighted / volume if intraday and row["_regular"] and volume > 0 else None,
                                         "regular_session": row["_regular"] if intraday else None})
    total_bars = len(selected)
    selected = selected[-MAX_CHART_BARS:]
    first, last = selected[0]["timestamp"], selected[-1]["timestamp"]

    def visible_events(events):
        return [event for event in events if first <= event["timestamp"].replace(" ", "T") <= last]

    symbol = metadata.get("symbol", backtest.get("symbol", "SIM"))
    currency = metadata.get("currency", "USD" if origin == "synthetic" else None)
    timezone = metadata.get("timezone", "exchange-local demo" if origin == "synthetic" else None)
    if not metadata:
        metadata = {"origin": origin, "symbol": symbol, "currency": currency, "timezone": timezone,
                    "interval_kind": "intraday" if intraday else "daily", "bar_minutes": interval or 0,
                    "session_open_minute": session_open, "session_close_minute": session_close}
    return {"schema_version": 1, "run_id": run["id"], "dataset": run["dataset"],
            "dataset_sha256": run["dataset_sha256"], "dataset_info": metadata,
            "origin": origin, "synthetic": origin == "synthetic",
            "currency": currency, "timezone": timezone,
            "symbol": symbol, "interval_minutes": interval,
            "session": session, "sessions": sessions,
            "regular_session_only": regular_session_only,
            "outside_session_bar_count": sum(bar["regular_session"] is False for bar in selected),
            "indicator_scope": {"ema": "regular_session_bars" if regular_session_only else "all_observed_bars",
                                "vwap": "regular_session_only" if intraday else "not_applicable"},
            "opening_window_minutes": opening, "session_open_minute": session_open,
            "session_close_minute": session_close, "periods": periods, "bars": selected,
            "trades": visible_events(trades), "rejections": visible_events(results.get("rejections", [])),
            "total_bars": total_bars, "truncated": total_bars > MAX_CHART_BARS}


def chart_for_run(store, run_id, session=None):
    report = store.result(run_id)  # Requires completed, atomically published output.
    return build_chart(store.get(run_id), report, store.dataset_snapshot(run_id), session)
