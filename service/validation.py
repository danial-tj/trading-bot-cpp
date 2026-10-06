"""Public API accepts bounded configuration and registered dataset IDs only."""
import copy
from datetime import date
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
DATASETS = {
    "sample": {"name": "Synthetic daily cycle", "file": "daily_demo.csv", "description": "Synthetic weekday daily OHLCV cycle. Not market performance evidence.",
               "origin": "synthetic", "symbol": "SIM", "kind": "daily", "interval_kind": "daily", "bar_minutes": 0,
               "timezone": "exchange-local demo", "currency": "USD", "price_adjustment": "unadjusted",
               "source": "Bundled synthetic fixture", "session_open_minute": 570, "session_close_minute": 960},
    "opening_demo": {"name": "Opening VWAP demo", "file": "opening_demo.csv", "description": "Synthetic two-minute bars with weekly/monthly warm-up. Not market performance evidence.",
                     "origin": "synthetic", "symbol": "SIM", "kind": "intraday", "interval_kind": "intraday", "bar_minutes": 2,
                     "timezone": "exchange-local demo", "currency": "USD", "price_adjustment": "unadjusted",
                     "source": "Bundled synthetic fixture", "session_open_minute": 570, "session_close_minute": 960},
}
STRATEGIES = ("SMA_CROSSOVER", "EMA_CROSSOVER", "RSI", "VWAP_OPENING")
MAX_DATASET_BYTES = 8 * 1024 * 1024
MAX_BODY_BYTES = 32 * 1024
MAX_IMPORT_BODY_BYTES = 12 * 1024 * 1024
MAX_IMPORTED_DATASETS = 50

# Config names mirror the engine contract. The engine also validates relationships.
NUMBERS = {
    "backtesting": {"initial_capital": (1, 100000000), "commission_rate": (0, .1),
                    "commission_fixed": (0, 10000), "slippage": (0, .1)},
    "risk_management": {"max_position_size": (.000001, 1), "max_drawdown": (.000001, 1),
                        "stop_loss_pct": (0, 1), "take_profit_pct": (0, 1), "max_daily_loss": (.000001, 1)},
    "SMA_CROSSOVER": {"short_period": (1, 100000), "long_period": (2, 100000)},
    "EMA_CROSSOVER": {"short_period": (1, 100000), "long_period": (2, 100000)},
    "RSI": {"rsi_period": (1, 100000), "oversold_threshold": (0, 100), "overbought_threshold": (0, 100)},
    "VWAP_OPENING": {"bar_minutes": (1, 30), "session_open_minute": (0, 1439),
                     "session_close_minute": (0, 1439), "opening_window_minutes": (1, 120),
                     "fast_ema": (1, 100000), "medium_ema": (1, 100000), "slow_ema": (1, 100000),
                     "min_body_fraction": (0, 1), "min_close_location": (0, 1),
                     "min_move_bps": (0, 10000), "min_vwap_slope_bps": (0, 10000),
                     "min_vwap_distance_bps": (0, 10000), "trend_ema_period": (1, 120),
                     "exit_buffer_minutes": (1, 1438), "enable_short_signals": (0, 1)},
}
INTEGERS = {"short_period", "long_period", "rsi_period", "bar_minutes", "session_open_minute",
            "session_close_minute", "opening_window_minutes", "fast_ema", "medium_ema", "slow_ema",
            "trend_ema_period", "exit_buffer_minutes", "enable_short_signals"}


def validate_section(name, section):
    if not isinstance(section, dict):
        raise ValueError(f"{name} must be an object")
    for key, value in section.items():
        if key in NUMBERS[name]:
            low, high = NUMBERS[name][key]
            if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
                raise ValueError(f"{name}.{key} must be a finite number in [{low}, {high}]")
            if key in INTEGERS and int(value) != value:
                raise ValueError(f"{name}.{key} must be a whole number")
        elif name == "backtesting" and key == "enable_short_selling":
            if value is not False:
                raise ValueError("live execution and short-selling execution are disabled")
        elif name == "backtesting" and key in ("start_date", "end_date"):
            if not isinstance(value, str) or (value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
                raise ValueError(f"{key} must use YYYY-MM-DD")
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise ValueError(f"{key} must be a valid calendar date") from None
        elif name == "backtesting" and key == "symbol":
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,24}", value):
                raise ValueError("symbol must contain 1-24 letters, digits, dot, underscore, or hyphen")
        else:
            raise ValueError(f"unsupported configuration: {name}.{key}")


