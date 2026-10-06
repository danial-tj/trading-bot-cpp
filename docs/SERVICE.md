# Durable local simulation service

The Python standard-library service runs the C++ engine as a bounded child process. SQLite stores request identity, immutable dataset snapshots, worker ownership, results, and the complete accounting event history. Each backtest owns an independent portfolio. The service has no broker connection, account credentials, uploads, or live execution.

## Start and use

Build the C++ executable using the root README, then run from the repository root:

```text
python -m service.server --engine build/bin/trading_bot.exe
```

On Linux use `build/bin/trading_bot`. A Visual Studio build may put the executable in `build/bin/Release/trading_bot.exe`. The service also searches common build locations if `--engine` is omitted. Open `http://127.0.0.1:8765`. Python 3.9 or newer is required; no pip packages are needed.

The default database is `.local/trading.sqlite3`. Use `--db PATH` for a separate durable history and `--port PORT` for a different local port. A second terminal can run `python -m service.worker --engine PATH --db PATH`; every worker uses the same transactional claim rules. `--no-worker` starts only the HTTP interface. Local clocks must agree because lease expiry uses Unix time.

Submission is asynchronous: a successful POST persists a queued run and returns before its background computation finishes. Poll its status/result routes or use the UI. Restarting the service preserves completed runs and resumes queued work. Work abandoned while `running` becomes eligible after its existing 90-second lease expires; it is not assumed dead merely because another service process starts. Recovery uses the exact stored dataset bytes and submitted configuration, and reruns from the beginning with the same engine/defaults. Failed and cancelled runs stay terminal; submit a new request ID to deliberately try a new run.

## API

Responses are JSON. Requests must use `Content-Type: application/json`. `Host` must be `127.0.0.1:PORT` or `localhost:PORT`; browser origins must match one of those local origins. The listener binds only to `127.0.0.1`.

| Route | Behavior |
| --- | --- |
| `GET /api/catalog` | Bundled dataset IDs, supported strategies, defaults, and workload limits |
| `POST /api/runs` | Submit `{request_id,dataset,strategy,config?}`; return `{run,created}` |
| `GET /api/runs` | Return `{runs}` with the 100 most recent submissions |
| `GET /api/runs/ID` | Return `{run}` including status, attempt, error, and dataset SHA-256 |
| `GET /api/runs/ID/result` | Return the complete engine document plus service provenance |
| `GET /api/runs/ID/chart?session=YYYY-MM-DD` | Return candles, causal VWAP/EMA overlays, and saved fills for one session of a completed run; session is optional |
| `GET /api/runs/ID/events` | Return `{events}` in committed order |
| `GET /api/runs/ID/reconcile` | Replay cents and whole quantities, compare stored ending balances |
| `POST /api/runs/ID/cancel` | Cancel queued/running work; repeat cancellation is safe |

Example request:

```json
{
  "request_id": "my-opening-study-001",
  "dataset": "opening_demo",
  "strategy": "VWAP_OPENING",
  "config": {
    "backtesting": {"initial_capital": 10000},
    "strategies": {"VWAP_OPENING": {"opening_window_minutes": 15}}
  }
}
```

`sample` selects `data/daily_demo.csv`; `opening_demo` selects `data/opening_demo.csv`. Both are synthetic and do not establish market performance. Users cannot supply filesystem paths through the API. Strategy names are `SMA_CROSSOVER`, `EMA_CROSSOVER`, `RSI`, and `VWAP_OPENING`.

Successful first submission returns HTTP 201. An identical retry returns 200 and the existing run, including after a service restart or lost response. Reusing `request_id` with a different normalized JSON payload returns 409. Object key ordering is normalized; configuration values remain part of request identity. An omitted config is normalized to `{}`. Use a new request ID for a deliberately new run.

Validation errors return 400, unknown runs/routes 404, unavailable results and terminal-state conflicts 409. Execution/configuration errors found by the engine mark the run `failed` and retain its error. No-trade results are successful runs.

## Saved-run chart

The chart route reads the immutable input snapshot and the completed result's `effective_config`; it never reads the current source CSV or updates accounting history. The response includes the run ID, dataset SHA-256, explicit synthetic-data flag, symbol, interval, available sessions, selected session, opening window, EMA periods, OHLCV bars, and matching saved fills/rejections. Intraday views default to the first BUY session, or the latest available session when no BUY exists. An optional session must be a valid available `YYYY-MM-DD` date within the saved run's active date range.

Session VWAP uses cumulative typical price `(high + low + close) / 3` weighted by volume and resets each session. The three EMA overlays use the saved VWAP strategy periods, an arithmetic-mean seed, and all prior regular-session closes, including warm-up history before the run's start date. Unready EMA values and VWAP without positive session volume are `null`. These overlays explain the saved data and do not rerun trading decisions. Daily datasets return their active date range with reference EMA overlays, `interval_minutes: null`, `session: null`, no session list, and `vwap: null`.

Responses contain at most 1,000 candles, with `total_bars` and `truncated` identifying a capped view. Indicator warm-up is calculated before this view is trimmed. Intraday timestamps and chart times remain exchange-local bar-start times. The terminal-style browser interface uses these returned candles and saved fills, with session selection and the opening window visible beside the strategy controls. Both bundled datasets remain synthetic research examples.

## Transaction and recovery boundary

