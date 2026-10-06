"""Bounded subprocess worker with generation-checked publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import threading
import time

from .store import Store, StaleClaim, canonical
from .validation import ROOT

LOG = logging.getLogger("trading_service")
MAX_RESULT_BYTES = 64 * 1024 * 1024


def find_engine(explicit=None):
    candidates = [Path(explicit)] if explicit else [
        ROOT / "build" / "trading_bot.exe", ROOT / "build" / "Release" / "trading_bot.exe",
        ROOT / "build" / "trading_bot", ROOT / "build" / "bin" / "trading_bot.exe",
        ROOT / "build" / "bin" / "trading_bot", ROOT / "build" / "bin" / "Release" / "trading_bot.exe",
        ROOT / "build" / "bin" / "Debug" / "trading_bot.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError("C++ engine not found. Build trading_bot or pass --engine PATH.")


def run_claim(store, claim, engine, timeout=60, crash_point=None):
    started = time.monotonic()
    try:
        payload = json.loads(claim["payload"])
        if hashlib.sha256(claim["dataset_bytes"]).hexdigest() != claim["dataset_sha256"]:
            raise ValueError("dataset snapshot fingerprint mismatch")
        with tempfile.TemporaryDirectory(prefix="trading-run-") as temporary:
            directory = Path(temporary)
            config_path, data_path, output_path = (directory / n for n in ("config.json", "data.csv", "result.json"))
            config_path.write_text(canonical(payload.get("config", {})), encoding="utf-8")
            data_path.write_bytes(claim["dataset_bytes"])
            args = [str(engine), "--config", str(config_path), "--data", str(data_path),
                    "--strategy", claim["strategy"], "--output", str(output_path)]
            with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
                completed = subprocess.run(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, timeout=timeout, shell=False)
            if completed.returncode:
                with (directory / "stderr.log").open("rb") as error_file:
                    error = error_file.read(2000).decode("utf-8", "replace")
                raise ValueError(f"engine exited {completed.returncode}: {error}")
            if not output_path.is_file() or output_path.stat().st_size > MAX_RESULT_BYTES:
                raise ValueError("engine result missing or too large")
            result = json.loads(output_path.read_text(encoding="utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        store.publish(claim, result, time.monotonic() - started, crash_point=crash_point)
        LOG.info(canonical({"event": "run_completed", "run_id": claim["id"], "attempt": claim["attempt"],
                            "seconds": round(time.monotonic() - started, 6)}))
        return True
    except StaleClaim:
        LOG.info(canonical({"event": "stale_result_discarded", "run_id": claim["id"], "attempt": claim["attempt"]}))
        return False
    except Exception as error:
        try:
            store.fail(claim, error)
        except StaleClaim:
            return False
        LOG.error(canonical({"event": "run_failed", "run_id": claim["id"], "error": str(error)[:2000]}))
        return False


def work_once(store, engine, lease_seconds=90, timeout=60, crash_point=None):
    claim = store.claim(lease_seconds=lease_seconds)
    if claim is None:
        return None
    return run_claim(store, claim, engine, timeout=timeout, crash_point=crash_point)


def worker_loop(store, engine, stop=None):
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            if work_once(store, engine) is None:
                stop.wait(.5)
        except sqlite3.Error:
            LOG.exception("database unavailable; worker will retry")
            stop.wait(1)


def main():
    parser = argparse.ArgumentParser(description="Run local backtest jobs")
    parser.add_argument("--db", default=str(ROOT / ".local" / "trading.sqlite3"))
    parser.add_argument("--engine")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--lease-seconds", type=float, default=90)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--crash-point", choices=("before_commit", "after_commit"), help="Recovery demonstration only")
    args = parser.parse_args()
    if args.lease_seconds <= 0 or not 0 < args.timeout <= 60:
        parser.error("positive lease and timeout up to 60 seconds required")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    store = Store(args.db)
    store.initialize()
    engine = find_engine(args.engine)
    if args.once:
        ok = work_once(store, engine, args.lease_seconds, args.timeout, args.crash_point)
        return 1 if ok is False else 0
    worker_loop(store, engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
