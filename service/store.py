"""SQLite job ownership and atomic, immutable result publication."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


class Conflict(ValueError):
    pass


class NotFound(ValueError):
    pass


class StaleClaim(Conflict):
    pass


MAX_ACTIVE_RUNS = 50
MAX_RUNS = 1000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def integer(value, name):
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def replay(result, events=None):
    """Independently rebuild cash, inventory, weighted basis and P&L in cents."""
    r = result["results"]
    if integer(r["initial_cash_cents"], "initial_cash_cents") <= 0:
        raise ValueError("initial cash must be positive")
    events = r["events"] if events is None else events
    cash = quantity = basis = realized = fees = last_price = 0
    ids = set()
    deposited = False
    for event in events:
        event_id = str(event["event_id"])
        if event_id in ids:
            raise ValueError("duplicate event identifier")
        ids.add(event_id)
        delta = integer(event["cash_delta_cents"], "cash_delta_cents")
        after_quantity = integer(event["quantity_after"], "quantity_after")
        fee = integer(event["fee_cents"], "fee_cents")
        price = integer(event["price_cents"], "price_cents")
        if fee < 0 or price < 0 or after_quantity < 0:
            raise ValueError("negative fee, price, or position")
        change = after_quantity - quantity
        if change and not deposited:
            raise ValueError("fill before initial deposit")
        if change > 0:
            if delta != -change * price - fee:
                raise ValueError("buy cash mismatch")
            basis += change * price + fee
        elif change < 0:
            sold = -change
            # Integer basis allocation uses nearest cent, half away from zero.
            released = basis if sold == quantity else (basis * sold * 2 + quantity) // (2 * quantity)
            if delta != sold * price - fee:
                raise ValueError("sell cash mismatch")
            basis -= released
            realized += delta - released
        elif delta:
            if deposited or cash or quantity or delta != r["initial_cash_cents"] or fee:
                raise ValueError("unexpected cash-only accounting event")
            deposited = True
        elif fee:
            raise ValueError("fee without a fill")
        cash += delta
        fees += fee
        quantity = after_quantity
        if price:
            last_price = price
        if cash < 0:
            raise ValueError("negative cash")
        for key, expected in (("cash_after_cents", cash), ("cost_basis_after_cents", basis),
                              ("realized_pnl_after_cents", realized)):
            if integer(event[key], key) != expected:
                raise ValueError(f"event {event_id}: {key} mismatch")
    if not deposited:
        raise ValueError("missing initial deposit event")
    expected = {"final_cash_cents": cash, "final_quantity": quantity,
                "cost_basis_cents": basis, "realized_pnl_cents": realized,
                "total_fees_cents": fees,
                "final_equity_cents": cash + quantity * last_price,
                "unrealized_pnl_cents": quantity * last_price - basis}
    for key, value in expected.items():
        actual = r.get(key, r.get("quantity") if key == "final_quantity" else None)
        if integer(actual, key) != value:
            raise ValueError(f"result {key} does not reconcile")
    return {"ok": True, "events": len(events), "cash_cents": cash,
            "quantity": quantity, "cost_basis_cents": basis,
            "realized_pnl_cents": realized, "fees_cents": fees}


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    sha256 TEXT PRIMARY KEY, content BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    dataset TEXT NOT NULL, dataset_sha256 TEXT NOT NULL REFERENCES datasets(sha256),
                    strategy TEXT NOT NULL, status TEXT NOT NULL
                        CHECK(status IN ('queued','running','completed','failed','cancelled')),
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    claim_token TEXT, lease_until REAL, error TEXT,
                    engine_seconds REAL
                );
                CREATE INDEX IF NOT EXISTS runnable ON runs(status,lease_until,created_at);
                CREATE TABLE IF NOT EXISTS results (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    document TEXT NOT NULL, result_sha256 TEXT NOT NULL,
                    committed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL REFERENCES runs(id), ordinal INTEGER NOT NULL,
                    event_id TEXT NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY(run_id,event_id), UNIQUE(run_id,ordinal)
                );
                CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT,'events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT,'events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_results_update BEFORE UPDATE ON results
                    BEGIN SELECT RAISE(ABORT,'results are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_results_delete BEFORE DELETE ON results
                    BEGIN SELECT RAISE(ABORT,'results are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_datasets_update BEFORE UPDATE ON datasets
                    BEGIN SELECT RAISE(ABORT,'dataset snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_datasets_delete BEFORE DELETE ON datasets
                    BEGIN SELECT RAISE(ABORT,'dataset snapshots are immutable'); END;
            """)

    @staticmethod
    def public(row):
        if row is None:
            raise NotFound("run not found")
        return {k: row[k] for k in ("id", "request_id", "dataset", "dataset_sha256", "strategy",
                                   "status", "created_at", "updated_at", "attempt", "error", "engine_seconds")}

    def submit(self, payload, dataset_bytes):
        request_id = payload["request_id"]
        body = canonical({k: v for k, v in payload.items() if k != "request_id"})
        digest = hashlib.sha256(body.encode()).hexdigest()
        dataset_digest = hashlib.sha256(dataset_bytes).hexdigest()
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM runs WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior["payload_hash"] != digest:
                    raise Conflict("request_id already belongs to a different request")
                db.commit()
                return self.public(prior), False
            active = db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            if active >= MAX_ACTIVE_RUNS or total >= MAX_RUNS:
                raise Conflict("local run capacity reached; finish pending jobs or start a separate database")
            run_id = uuid.uuid4().hex
            db.execute("INSERT OR IGNORE INTO datasets VALUES(?,?)", (dataset_digest, dataset_bytes))
            db.execute("""INSERT INTO runs
                (id,request_id,payload,payload_hash,dataset,dataset_sha256,strategy,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'queued',?,?)""",
                       (run_id, request_id, body, digest, payload["dataset"], dataset_digest,
                        payload["strategy"], now, now))
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            db.commit()
            return self.public(row), True

    def get(self, run_id):
        with self.connect() as db:
            return self.public(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def list(self):
        with self.connect() as db:
            return [self.public(r) for r in db.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 100")]

    def claim(self, lease_seconds=90, now=None, max_attempts=3):
        now = time.time() if now is None else now
        token = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE runs SET status='failed',claim_token=NULL,lease_until=NULL,
                updated_at=?,error='worker lease expired after maximum attempts'
                WHERE status='running' AND lease_until<=? AND attempt>=?""", (now, now, max_attempts))
            row = db.execute("""SELECT * FROM runs WHERE status='queued'
                OR (status='running' AND lease_until<=? AND attempt<?)
                ORDER BY created_at,id LIMIT 1""", (now, max_attempts)).fetchone()
            if row is None:
                db.commit()
                return None
            db.execute("""UPDATE runs SET status='running',claim_token=?,lease_until=?,
                attempt=attempt+1,updated_at=?,error=NULL WHERE id=?""", (token, now + lease_seconds, now, row["id"]))
            claim = dict(db.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone())
            claim["dataset_bytes"] = bytes(db.execute("SELECT content FROM datasets WHERE sha256=?", (row["dataset_sha256"],)).fetchone()[0])
            db.commit()
            return claim

    def cancel(self, run_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            self.public(row)
            if row["status"] in ("completed", "failed"):
                raise Conflict("run has already reached a terminal state")
            db.execute("""UPDATE runs SET status='cancelled',claim_token=NULL,lease_until=NULL,
                updated_at=? WHERE id=?""", (time.time(), run_id))
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            db.commit()
            return self.public(row)

    @staticmethod
    def assert_owner(db, claim):
        row = db.execute("SELECT * FROM runs WHERE id=?", (claim["id"],)).fetchone()
        if (row is None or row["status"] != "running" or row["claim_token"] != claim["claim_token"]
                or row["attempt"] != claim["attempt"] or row["lease_until"] <= time.time()):
            raise StaleClaim("worker no longer owns an unexpired claim")

    def publish(self, claim, result, seconds=0, crash_point=None):
        reconciliation = replay(result)
        result = dict(result)
        result["provenance"] = {"run_id": claim["id"], "dataset": claim["dataset"],
                                "dataset_sha256": claim["dataset_sha256"],
                                "request_sha256": claim["payload_hash"],
                                "reconciliation": reconciliation}
        document = canonical(result)
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.assert_owner(db, claim)
            for i, event in enumerate(result["results"]["events"]):
                db.execute("INSERT INTO events VALUES(?,?,?,?)", (claim["id"], i, str(event["event_id"]), canonical(event)))
            db.execute("INSERT INTO results VALUES(?,?,?,?)", (claim["id"], document,
                       hashlib.sha256(document.encode()).hexdigest(), now))
            db.execute("""UPDATE runs SET status='completed',claim_token=NULL,lease_until=NULL,
                updated_at=?,engine_seconds=? WHERE id=?""", (now, seconds, claim["id"]))
            if crash_point == "before_commit":
                os._exit(91)
            db.commit()
        if crash_point == "after_commit":
            os._exit(92)
        return result

    def fail(self, claim, error):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.assert_owner(db, claim)
            db.execute("""UPDATE runs SET status='failed',claim_token=NULL,lease_until=NULL,
                error=?,updated_at=? WHERE id=?""", (str(error)[:2000], time.time(), claim["id"]))
            db.commit()

    def result(self, run_id):
        self.get(run_id)
        with self.connect() as db:
            row = db.execute("SELECT document FROM results WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise Conflict("result is not available until the run completes")
            return json.loads(row[0])

    def dataset_snapshot(self, run_id):
        """Read the exact immutable input captured when this run was submitted."""
        with self.connect() as db:
            row = db.execute("""SELECT d.content, r.dataset_sha256 FROM runs r
                JOIN datasets d ON d.sha256=r.dataset_sha256 WHERE r.id=?""", (run_id,)).fetchone()
            if row is None:
                raise NotFound("run not found")
            content = bytes(row["content"])
            if hashlib.sha256(content).hexdigest() != row["dataset_sha256"]:
                raise ValueError("dataset snapshot fingerprint mismatch")
            return content

    def events(self, run_id):
        self.get(run_id)
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT document FROM events WHERE run_id=? ORDER BY ordinal", (run_id,))]

    def reconcile(self, run_id):
        return replay(self.result(run_id), self.events(run_id))
