"""Saved-run charts are causal, bounded projections of immutable input data."""
import copy
from datetime import date, timedelta
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.chart_data import build_chart, chart_for_run
from service.server import LocalServer
from service.store import Store, Conflict, NotFound, canonical


def csv_bytes(rows):
    return ("timestamp,open,high,low,close,volume\n" + "\n".join(
        f"{timestamp},{close-0.5},{close+1},{close-2},{close},{volume}"
        for timestamp, close, volume in rows) + "\n").encode()


ROWS = [("2025-01-02T09:28:00", 500, 100),  # Outside regular session: no indicator contribution.
        ("2025-01-02T09:30:00", 10, 10), ("2025-01-02T09:32:00", 12, 30),
        ("2025-01-02T09:34:00", 14, 20), ("2025-01-02T09:36:00", 16, 20),
        ("2025-01-03T09:30:00", 20, 0), ("2025-01-03T09:32:00", 22, 30),
        ("2025-01-03T09:34:00", 24, 20)]


def report_fixture():
    return {"schema_version": 1, "effective_config": {
        "backtesting": {"symbol": "TEST", "start_date": "", "end_date": ""},
        "strategies": {"VWAP_OPENING": {"fast_ema": 2, "medium_ema": 3, "slow_ema": 4,
                                      "bar_minutes": 2, "opening_window_minutes": 15}}},
        "results": {"initial_cash_cents": 1000000, "final_cash_cents": 1000000,
                    "final_equity_cents": 1000000, "final_quantity": 0, "cost_basis_cents": 0,
                    "realized_pnl_cents": 0, "unrealized_pnl_cents": 0, "total_fees_cents": 0,
                    "trades": [], "rejections": [], "events": [{"event_id": "deposit",
                        "timestamp": "2025-01-02T09:30:00", "type": "DEPOSIT", "quantity": 0,
                        "price_cents": 0, "fee_cents": 0, "cash_delta_cents": 1000000,
                        "cash_after_cents": 1000000, "quantity_after": 0,
                        "cost_basis_after_cents": 0, "realized_pnl_after_cents": 0}]}}


RUN = {"id": "1" * 32, "dataset": "opening_demo", "strategy": "VWAP_OPENING",
       "dataset_sha256": "test-fingerprint"}


def imported_info(kind="intraday"):
    return {"origin": "imported", "name": "User-provided sample", "symbol": "ACME",
            "source": "User CSV export; provider not independently verified",
            "timezone": "America/Toronto", "currency": "CAD", "price_adjustment": "unadjusted",
            "interval_kind": kind, "kind": kind, "bar_minutes": 5 if kind == "intraday" else 0,
            "session_open_minute": 480, "session_close_minute": 500}


IMPORTED_ROWS = [("2025-01-02T08:00:00", 10, 10), ("2025-01-02T08:05:00", 12, 30),
                 ("2025-01-02T08:10:00", 14, 20), ("2025-01-02T08:15:00", 16, 20),
                 ("2025-01-03T08:00:00", 20, 10), ("2025-01-03T08:05:00", 22, 30)]


