"""Public API accepts bounded configuration and bundled dataset IDs only."""
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
DATASETS = {
    "sample": {"name": "Synthetic daily cycle", "file": "daily_demo.csv", "description": "Synthetic weekday daily OHLCV cycle. Not market performance evidence."},
    "opening_demo": {"name": "Opening VWAP demo", "file": "opening_demo.csv", "description": "Synthetic two-minute bars with weekly/monthly warm-up. Not market performance evidence."},
}
STRATEGIES = ("SMA_CROSSOVER", "EMA_CROSSOVER", "RSI", "VWAP_OPENING")
MAX_DATASET_BYTES = 8 * 1024 * 1024
MAX_BODY_BYTES = 32 * 1024

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
        elif name == "backtesting" and key == "symbol":
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,24}", value):
                raise ValueError("symbol must contain 1-24 letters, digits, dot, underscore, or hyphen")
        else:
            raise ValueError(f"unsupported configuration: {name}.{key}")


def validate_request(payload):
    if not isinstance(payload, dict) or set(payload) - {"request_id", "dataset", "strategy", "config"}:
        raise ValueError("request must contain request_id, dataset, strategy, and optional config")
    if not isinstance(payload.get("request_id"), str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", payload["request_id"]):
        raise ValueError("request_id must be 1-128 letters, digits, or ._:- characters")
    if payload.get("dataset") not in DATASETS:
        raise ValueError("unknown dataset; use a bundled dataset ID")
    if payload.get("strategy") not in STRATEGIES:
        raise ValueError("unknown strategy")
    config = payload.get("config", {})
    if not isinstance(config, dict) or set(config) - {"backtesting", "risk_management", "strategies"}:
        raise ValueError("config only accepts backtesting, risk_management, and strategies")
    for section in ("backtesting", "risk_management"):
        if section in config:
            validate_section(section, config[section])
    if "strategies" in config:
        if not isinstance(config["strategies"], dict) or set(config["strategies"]) - set(STRATEGIES):
            raise ValueError("unsupported strategies configuration")
        for name, section in config["strategies"].items():
            validate_section(name, section)
    return {"request_id": payload["request_id"], "dataset": payload["dataset"],
            "strategy": payload["strategy"], "config": config}


def dataset_bytes(dataset):
    path = ROOT / "data" / DATASETS[dataset]["file"]
    if path.stat().st_size > MAX_DATASET_BYTES:
        raise ValueError("bundled dataset exceeds the service limit")
    return path.read_bytes()


def catalog():
    return {
        "datasets": [{"id": key, **{k: v for k, v in value.items() if k != "file"}} for key, value in DATASETS.items()],
        "strategies": [{"id": key, "name": key.replace("_", " ").title()} for key in STRATEGIES],
        "defaults": {"dataset": "opening_demo", "strategy": "VWAP_OPENING",
                     "backtesting": {"initial_capital": 10000, "commission_rate": .001,
                                     "commission_fixed": 0, "slippage": .0001},
                     "risk_management": {"max_position_size": .1}},
        "limits": {"max_request_bytes": MAX_BODY_BYTES, "max_dataset_bytes": MAX_DATASET_BYTES,
                   "engine_timeout_seconds": 60, "list_runs": 100, "max_active_runs": 50,
                   "max_total_runs": 1000, "execution": "local simulation only"},
    }
