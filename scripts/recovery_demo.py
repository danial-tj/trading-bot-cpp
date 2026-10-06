"""Crash the real worker around publication and verify durable accounting."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.store import Store
from service.validation import dataset_bytes, validate_request
from service.worker import find_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine")
    args = parser.parse_args()
    engine = find_engine(args.engine)
    reports = []
    with tempfile.TemporaryDirectory(prefix="trading-recovery-") as temporary:
        for point in ("before_commit", "after_commit"):
            db = Path(temporary) / f"{point}.sqlite3"
            store = Store(db)
            store.initialize()
            payload = validate_request({"request_id": "recovery-demonstration", "dataset": "sample",
                                        "strategy": "SMA_CROSSOVER", "config": {
                                            "strategies": {"SMA_CROSSOVER": {"short_period": 2, "long_period": 3}}}})
            submitted, _ = store.submit(payload, dataset_bytes("sample"))
            command = [sys.executable, "-m", "service.worker", "--db", str(db), "--engine", str(engine), "--once"]
            crashed = subprocess.run(command + ["--crash-point", point], cwd=ROOT, capture_output=True, timeout=70)
            expected_exit = 91 if point == "before_commit" else 92
            if crashed.returncode != expected_exit:
                raise AssertionError(f"crash hook was not reached: {crashed.returncode}, {crashed.stderr.decode()}")
            reopened = Store(db)
            repeated, created = reopened.submit(payload, dataset_bytes("sample"))
            assert not created and repeated["id"] == submitted["id"]
            if point == "before_commit":
                assert reopened.events(submitted["id"]) == []
                # Administrative clock injection avoids a 90s wait, preserving the
                # exact expired-lease recovery path. The worker is already dead.
                with reopened.connect() as connection:
                    connection.execute("UPDATE runs SET lease_until=? WHERE id=?", (time.time() - 1, submitted["id"]))
            restarted = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=70)
            assert restarted.returncode == 0, restarted.stderr.decode()
            result = reopened.result(submitted["id"])
            reconciliation = reopened.reconcile(submitted["id"])
            events = reopened.events(submitted["id"])
            # Event type naming is presentation-only; inventory changes also identify fills.
            held = 0
            inventory_fills = 0
            for event in events:
                inventory_fills += event["quantity_after"] != held
                held = event["quantity_after"]
            assert inventory_fills > 0, "recovery fixture must exercise at least one fill"
            assert inventory_fills == len(result["results"]["trades"])
            assert len(events) == len({e["event_id"] for e in events})
            assert len(events) == len(result["results"]["events"])
            with reopened.connect() as connection:
                publications = connection.execute("SELECT COUNT(*) FROM results WHERE run_id=?", (submitted["id"],)).fetchone()[0]
            assert publications == 1
            reports.append({"crash_point": point, "worker_exit": crashed.returncode,
                            "same_request_same_run": True, "publication_count": publications,
                            "attempts": reopened.get(submitted["id"])["attempt"],
                            "fills": inventory_fills, "reconciliation": reconciliation})
    print(json.dumps({"passed": True, "boundary": "one committed publication per run in SQLite", "scenarios": reports}, indent=2))


if __name__ == "__main__":
    main()