class ChartProjectionTests(unittest.TestCase):
    def test_vwap_is_volume_weighted_and_resets_each_session(self):
        report = report_fixture()
        first = build_chart(RUN, report, csv_bytes(ROWS), "2025-01-02")
        self.assertEqual(len(first["bars"]), 4)
        self.assertAlmostEqual(first["bars"][0]["vwap"], 10 - 1 / 3)
        self.assertAlmostEqual(first["bars"][1]["vwap"], ((10 - 1 / 3) * 10 + (12 - 1 / 3) * 30) / 40)
        second = build_chart(RUN, report, csv_bytes(ROWS), "2025-01-03")
        self.assertIsNone(second["bars"][0]["vwap"])
        self.assertAlmostEqual(second["bars"][1]["vwap"], 22 - 1 / 3)

    def test_ema_uses_saved_periods_sma_seed_and_prior_regular_history(self):
        report = report_fixture()
        report["effective_config"]["backtesting"]["start_date"] = "2025-01-03"
        chart = build_chart(RUN, report, csv_bytes(ROWS))
        self.assertEqual(chart["sessions"], ["2025-01-03"])
        self.assertEqual(chart["periods"], {"fast": 2, "medium": 3, "slow": 4})
        self.assertEqual(chart["opening_window_minutes"], 15)
        self.assertEqual(chart["symbol"], "TEST")
        self.assertAlmostEqual(chart["bars"][0]["ema_slow"], 13 + 2 / 5 * (20 - 13))
        self.assertAlmostEqual(chart["bars"][0]["ema_medium"], 14 + 1 / 2 * (20 - 14))
        first = build_chart(RUN, report_fixture(), csv_bytes(ROWS), "2025-01-02")
        self.assertIsNone(first["bars"][0]["ema_fast"])
        self.assertEqual(first["bars"][1]["ema_fast"], 11)
        self.assertEqual(first["bars"][3]["ema_slow"], 13)

    def test_future_changes_do_not_change_earlier_overlays(self):
        before = build_chart(RUN, report_fixture(), csv_bytes(ROWS), "2025-01-02")
        changed = ROWS[:-1] + [(ROWS[-1][0], 900, 100000)]
        after = build_chart(RUN, report_fixture(), csv_bytes(changed), "2025-01-02")
        self.assertEqual(before["bars"], after["bars"])
        one = build_chart(RUN, report_fixture(), csv_bytes(ROWS), "2025-01-03")
        two = build_chart(RUN, report_fixture(), csv_bytes(changed), "2025-01-03")
        self.assertEqual(one["bars"][:-1], two["bars"][:-1])

    def test_default_first_buy_session_and_saved_fill_filter(self):
        report = report_fixture()
        buy = {"timestamp": "2025-01-02T09:32:00", "action": "BUY", "price": 11.5, "quantity": 2}
        sell = {"timestamp": "2025-01-03T09:32:00", "action": "SELL", "price": 21.5, "quantity": 2}
        rejected = {"timestamp": "2025-01-02T09:34:00", "action": "SHORT", "reason": "disabled"}
        report["results"].update(trades=[buy, sell], rejections=[rejected])
        chart = build_chart(RUN, report, csv_bytes(ROWS))
        self.assertEqual(chart["session"], "2025-01-02")
        self.assertEqual(chart["trades"], [buy])
        self.assertEqual(chart["rejections"], [rejected])
        other = build_chart(RUN, report, csv_bytes(ROWS), "2025-01-03")
        self.assertEqual(other["trades"], [sell])
        self.assertEqual(other["rejections"], [])
        report["results"]["trades"] = []
        self.assertEqual(build_chart(RUN, report, csv_bytes(ROWS))["session"], "2025-01-03")

    def test_daily_overlays_are_not_presented_as_session_vwap(self):
        rows = [(str(date(2024, 1, 1) + timedelta(days=i)), 100 + i, 1000) for i in range(360)]
        report = report_fixture()
        report["effective_config"]["backtesting"].update(start_date="2024-01-04", end_date="2024-01-06")
        chart = build_chart(RUN, report, csv_bytes(rows))
        self.assertIsNone(chart["interval_minutes"])
        self.assertIsNone(chart["session"])
        self.assertEqual(chart["sessions"], [])
        self.assertEqual(len(chart["bars"]), 3)
        self.assertEqual(chart["bars"][0]["ema_slow"], 101.5)
        self.assertTrue(all(bar["vwap"] is None for bar in chart["bars"]))
        full = build_chart(RUN, report_fixture(), csv_bytes(rows))
        self.assertEqual(len(full["bars"]), 360)
        with self.assertRaisesRegex(ValueError, "daily charts"):
            build_chart(RUN, report, csv_bytes(rows), "2024-01-04")

    def test_daily_cap_keeps_warmed_indicators_and_marks_truncation(self):
        rows = [(str(date(2020, 1, 1) + timedelta(days=i)), 100 + i, 1000) for i in range(1100)]
        chart = build_chart(RUN, report_fixture(), csv_bytes(rows))
        self.assertEqual(len(chart["bars"]), 1000)
        self.assertTrue(chart["truncated"])
        self.assertEqual(chart["total_bars"], 1100)
        self.assertEqual(chart["bars"][0]["timestamp"], rows[100][0])
        self.assertIsNotNone(chart["bars"][0]["ema_slow"])

    def test_bad_or_out_of_range_sessions_rejected(self):
        for session in ("", "2025-02-30", "2025-01-04", "../data", "2025-1-2"):
            with self.subTest(session=session), self.assertRaises(ValueError):
                build_chart(RUN, report_fixture(), csv_bytes(ROWS), session)
        report = report_fixture()
        report["effective_config"]["backtesting"]["end_date"] = "2025-01-02"
        with self.assertRaisesRegex(ValueError, "not available"):
            build_chart(RUN, report, csv_bytes(ROWS), "2025-01-03")

    def test_interval_configuration_is_validated_against_saved_data(self):
        report = report_fixture()
        report["effective_config"]["strategies"]["VWAP_OPENING"]["bar_minutes"] = 5
        with self.assertRaisesRegex(ValueError, "interval"):
            build_chart(RUN, report, csv_bytes(ROWS))
        del report["effective_config"]["strategies"]["VWAP_OPENING"]["bar_minutes"]
        self.assertEqual(build_chart(RUN, report, csv_bytes(ROWS))["interval_minutes"], 2)


