"""Durability tests use real SQLite files and real process termination."""
from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.server import LocalServer
from service.store import Store, Conflict, StaleClaim, canonical, replay
from service.validation import validate_request, dataset_bytes
from service.worker import find_engine, work_once


def example_result():
    def event(i, kind, qty, price, fee, delta, cash, held, basis, realized):
        return {"event_id": str(i), "timestamp": f"2025-01-0{i + 1} 09:30:00", "type": kind,
                "quantity": qty, "price_cents": price, "fee_cents": fee, "cash_delta_cents": delta,
                "cash_after_cents": cash, "quantity_after": held, "cost_basis_after_cents": basis,
                "realized_pnl_after_cents": realized}
    return {"schema_version": 1, "engine_version": "test-fixture", "effective_config": {},
            "results": {"initial_cash_cents": 1000000, "final_cash_cents": 1009700,
                        "final_equity_cents": 1009700, "final_quantity": 0, "cost_basis_cents": 0,
                        "realized_pnl_cents": 9700, "unrealized_pnl_cents": 0, "total_fees_cents": 300,
                        "events": [event(0, "DEPOSIT", 0, 0, 0, 1000000, 1000000, 0, 0, 0),
                                   event(1, "BUY", 10, 10000, 100, -100100, 899900, 10, 100100, 0),
                                   event(2, "SELL", 4, 11000, 100, 43900, 943800, 6, 60060, 3860),
                                   event(3, "SELL", 6, 11000, 100, 65900, 1009700, 0, 0, 9700)]}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "state.sqlite3"
        self.store = Store(self.path)
        self.store.initialize()
        self.payload = validate_request({"request_id": "request-1", "dataset": "sample", "strategy": "SMA_CROSSOVER"})

    def tearDown(self):
        self.temporary.cleanup()

    def submit(self, request_id=None):
        p = dict(self.payload)
        if request_id:
            p["request_id"] = request_id
        return self.store.submit(p, b"immutable CSV fixture\n")[0]

    def test_duplicate_is_durable_and_preserves_dataset_snapshot(self):
        first = self.submit()
        reopened = Store(self.path)
        second, created = reopened.submit(self.payload, b"changed source\n")
        self.assertFalse(created)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["dataset_sha256"], second["dataset_sha256"])
        self.assertEqual(reopened.claim()["dataset_bytes"], b"immutable CSV fixture\n")

    def test_same_key_different_payload_conflicts(self):
        self.submit()
        with self.assertRaises(Conflict):
            self.store.submit({**self.payload, "strategy": "RSI"}, b"fixture")
        self.assertEqual(len(self.store.list()), 1)

    def test_concurrent_identical_submissions_create_one_operation(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.submit(self.payload, b"fixture"), range(20)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({run["id"] for run, _ in results}), 1)

    def test_concurrent_claims_have_one_owner(self):
        self.submit()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.claim(), range(20)))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_expired_worker_cannot_publish_after_takeover(self):
        self.submit()
        first = self.store.claim(now=time.time() - 120, lease_seconds=10)
        second = self.store.claim()
        self.assertEqual(second["attempt"], 2)
        self.assertNotEqual(first["claim_token"], second["claim_token"])
        with self.assertRaises(StaleClaim):
            self.store.publish(first, example_result())
        self.store.publish(second, example_result())
        self.assertEqual(self.store.get(first["id"])["status"], "completed")
        self.assertTrue(self.store.reconcile(first["id"])["ok"])

    def test_expired_lease_cannot_publish_without_takeover(self):
        self.submit()
        claim = self.store.claim(now=time.time() - 20, lease_seconds=10)
        with self.assertRaises(StaleClaim):
            self.store.publish(claim, example_result())
        self.assertEqual(self.store.events(claim["id"]), [])

    def test_cancellation_wins_against_pending_publication(self):
        run = self.submit()
        claim = self.store.claim()
        self.store.cancel(run["id"])
        self.assertEqual(self.store.cancel(run["id"])["status"], "cancelled")
        with self.assertRaises(StaleClaim):
            self.store.publish(claim, example_result())
        self.assertEqual(self.store.events(run["id"]), [])
        self.assertIsNone(self.store.claim())

    def test_completed_publication_wins_against_cancellation(self):
        run = self.submit()
        self.store.publish(self.store.claim(), example_result())
        with self.assertRaises(Conflict):
            self.store.cancel(run["id"])
        self.assertEqual(self.store.get(run["id"])["status"], "completed")

    def test_invalid_accounting_leaves_no_partial_publication(self):
        run = self.submit()
        bad = example_result()
        bad["results"]["events"][2]["cash_delta_cents"] += 1
        with self.assertRaisesRegex(ValueError, "sell cash mismatch"):
            self.store.publish(self.store.claim(), bad)
        self.assertEqual(self.store.events(run["id"]), [])
        with self.assertRaises(Conflict):
            self.store.result(run["id"])

    def test_replay_reconstructs_partial_sale_basis(self):
        outcome = replay(example_result())
        self.assertEqual(outcome["cash_cents"], 1009700)
        self.assertEqual(outcome["realized_pnl_cents"], 9700)
        self.assertEqual(outcome["fees_cents"], 300)

    def test_event_and_result_history_are_immutable(self):
        self.submit()
        self.store.publish(self.store.claim(), example_result())
        for table in ("events", "results"):
            with self.store.connect() as db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"DELETE FROM {table}")
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"UPDATE {table} SET document='{{}}'")

    def test_reconciliation_detects_corrupted_read_model(self):
        run = self.submit()
        self.store.publish(self.store.claim(), example_result())
        # Explicitly break the immutable guard to simulate disk/operator corruption.
        with self.store.connect() as db:
            result = json.loads(db.execute("SELECT document FROM results").fetchone()[0])
            result["results"]["final_cash_cents"] += 1
            db.execute("DROP TRIGGER immutable_results_update")
            db.execute("UPDATE results SET document=?", (canonical(result),))
        with self.assertRaisesRegex(ValueError, "does not reconcile"):
            self.store.reconcile(run["id"])

    def child_crash(self, point):
        document = Path(self.temporary.name) / "result.json"
        document.write_text(canonical(example_result()), encoding="utf-8")
        code = ("import json,sys; from service.store import Store; "
                "s=Store(sys.argv[1]); c=s.claim(lease_seconds=0.2); "
                "s.publish(c,json.load(open(sys.argv[2])),crash_point=sys.argv[3])")
        return subprocess.run([sys.executable, "-c", code, str(self.path), str(document), point],
                              cwd=ROOT, timeout=10, capture_output=True)

    def test_real_process_crash_before_commit_rolls_back_and_retries(self):
        run = self.submit()
        process = self.child_crash("before_commit")
        self.assertEqual(process.returncode, 91, process.stderr)
        reopened = Store(self.path)
        self.assertEqual(reopened.events(run["id"]), [])
        self.assertEqual(reopened.get(run["id"])["status"], "running")
        # Simulate lease expiry deterministically without sleeping.
        retry = reopened.claim(now=time.time() + 1)
        self.assertEqual(retry["attempt"], 2)
        reopened.publish(retry, example_result())
        self.assertEqual(len(reopened.events(run["id"])), 4)
        self.assertTrue(reopened.reconcile(run["id"])["ok"])

    def test_real_process_crash_after_commit_then_lost_ack_retry(self):
        run = self.submit()
        process = self.child_crash("after_commit")
        self.assertEqual(process.returncode, 92, process.stderr)
        reopened = Store(self.path)
        repeated, created = reopened.submit(self.payload, b"immutable CSV fixture\n")
        self.assertFalse(created)
        self.assertEqual(repeated["id"], run["id"])
        self.assertEqual(repeated["status"], "completed")
        self.assertIsNone(reopened.claim())
        self.assertEqual(len(reopened.events(run["id"])), 4)
        self.assertEqual(reopened.reconcile(run["id"])["cash_cents"], 1009700)

    def test_abandoned_jobs_have_bounded_retries(self):
        run = self.submit()
        for attempt in range(3):
            self.assertIsNotNone(self.store.claim(now=time.time() - 100 + attempt * 10, lease_seconds=1))
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.get(run["id"])["status"], "failed")

    def test_capacity_limit_still_allows_identical_retry(self):
        first = self.submit()
        for i in range(49):
            self.submit(f"other-{i}")
        with self.assertRaisesRegex(Conflict, "capacity"):
            self.submit("overflow")
        self.assertEqual(self.submit()["id"], first["id"])

    def test_actual_engine_result_is_durable_and_reconciles(self):
        try:
            engine = find_engine(os.environ.get("TRADING_BOT_ENGINE"))
        except ValueError:
            self.skipTest("Build the C++ engine or set TRADING_BOT_ENGINE to run integration")
        for dataset, strategy, parameters in (("sample", "SMA_CROSSOVER", {"short_period": 2, "long_period": 3}),
                                               ("opening_demo", "VWAP_OPENING", {"opening_window_minutes": 15})):
            with self.subTest(dataset=dataset, strategy=strategy):
                payload = validate_request({"request_id": f"real-engine-{dataset}", "dataset": dataset,
                                            "strategy": strategy, "config": {"strategies": {strategy: parameters}}})
                run, _ = self.store.submit(payload, dataset_bytes(dataset))
                self.assertTrue(work_once(self.store, engine), self.store.get(run["id"])["error"])
                self.assertTrue(self.store.reconcile(run["id"])["ok"])
                self.assertTrue(self.store.result(run["id"])["engine_version"])


