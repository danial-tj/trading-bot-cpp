"""Offline provider-job tests: fake clients only, no real authentication."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.import_data import validate_import
from service.provider_jobs import ProviderJobs
from service.server import LocalServer
from service.store import Store, Conflict, NotFound

SECRET = "refresh-secret-never-persist-or-log"
IMPORT = {"request_id": "history-1", "symbol_id": 123, "start_date": "2025-01-02",
          "end_date": "2025-01-03", "bar_minutes": 2}


def dataset():
    return validate_import({
        "name": "Provider test data", "symbol": "TEST", "source": "Fake offline provider",
        "timezone": "America/New_York", "currency": "USD", "price_adjustment": "unknown",
        "bar_minutes": 2,
        "csv": "timestamp,open,high,low,close,volume\n2025-01-02T09:30,100,102,99,101,1000\n",
    })


class FakeClient:
    def __init__(self, connected=True, immediate=False, failure=None):
        self.connected = connected
        self.immediate = immediate
        self.failure = failure
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.disconnects = 0
        self.leak_extra_fields = False

    def status(self):
        value = {"provider": "Questrade", "connected": self.connected,
                 "state": "connected" if self.connected else "disconnected",
                 "expires_at": "2099-01-01T00:00:00+00:00" if self.connected else None,
                 "can_refresh": self.connected,
                 "history_progress": {"completed_chunks": 0, "total_chunks": 1, "rows": 0}}
        if self.leak_extra_fields:
            value["refresh_token"] = SECRET
            value["history_progress"]["access_token"] = SECRET
        return value

    def connect(self, token):
        self.calls.append("connect")
        if self.failure is not None:
            raise self.failure
        self.connected = True
        return self.status()

    def disconnect(self):
        self.disconnects += 1
        self.connected = False
        self.release.set()

    def search_symbols(self, prefix):
        self.calls.append(("search", prefix))
        if self.failure is not None:
            raise self.failure
        return [{"id": 123, "symbol": "TEST", "description": "Test symbol", "currency": "USD",
                 "refresh_token": SECRET}]

    def historical_import(self, params, cancel_event=None):
        self.calls.append(("history", dict(params)))
        self.entered.set()
        if not self.immediate:
            if not self.release.wait(timeout=5):
                raise RuntimeError("fake client did not receive its release")
        if self.failure is not None:
            raise self.failure
        # Deliberately ignore cancellation: ProviderJobs must prevent publishing
        # even when the upstream request returns successfully after cancellation.
        return dataset()


def wait_finished(jobs, job_id, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = jobs.get(job_id)
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(.005)
    raise AssertionError("provider job did not reach a terminal state")


class ProviderJobsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / "state.sqlite3")
        self.store.initialize()
        self.client = FakeClient()
        self.jobs = ProviderJobs(self.store, self.client)

    def tearDown(self):
        self.client.release.set()
        self.jobs.shutdown()
        self.temporary.cleanup()

    def start(self, **overrides):
        job, created = self.jobs.start({**IMPORT, **overrides})
        self.assertTrue(created)
        self.assertTrue(self.client.entered.wait(timeout=2))
        return job

    def test_job_is_async_and_import_is_published_only_on_completion(self):
        started = time.monotonic()
        job = self.start()
        self.assertLess(time.monotonic() - started, 1)
        self.assertIn(self.jobs.get(job["id"])["status"], {"queued", "running"})
        self.assertEqual(self.store.imported_datasets(), [])
        self.client.release.set()
        final = wait_finished(self.jobs, job["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(self.store.imported_datasets(), [final["dataset"]])
        self.assertEqual(self.jobs.latest(), final)

    def test_idempotent_retries_share_one_active_job_and_one_import(self):
        job = self.start()
        retry, created = self.jobs.start(dict(IMPORT))
        self.assertFalse(created)
        self.assertEqual(retry["id"], job["id"])
        self.client.release.set()
        final = wait_finished(self.jobs, job["id"])
        retry, created = self.jobs.start(dict(IMPORT))
        self.assertFalse(created)
        self.assertEqual(retry, final)
        self.assertEqual(sum(call[0] == "history" for call in self.client.calls if isinstance(call, tuple)), 1)
        self.assertEqual(len(self.store.imported_datasets()), 1)

    def test_conflicting_request_id_and_second_active_job_are_rejected(self):
        self.start()
        with self.assertRaisesRegex(Conflict, "different"):
            self.jobs.start({**IMPORT, "bar_minutes": 5})
        with self.assertRaisesRegex(Conflict, "one historical"):
            self.jobs.start({**IMPORT, "request_id": "second"})
        self.assertEqual(len(self.client.calls), 1)

    def test_concurrent_identical_requests_share_one_job(self):
        barrier = threading.Barrier(6)

        def start(_):
            barrier.wait(timeout=3)
            return self.jobs.start(dict(IMPORT))

        with ThreadPoolExecutor(max_workers=6) as pool:
            attempts = list(pool.map(start, range(6)))
        self.assertEqual(len({job["id"] for job, _ in attempts}), 1)
        self.assertEqual(sum(created for _, created in attempts), 1)
        self.assertTrue(self.client.entered.wait(timeout=2))
        self.assertEqual(len(self.client.calls), 1)

    def test_cancel_before_response_prevents_all_dataset_publication(self):
        job = self.start()
        cancelled = self.jobs.cancel({"job_id": job["id"]})
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.jobs.cancel({"job_id": job["id"]}), cancelled)
        self.client.release.set()
        self.jobs._thread.join(timeout=2)
        self.assertEqual(self.jobs.get(job["id"])["status"], "cancelled")
        self.assertEqual(self.store.imported_datasets(), [])
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 0)

    def test_cancel_and_publication_are_serialized_without_false_cancellation(self):
        entered = threading.Event()
        release = threading.Event()
        original_import = self.store.import_dataset

        def blocking_import(metadata, content):
            entered.set()
            self.assertTrue(release.wait(timeout=3))
            return original_import(metadata, content)

        self.store.import_dataset = blocking_import
        job = self.start()
        self.client.release.set()
        self.assertTrue(entered.wait(timeout=2))
        with ThreadPoolExecutor(max_workers=1) as pool:
            cancelled = pool.submit(self.jobs.cancel, {"job_id": job["id"]})
            time.sleep(.03)
            self.assertFalse(cancelled.done())
            release.set()
            with self.assertRaises(Conflict):
                cancelled.result(timeout=2)
        self.assertEqual(wait_finished(self.jobs, job["id"])["status"], "completed")
        self.assertEqual(len(self.store.imported_datasets()), 1)

    def test_failure_is_redacted_and_no_partial_dataset_is_saved(self):
        self.client.failure = RuntimeError("Authorization: Bearer " + SECRET)
        job = self.start()
        self.client.release.set()
        final = wait_finished(self.jobs, job["id"])
        self.assertEqual(final["status"], "failed")
        self.assertNotIn(SECRET, json.dumps(final))
        self.assertEqual(self.store.imported_datasets(), [])

    def test_arbitrary_value_error_is_not_assumed_safe(self):
        self.client.failure = ValueError(SECRET)
        job = self.start()
        self.client.release.set()
        final = wait_finished(self.jobs, job["id"])
        self.assertEqual(final["status"], "failed")
        self.assertNotIn(SECRET, final["error"])

    def test_completed_and_failed_jobs_cannot_be_relabelled_cancelled(self):
        job = self.start()
        self.client.release.set()
        wait_finished(self.jobs, job["id"])
        with self.assertRaises(Conflict):
            self.jobs.cancel({"job_id": job["id"]})
        with self.assertRaises(NotFound):
            self.jobs.cancel({"job_id": "f" * 32})

    def test_disconnect_cancels_before_clearing_client(self):
        job = self.start()
        connection = self.jobs.disconnect({})
        self.jobs._thread.join(timeout=2)
        self.assertFalse(connection["connected"])
        self.assertEqual(self.jobs.get(job["id"])["status"], "cancelled")
        self.assertEqual(self.client.disconnects, 1)
        self.assertEqual(self.store.imported_datasets(), [])

    def test_shutdown_cancels_and_refuses_new_jobs_without_persistence(self):
        job = self.start()
        self.jobs.shutdown()
        self.client.release.set()
        self.jobs._thread.join(timeout=2)
        self.assertEqual(self.jobs.get(job["id"])["status"], "cancelled")
        with self.assertRaises(Conflict):
            self.jobs.start({**IMPORT, "request_id": "after-close"})
        reopened = ProviderJobs(self.store, FakeClient())
        self.assertIsNone(reopened.latest())
        reopened.shutdown()
        self.assertEqual(self.store.imported_datasets(), [])

    def test_only_last_twenty_jobs_and_request_ids_are_retained(self):
        self.client.immediate = True
        first_id = None
        for index in range(21):
            job, created = self.jobs.start({**IMPORT, "request_id": f"job-{index}"})
            self.assertTrue(created)
            first_id = first_id or job["id"]
            wait_finished(self.jobs, job["id"])
            self.jobs._thread.join(timeout=2)
        self.assertEqual(len(self.jobs._jobs), 20)
        self.assertEqual(len(self.jobs._requests), 20)
        with self.assertRaises(NotFound):
            self.jobs.get(first_id)
        job, created = self.jobs.start({**IMPORT, "request_id": "job-0"})
        self.assertTrue(created)
        self.assertNotEqual(job["id"], first_id)
        wait_finished(self.jobs, job["id"])

    def test_validation_prevents_network_on_bad_params(self):
        invalid = [
            {"extra": 1}, {"symbol_id": True}, {"symbol_id": 0}, {"symbol_id": 2147483648},
            {"bar_minutes": 3}, {"bar_minutes": "2"}, {"start_date": "2025-02-29"},
            {"start_date": "2025-01-04"}, {"start_date": "2020-01-01"},
            {"end_date": "9999-12-31"}, {"start_date": "2025-1-2"},
            {"request_id": "bad\nid"},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.jobs.start({**IMPORT, **overrides})
        with self.assertRaises(ValueError):
            self.jobs.start({key: value for key, value in IMPORT.items() if key != "end_date"})
        self.assertEqual(self.client.calls, [])

    def test_connection_is_required_and_reconnect_blocked_during_download(self):
        self.client.connected = False
        with self.assertRaises(Conflict):
            self.jobs.start(dict(IMPORT))
        state = self.jobs.connect({"refresh_token": SECRET})
        self.assertTrue(state["connected"])
        job = self.start()
        with self.assertRaises(Conflict):
            self.jobs.connect({"refresh_token": "replacement"})
        self.assertNotIn(SECRET, json.dumps(job))
        self.assertNotIn(SECRET, repr(self.jobs._jobs))

    def test_expired_access_token_with_refresh_session_can_start_import(self):
        self.client.connected = False
        self.client.status = lambda: {
            "provider": "Questrade", "connected": False, "state": "expired",
            "expires_at": "2025-01-01T00:00:00+00:00", "can_refresh": True,
        }
        job = self.start()
        self.client.release.set()
        final = wait_finished(self.jobs, job["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(len(self.store.imported_datasets()), 1)

    def test_status_and_symbol_projection_drop_unexpected_secret_fields(self):
        self.client.leak_extra_fields = True
        self.assertNotIn(SECRET, json.dumps(self.jobs.status()))
        rows = self.jobs.search_symbols("TEST")
        self.assertEqual(rows, [{"id": 123, "symbol": "TEST", "description": "Test symbol", "currency": "USD"}])
        self.assertNotIn(SECRET, json.dumps(rows))

    def test_connection_and_search_failures_do_not_echo_credentials(self):
        self.client.failure = RuntimeError(SECRET)
        for operation in (
            lambda: self.jobs.connect({"refresh_token": SECRET}),
            lambda: self.jobs.search_symbols("TEST"),
        ):
            with self.assertRaises(ValueError) as caught:
                operation()
            self.assertNotIn(SECRET, str(caught.exception))
        for value in ({}, {"refresh_token": SECRET, "save": True}, {"refresh_token": ""},
                      {"refresh_token": "space token"}, {"refresh_token": 123}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.jobs.connect(value)


class ProviderHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / "http.sqlite3")
        self.store.initialize()
        self.client = FakeClient()
        self.server = LocalServer(("127.0.0.1", 0), self.store, questrade_client=self.client)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.client.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def request(self, method, route, payload=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        body = raw if raw is not None else json.dumps(payload) if payload is not None else None
        connection.request(method, route, body, {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_connect_status_search_disconnect_have_safe_documented_shapes(self):
        status, result = self.request("POST", "/api/providers/questrade/connect", {"refresh_token": SECRET})
        self.assertEqual(status, 200)
        self.assertTrue(result["connection"]["connected"])
        status, result = self.request("GET", "/api/providers/questrade/status")
        self.assertEqual(status, 200)
        self.assertIsNone(result["job"])
        self.assertNotIn(SECRET, json.dumps(result))
        status, result = self.request("GET", "/api/providers/questrade/symbols?prefix=TEST")
        self.assertEqual(status, 200)
        self.assertEqual(result["symbols"][0]["id"], 123)
        status, result = self.request("POST", "/api/providers/questrade/disconnect", {})
        self.assertEqual(status, 200)
        self.assertFalse(result["connection"]["connected"])

    def test_async_import_dedup_poll_and_cancel_through_http(self):
        status, response = self.request("POST", "/api/providers/questrade/import", dict(IMPORT))
        self.assertEqual(status, 202)
        job_id = response["job"]["id"]
        self.assertEqual(response["job"]["request_id"], IMPORT["request_id"])
        self.assertEqual(set(response["job"]), {"id", "request_id", "status"})
        self.assertTrue(self.client.entered.wait(timeout=2))
        status, response = self.request("POST", "/api/providers/questrade/import", dict(IMPORT))
        self.assertEqual(status, 200)
        self.assertEqual(response["job"]["id"], job_id)
        status, response = self.request("GET", "/api/providers/questrade/status")
        self.assertEqual(status, 200)
        self.assertEqual(response["job"]["status"], "running")
        status, response = self.request("POST", "/api/providers/questrade/cancel", {"job_id": job_id})
        self.assertEqual(status, 200)
        self.assertEqual(response["job"]["status"], "cancelled")
        self.assertEqual(response["job"]["request_id"], IMPORT["request_id"])
        self.client.release.set()
        self.server.provider_jobs._thread.join(timeout=2)
        self.assertEqual(self.store.imported_datasets(), [])

    def test_status_recovers_completed_request_identity_after_submission_response_is_lost(self):
        self.assertEqual(self.request("POST", "/api/providers/questrade/import", dict(IMPORT))[0], 202)
        # The caller retained its request ID but did not retain the response job.
        self.assertTrue(self.client.entered.wait(timeout=2))
        self.client.release.set()
        self.server.provider_jobs._thread.join(timeout=2)
        status, response = self.request("GET", "/api/providers/questrade/status")
        self.assertEqual(status, 200)
        recovered = response["job"]
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["request_id"], IMPORT["request_id"])
        self.assertEqual(set(recovered), {"id", "request_id", "status", "dataset"})
        self.assertNotIn(SECRET, json.dumps(recovered))
        status, response = self.request("POST", "/api/providers/questrade/import", dict(IMPORT))
        self.assertEqual(status, 200)
        self.assertEqual(response["job"], recovered)
        self.assertEqual(len(self.store.imported_datasets()), 1)

    def test_host_origin_body_and_duplicate_json_guards_apply_to_auth(self):
        route = "/api/providers/questrade/connect"
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}):
            self.assertEqual(self.request("POST", route, {"refresh_token": SECRET}, headers=headers)[0], 403)
        self.assertEqual(self.request("POST", route, {"refresh_token": "x" * 40000})[0], 400)
        self.assertEqual(self.request("POST", route, raw='{"refresh_token":"x","refresh_token":"y"}')[0], 400)
        self.assertEqual(self.client.calls, [])

    def test_provider_endpoint_payload_and_query_are_strict(self):
        for method, route, value in (
            ("POST", "/api/providers/questrade/connect", {"refresh_token": SECRET, "persist": True}),
            ("POST", "/api/providers/questrade/disconnect", {"refresh_token": SECRET}),
            ("POST", "/api/providers/questrade/cancel", {"job_id": "invalid"}),
            ("POST", "/api/providers/questrade/import", {**IMPORT, "path": "../file"}),
            ("GET", "/api/providers/questrade/symbols", None),
            ("GET", "/api/providers/questrade/symbols?prefix=TEST&prefix=OTHER", None),
            ("GET", "/api/providers/questrade/symbols?prefix=TEST&secret=x", None),
            ("GET", "/api/providers/questrade/symbols?prefix=https%3A%2F%2Fexample.com", None),
            ("GET", "/api/providers/questrade/status?refresh_token=secret", None),
        ):
            with self.subTest(route=route):
                self.assertEqual(self.request(method, route, value)[0], 400)
        self.assertEqual(self.client.calls, [])

    def test_auth_failure_and_provider_request_logging_never_include_secrets(self):
        self.client.failure = RuntimeError("bad refresh token " + SECRET)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("trading_service.http")
        original_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            status, result = self.request("POST", "/api/providers/questrade/connect", {"refresh_token": SECRET})
            self.assertEqual(status, 400)
            self.assertNotIn(SECRET, json.dumps(result))
            self.request("GET", "/api/providers/questrade/status?refresh_token=" + SECRET)
            self.request("GET", "/api/providers/questrade/" + SECRET)
            self.assertNotIn(SECRET, stream.getvalue())
        finally:
            logger.removeHandler(handler)
            logger.setLevel(original_level)

    def test_standard_csv_import_accepts_format_selector_without_changing_old_contract(self):
        payload = {
            "name": "Standard import", "symbol": "TEST", "source": "Offline fixture",
            "timezone": "America/New_York", "currency": "USD", "price_adjustment": "unknown",
            "csv": "timestamp,open,high,low,close,volume\n2025-01-02,100,102,99,101,1000\n",
        }
        self.assertEqual(self.request("POST", "/api/datasets/import", {**payload, "format": "standard"})[0], 201)
        self.assertEqual(self.request("POST", "/api/datasets/import", payload)[0], 200)
        self.assertEqual(self.request("POST", "/api/datasets/import", {**payload, "format": "unknown"})[0], 400)
        self.assertEqual(self.request("POST", "/api/datasets/import", [payload])[0], 400)

    def test_tradingview_format_routes_only_to_its_validator(self):
        from unittest.mock import patch
        metadata, content = dataset()
        with patch("service.tradingview.validate_tradingview_import", return_value=(metadata, content)) as validate:
            status, _ = self.request("POST", "/api/datasets/import", {"format": "tradingview", "fixture": True})
            self.assertEqual(status, 201)
            validate.assert_called_once_with({"fixture": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