class ImportedChartTests(unittest.TestCase):
    def test_non_vwap_retains_extended_hours_candles_fills_and_ema_history(self):
        metadata = imported_info()
        rows = [("2025-01-02T07:55:00", 100, 10000), *IMPORTED_ROWS[:4],
                ("2025-01-02T08:20:00", 200, 20000)]
        report = report_fixture()
        fills = [{"timestamp": rows[0][0], "action": "BUY", "price": 99.5, "quantity": 2},
                 {"timestamp": rows[-1][0], "action": "SELL", "price": 199.5, "quantity": 2}]
        report["results"]["trades"] = fills
        report["results"]["rejections"] = [{"timestamp": rows[0][0], "action": "BUY", "reason": "test rejection"}]
        for strategy in ("SMA_CROSSOVER", "EMA_CROSSOVER", "RSI"):
            run = {**RUN, "dataset": "import_extended", "strategy": strategy, "dataset_info": metadata}
            with self.subTest(strategy=strategy):
                chart = build_chart(run, report, csv_bytes(rows))
                self.assertFalse(chart["regular_session_only"])
                self.assertEqual(chart["outside_session_bar_count"], 2)
                self.assertEqual(chart["indicator_scope"], {"ema": "all_observed_bars", "vwap": "regular_session_only"})
                self.assertEqual([bar["timestamp"] for bar in chart["bars"]], [row[0] for row in rows])
                self.assertEqual(chart["trades"], fills)
                self.assertEqual(chart["rejections"], report["results"]["rejections"])
                self.assertEqual(chart["bars"][1]["ema_fast"], 55)
                self.assertEqual(chart["bars"][3]["ema_slow"], 34)
                self.assertAlmostEqual(chart["bars"][-1]["ema_slow"], 96.08)
                self.assertIsNone(chart["bars"][0]["vwap"])
                self.assertIsNone(chart["bars"][-1]["vwap"])
                self.assertAlmostEqual(chart["bars"][1]["vwap"], 10 - 1 / 3)
                self.assertAlmostEqual(chart["bars"][2]["vwap"], ((10 - 1 / 3) * 10 + (12 - 1 / 3) * 30) / 40)

    def test_active_vwap_excludes_extended_hours_from_candles_and_ema(self):
        metadata = imported_info()
        rows = [("2025-01-02T07:55:00", 100, 10000), *IMPORTED_ROWS[:4],
                ("2025-01-02T08:20:00", 200, 20000)]
        report = report_fixture()
        report["effective_config"]["strategies"]["VWAP_OPENING"].update(
            {key: metadata[key] for key in ("bar_minutes", "session_open_minute", "session_close_minute")})
        run = {**RUN, "dataset": "import_extended", "dataset_info": metadata}
        chart = build_chart(run, report, csv_bytes(rows))
        self.assertTrue(chart["regular_session_only"])
        self.assertEqual(chart["outside_session_bar_count"], 0)
        self.assertEqual(chart["indicator_scope"]["ema"], "regular_session_bars")
        self.assertEqual([bar["timestamp"] for bar in chart["bars"]], [row[0] for row in IMPORTED_ROWS[:4]])
        self.assertIsNone(chart["bars"][0]["ema_fast"])
        self.assertEqual(chart["bars"][1]["ema_fast"], 11)
        self.assertEqual(chart["bars"][-1]["ema_slow"], 13)
        self.assertAlmostEqual(chart["bars"][0]["vwap"], 10 - 1 / 3)

    def test_extended_only_date_remains_selectable_for_non_vwap(self):
        metadata = imported_info()
        rows = [*IMPORTED_ROWS, ("2025-01-04T07:55:00", 30, 1000), ("2025-01-04T08:20:00", 40, 1000)]
        run = {**RUN, "dataset": "import_extended", "strategy": "EMA_CROSSOVER", "dataset_info": metadata}
        chart = build_chart(run, report_fixture(), csv_bytes(rows), "2025-01-04")
        self.assertEqual(len(chart["bars"]), 2)
        self.assertEqual(chart["outside_session_bar_count"], 2)
        self.assertIn("2025-01-04", chart["sessions"])
        self.assertTrue(all(bar["vwap"] is None for bar in chart["bars"]))
        self.assertIsNotNone(chart["bars"][0]["ema_slow"])

    def test_imported_crossover_uses_five_minute_metadata_not_unused_vwap_defaults(self):
        metadata = imported_info()
        run = {**RUN, "dataset": "import_fixture", "strategy": "EMA_CROSSOVER", "dataset_info": metadata}
        report = report_fixture()
        report["effective_config"]["strategies"]["VWAP_OPENING"].update(
            bar_minutes=2, session_open_minute=570, session_close_minute=960)
        chart = build_chart(run, report, csv_bytes(IMPORTED_ROWS))
        self.assertEqual(chart["interval_minutes"], 5)
        self.assertEqual(chart["session_open_minute"], 480)
        self.assertEqual(chart["session_close_minute"], 500)
        self.assertEqual(chart["session"], "2025-01-03")
        self.assertEqual(chart["sessions"], ["2025-01-02", "2025-01-03"])
        self.assertEqual(len(chart["bars"]), 2)
        self.assertAlmostEqual(chart["bars"][0]["ema_slow"], 13 + 2 / 5 * (20 - 13))
        self.assertAlmostEqual(chart["bars"][0]["vwap"], 20 - 1 / 3)
        self.assertEqual(chart["origin"], "imported")
        self.assertFalse(chart["synthetic"])
        self.assertEqual(chart["dataset_info"], metadata)
        self.assertEqual(chart["symbol"], "ACME")
        self.assertEqual(chart["currency"], "CAD")
        self.assertEqual(chart["timezone"], "America/Toronto")
        self.assertNotIn("verified", chart)
        changed = IMPORTED_ROWS[:-1] + [(IMPORTED_ROWS[-1][0], 999, 10000)]
        self.assertEqual(chart["bars"][:-1], build_chart(run, report, csv_bytes(changed))["bars"][:-1])

    def test_active_imported_vwap_requires_saved_cadence_and_session_agreement(self):
        metadata = imported_info()
        run = {**RUN, "dataset": "import_fixture", "strategy": "VWAP_OPENING", "dataset_info": metadata}
        report = report_fixture()
        parameters = report["effective_config"]["strategies"]["VWAP_OPENING"]
        parameters.update({key: metadata[key] for key in ("bar_minutes", "session_open_minute", "session_close_minute")})
        self.assertEqual(build_chart(run, report, csv_bytes(IMPORTED_ROWS))["interval_minutes"], 5)
        for key, invalid in (("bar_minutes", 2), ("session_open_minute", 475), ("session_close_minute", 505)):
            changed = copy.deepcopy(report)
            changed["effective_config"]["strategies"]["VWAP_OPENING"][key] = invalid
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, f"saved VWAP {key}"):
                build_chart(run, changed, csv_bytes(IMPORTED_ROWS))

    def test_imported_daily_retains_source_metadata_and_has_no_session_vwap(self):
        metadata = imported_info("daily")
        run = {**RUN, "dataset": "import_daily", "strategy": "RSI", "dataset_info": metadata}
        rows = [(f"2025-01-0{day}", 100 + day, 1000) for day in range(1, 6)]
        chart = build_chart(run, report_fixture(), csv_bytes(rows))
        self.assertEqual(chart["dataset_info"], metadata)
        self.assertEqual(chart["origin"], "imported")
        self.assertFalse(chart["synthetic"])
        self.assertIsNone(chart["interval_minutes"])
        self.assertIsNone(chart["session"])
        self.assertEqual(chart["sessions"], [])
        self.assertEqual(len(chart["bars"]), 5)
        self.assertTrue(all(bar["vwap"] is None for bar in chart["bars"]))
        self.assertEqual(chart["bars"][3]["ema_slow"], 102.5)
        with self.assertRaisesRegex(ValueError, "requires an intraday"):
            build_chart({**run, "strategy": "VWAP_OPENING"}, report_fixture(), csv_bytes(rows))

    def test_saved_metadata_must_match_timestamp_kind_and_grid(self):
        run = {**RUN, "dataset": "import_fixture", "strategy": "EMA_CROSSOVER", "dataset_info": imported_info()}
        for changed, message in (({"interval_kind": "daily", "kind": "daily"}, "kind"),
                                 ({"bar_minutes": 3}, "interval"),
                                 ({"kind": "daily"}, "kinds disagree")):
            invalid = {**run, "dataset_info": {**run["dataset_info"], **changed}}
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, message):
                build_chart(invalid, report_fixture(), csv_bytes(IMPORTED_ROWS))

    def test_saved_report_metadata_fallback_and_legacy_origin(self):
        report = report_fixture()
        report["provenance"] = {"dataset_info": imported_info()}
        run = {**RUN, "dataset": "import_fixture", "strategy": "EMA_CROSSOVER"}
        chart = build_chart(run, report, csv_bytes(IMPORTED_ROWS))
        self.assertEqual(chart["origin"], "imported")
        self.assertEqual(chart["symbol"], "ACME")
        legacy = build_chart(RUN, report_fixture(), csv_bytes(ROWS))
        self.assertTrue(legacy["synthetic"])
        self.assertEqual(legacy["origin"], "synthetic")
        self.assertEqual(legacy["symbol"], "TEST")
        unknown = build_chart({**RUN, "dataset": "missing_info"}, report_fixture(), csv_bytes(ROWS))
        self.assertFalse(unknown["synthetic"])
        self.assertEqual(unknown["origin"], "unknown")


class SavedChartTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / "state.sqlite3")
        self.store.initialize()
        self.payload = {"request_id": "chart", "dataset": "opening_demo", "strategy": "VWAP_OPENING", "config": {}}
        self.content = csv_bytes(ROWS)
        self.run, _ = self.store.submit(self.payload, self.content)

    def tearDown(self):
        self.temporary.cleanup()

    def complete(self):
        self.store.publish(self.store.claim(), report_fixture())

    def test_chart_requires_completed_result(self):
        with self.assertRaises(Conflict):
            chart_for_run(self.store, self.run["id"])
        with self.assertRaises(NotFound):
            self.store.dataset_snapshot("f" * 32)

    def test_chart_reads_immutable_snapshot_without_changing_saved_run(self):
        self.complete()
        before = canonical(self.store.result(self.run["id"]))
        before_run = self.store.get(self.run["id"])
        repeated, created = self.store.submit(self.payload, b"replaced mutable source")
        self.assertFalse(created)
        self.assertEqual(repeated["id"], self.run["id"])
        self.assertEqual(self.store.dataset_snapshot(self.run["id"]), self.content)
        chart = chart_for_run(self.store, self.run["id"])
        self.assertEqual(chart["dataset_sha256"], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(chart["bars"][0]["close"], 20)
        self.assertEqual(before, canonical(self.store.result(self.run["id"])))
        self.assertEqual(before_run, self.store.get(self.run["id"]))
        self.assertTrue(self.store.reconcile(self.run["id"])["ok"])

    def test_imported_chart_uses_metadata_captured_with_the_completed_run(self):
        self.complete()
        metadata = imported_info()
        content = csv_bytes(IMPORTED_ROWS)
        dataset, created = self.store.import_dataset(metadata, content)
        self.assertTrue(created)
        payload = {"request_id": "imported-chart", "dataset": dataset["id"],
                   "strategy": "EMA_CROSSOVER", "config": {}}
        run, _ = self.store.submit(payload, content, dataset_info=dataset)
        self.store.publish(self.store.claim(), report_fixture())
        saved_info = copy.deepcopy(self.store.get(run["id"])["dataset_info"])
        dataset["symbol"] = "CHANGED"
        metadata["bar_minutes"] = 2
        chart = chart_for_run(self.store, run["id"])
        self.assertEqual(chart["dataset_info"], saved_info)
        self.assertEqual(chart["symbol"], "ACME")
        self.assertEqual(chart["interval_minutes"], 5)
        self.assertEqual(chart["dataset_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(self.store.dataset_snapshot(run["id"]), content)
        self.assertTrue(self.store.reconcile(run["id"])["ok"])

    def test_endpoint_handles_selection_and_bad_queries(self):
        self.complete()
        server = LocalServer(("127.0.0.1", 0), self.store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def get(query):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            connection.request("GET", f"/api/runs/{self.run['id']}/chart{query}")
            response = connection.getresponse()
            status, body = response.status, json.loads(response.read())
            connection.close()
            return status, body

        try:
            status, chart = get("?session=2025-01-02")
            self.assertEqual(status, 200)
            self.assertEqual(chart["session"], "2025-01-02")
            for query in ("?session=", "?session=2025-01-02&session=2025-01-03", "?session=2025-01-04", "?file=secret"):
                with self.subTest(query=query):
                    self.assertEqual(get(query)[0], 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main(verbosity=2)