class ValidationTests(unittest.TestCase):
    def test_unsafe_payloads_rejected(self):
        base = {"request_id": "valid", "dataset": "sample", "strategy": "RSI"}
        invalid = [None, [], {**base, "dataset": "../../secrets"}, {**base, "strategy": "x; cmd"},
                   {**base, "request_id": "a\nb"}, {**base, "config": {"path": "secret"}},
                   {**base, "config": {"backtesting": {"initial_capital": float("nan")}}},
                   {**base, "config": {"backtesting": {"enable_short_selling": True}}},
                   {**base, "config": {"strategies": {"RSI": {"rsi_period": 1.1}}}}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                validate_request(payload)

    def test_vwap_opening_window_override_allowed(self):
        payload = validate_request({"request_id": "vwap", "dataset": "opening_demo", "strategy": "VWAP_OPENING",
                                    "config": {"strategies": {"VWAP_OPENING": {"opening_window_minutes": 15}}}})
        self.assertEqual(payload["config"]["strategies"]["VWAP_OPENING"]["opening_window_minutes"], 15)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        store = Store(Path(self.temporary.name) / "state.sqlite3")
        store.initialize()
        self.server = LocalServer(("127.0.0.1", 0), store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        body = json.dumps(payload) if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, json.loads(data)

    def test_submit_retry_conflict_cancel_through_http(self):
        payload = {"request_id": "http-1", "dataset": "sample", "strategy": "RSI"}
        status, first = self.request("POST", "/api/runs", payload)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("POST", "/api/runs", payload)[0], 200)
        self.assertEqual(self.request("POST", "/api/runs", {**payload, "strategy": "EMA_CROSSOVER"})[0], 409)
        run_id = first["run"]["id"]
        self.assertEqual(self.request("GET", f"/api/runs/{run_id}/result")[0], 409)
        self.assertEqual(self.request("POST", f"/api/runs/{run_id}/cancel")[1]["run"]["status"], "cancelled")

    def test_local_host_and_origin_enforced(self):
        self.assertEqual(self.request("GET", "/api/catalog", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/catalog", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/catalog")[0], 200)

    def test_local_font_served_as_woff2_with_existing_security_headers(self):
        font = ROOT / "service" / "static" / "fonts" / "manrope.woff2"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        try:
            connection.request("GET", "/fonts/manrope.woff2")
            response = connection.getresponse()
            data = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "font/woff2")
            self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
            self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
            self.assertEqual(data, font.read_bytes())
            self.assertEqual(data[:4], b"wOF2")
            self.assertEqual(int(response.getheader("Content-Length")), len(data))
        finally:
            connection.close()

    def test_traversal_and_oversized_body_rejected(self):
        self.assertEqual(self.request("GET", "/%2e%2e/config.json")[0], 404)
        self.assertEqual(self.request("POST", "/api/runs", {"request_id": "x" * 40000})[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