def validate_request(payload, extra_datasets=None):
    if not isinstance(payload, dict) or set(payload) - {"request_id", "dataset", "strategy", "config"}:
        raise ValueError("request must contain request_id, dataset, strategy, and optional config")
    if not isinstance(payload.get("request_id"), str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", payload["request_id"]):
        raise ValueError("request_id must be 1-128 letters, digits, or ._:- characters")
    extra_datasets = extra_datasets or {}
    dataset_id = payload.get("dataset")
    if not isinstance(dataset_id, str) or (dataset_id not in DATASETS and dataset_id not in extra_datasets):
        raise ValueError("unknown dataset; use a bundled or registered dataset ID")
    if payload.get("strategy") not in STRATEGIES:
        raise ValueError("unknown strategy")
    config = payload.get("config", {})
    if not isinstance(config, dict) or set(config) - {"backtesting", "risk_management", "strategies"}:
        raise ValueError("config only accepts backtesting, risk_management, and strategies")
    for section in ("backtesting", "risk_management"):
        if section in config:
            validate_section(section, config[section])
    dates = config.get("backtesting", {})
    if dates.get("start_date") and dates.get("end_date") and dates["start_date"] > dates["end_date"]:
        raise ValueError("start_date must be on or before end_date")
    if "strategies" in config:
        if not isinstance(config["strategies"], dict) or set(config["strategies"]) - set(STRATEGIES):
            raise ValueError("unsupported strategies configuration")
        for name, section in config["strategies"].items():
            validate_section(name, section)
    if dataset_id in extra_datasets:
        metadata = extra_datasets[dataset_id]
        config = copy.deepcopy(config)
        backtesting = config.setdefault("backtesting", {})
        if "symbol" in backtesting and backtesting["symbol"] != metadata["symbol"]:
            raise ValueError("backtesting.symbol must match the imported dataset symbol")
        backtesting["symbol"] = metadata["symbol"]
        configured_vwap = config.get("strategies", {}).get("VWAP_OPENING")
        if payload["strategy"] == "VWAP_OPENING" or configured_vwap is not None:
            if metadata["bar_minutes"] == 0:
                if payload["strategy"] == "VWAP_OPENING":
                    raise ValueError("VWAP_OPENING requires an intraday dataset")
            else:
                settings = config.setdefault("strategies", {}).setdefault("VWAP_OPENING", {})
                for key in ("bar_minutes", "session_open_minute", "session_close_minute"):
                    if key in settings and settings[key] != metadata[key]:
                        raise ValueError(f"VWAP_OPENING.{key} must match imported dataset metadata")
                    settings[key] = metadata[key]
                settings.setdefault("exit_buffer_minutes", max(4, metadata["bar_minutes"]))
                if settings["exit_buffer_minutes"] < metadata["bar_minutes"]:
                    raise ValueError("VWAP_OPENING.exit_buffer_minutes must be at least bar_minutes")
    return {"request_id": payload["request_id"], "dataset": payload["dataset"],
            "strategy": payload["strategy"], "config": config}


def dataset_bytes(dataset):
    path = ROOT / "data" / DATASETS[dataset]["file"]
    if path.stat().st_size > MAX_DATASET_BYTES:
        raise ValueError("bundled dataset exceeds the service limit")
    return path.read_bytes()


def preset_info(dataset):
    value = DATASETS.get(dataset)
    return {"id": dataset, **{k: v for k, v in value.items() if k != "file"}} if value else {}


def catalog(imported_datasets=None):
    return {
        "datasets": [preset_info(key) for key in DATASETS] + list(imported_datasets or []),
        "strategies": [{"id": key, "name": key.replace("_", " ").title()} for key in STRATEGIES],
        "defaults": {"dataset": "opening_demo", "strategy": "VWAP_OPENING",
                     "backtesting": {"initial_capital": 10000, "commission_rate": .001,
                                     "commission_fixed": 0, "slippage": .0001},
                     "risk_management": {"max_position_size": .1}},
        "limits": {"max_request_bytes": MAX_BODY_BYTES, "max_dataset_bytes": MAX_DATASET_BYTES,
                   "max_import_request_bytes": MAX_IMPORT_BODY_BYTES, "max_imported_datasets": MAX_IMPORTED_DATASETS,
                   "engine_timeout_seconds": 60, "list_runs": 100, "max_active_runs": 50,
                   "max_total_runs": 1000, "execution": "local simulation only"},
    }