1. Submission takes a SQLite `BEGIN IMMEDIATE` write transaction. A unique `request_id` and SHA-256 of the canonical request enforce persistent idempotency. The dataset bytes are stored once by SHA-256; later edits to source CSV files cannot change the submitted run. The snapshot is checked before execution.
2. A worker claims one queued job or an expired running job under `BEGIN IMMEDIATE`. Each claim receives a random ownership token, a monotonically increasing attempt number, and a lease. The normal lease is 90 seconds, while engine runtime is limited to 60 seconds. SQLite serializes claim writes; workers do not share a mutable in-memory ownership set.
3. The worker writes its stored dataset and configuration into a private temporary directory and invokes the engine with an argument array and `shell=False`. Results are parsed as strict JSON. Execution is deterministic for the same engine, config, and dataset; recovery restarts the full short backtest.
4. Before publication, an independent Python replay rebuilds integer cash, whole inventory, weighted cost basis, realized profit, fees, and ending mark-to-market balances. It checks every post-event balance and rejects inconsistent results.
5. A single write transaction checks that the worker still has the current unexpired token/attempt, inserts all events and the result, and moves the run to `completed`. Unique `(run_id,event_id)` keys protect run-scoped event identity. Database triggers reject updates/deletes of result, event, and dataset history.
6. If a process dies before commit, SQLite rolls back the entire publication. After the lease expires, another worker recomputes it. If a process dies after commit but before acknowledging it, the completed run and accounting history already exist; resubmission returns the same run and workers cannot claim it again.

This provides one committed accounting publication per run within the SQLite transaction/idempotency boundary. It does not claim universal exactly-once delivery, broker execution, or distributed-system reliability. The history is an auditable cash/inventory event log, not a double-entry ledger.

Cancellation and publication serialize on the database write lock. If cancellation commits first, ownership is revoked and subsequent publication fails. If publication commits first, cancellation returns a conflict. A cancelled worker may finish its bounded computation, but its output is discarded. An expired worker is rejected even when no replacement has claimed the job yet. Three abandoned attempts produce a failed run.

Events preserve simulation timestamps from the engine. API `created_at` and `updated_at` are UTC Unix seconds; they do not inherit the host display timezone. Effective configuration, engine version, commit/source fingerprint when supplied by the engine, and simulation assumptions stay in the immutable result. Keep the engine/config defaults unchanged while pending jobs recover; upgrading the executable between attempts can change an unpublished result. To compare an upgraded engine, use a separate database or wait for pending jobs to finish and submit new request IDs.

## Prove recovery

```text
python -m unittest discover -s tests -p test_service.py -v
python -m unittest discover -s tests -p test_chart_data.py -v
python scripts/recovery_demo.py --engine build/bin/trading_bot.exe
```

The service tests cover concurrent submissions and claims, changed-payload conflicts, stale owners, lease expiry, cancellation orderings, immutable history, partial-exit basis replay, intentional accounting corruption, bounded abandoned retries, HTTP validation, and actual child-process termination before and after transaction commit. When the executable is built, an additional integration test runs and reconciles a real C++ backtest. Set `TRADING_BOT_ENGINE` for a nonstandard build location. That integration test explicitly reports a skip if the executable is unavailable; CTest supplies the built executable.

The separately registered `chart_data` CTest suite covers session VWAP resets, zero-volume observations, saved EMA periods and prior-history seeding, causal projections, saved-fill selection, daily handling, bounded views, active-date selection, immutable snapshot use, and chart query validation.

The recovery demonstration uses the actual C++ engine and an actual Python worker subprocess. The worker deliberately calls `os._exit` immediately before commit (91) or immediately after commit (92), before returning success. The script reopens SQLite, repeats the identical submission, restarts the worker, checks one publication and unique fill/event identities, and replays the portfolio. For the precommit case only, it advances the dead worker's lease to expired in the test database rather than waiting 90 seconds. It asserts that the fixture actually creates fills. This is deterministic crash injection, not a power-failure or filesystem-corruption test.

## Limits and deferred scope

The service allows 16 concurrent HTTP connections, request bodies up to 32 KiB, preset CSV snapshots up to 8 MiB, engine result files up to 64 MiB, 50 queued/running jobs, and 1,000 total runs per database. Results and events are returned as complete documents. The HTTP connection timeout is 10 seconds, database busy timeout is 15 seconds, and engine timeout is 60 seconds. Capital is limited to 1 through 100,000,000 currency units by the API. The CLI supports a broader documented range. Numeric settings must be finite and satisfy the API bounds; the engine validates cross-field relationships.

SQLite WAL with `synchronous=FULL` is appropriate for this single-machine demo. This is not a public multiuser service: there is no authentication or tenant boundary, network filesystem support, automatic retention, migration framework, resumable event pagination, or production operations claim. The history is immutable through the application, not protected against a privileged database owner. Back up the database with SQLite's backup mechanism; do not copy only the main file while writes are active and assume the WAL is included.

Each C++ backtest executes one sequential portfolio and publishes its entire order outcomes and accounting history atomically. There are no shared-account concurrent order requests, durable pending buying-power reservations, partial fills, or incremental engine checkpoints. Those require a separate order lifecycle and are intentionally deferred. Short signals may be inspected as strategy evidence, but short execution, options, and live orders remain disabled.
