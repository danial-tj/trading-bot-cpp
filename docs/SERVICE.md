# Durable local simulation service

The Python service runs the C++ engine as a bounded child process. SQLite stores request identity, immutable dataset snapshots and import metadata, worker ownership, results, and the complete accounting event history. Each backtest owns an independent portfolio. Historical data can come from local CSV imports or an explicit read-only Questrade connection. There is no live feed, account/position synchronization or broker execution.

## Start and use

Build the C++ executable using the root README, then run from the repository root:

```text
python -m service.server --engine build/bin/trading_bot.exe
```

On Linux use `build/bin/trading_bot`. A Visual Studio build may put the executable in `build/bin/Release/trading_bot.exe`. The service also searches common build locations if `--engine` is omitted. Open `http://127.0.0.1:8765`. Python 3.9 or newer is required. Install `requirements.txt` for pinned IANA timezone data, particularly on Windows; CSV/provider timestamp conversion needs that data.

The default database is `.local/trading.sqlite3`. Use `--db PATH` for a separate durable history and `--port PORT` for a different local port. A second terminal can run `python -m service.worker --engine PATH --db PATH`; every worker uses the same transactional claim rules. `--no-worker` starts only the HTTP interface. Local clocks must agree because lease expiry uses Unix time.

Submission is asynchronous: a successful POST persists a queued run and returns before its background computation finishes. Poll its status/result routes or use the UI. Restarting the service preserves completed runs and resumes queued work. Work abandoned while `running` becomes eligible after its existing 90-second lease expires; it is not assumed dead merely because another service process starts. Recovery uses the exact stored dataset bytes and submitted configuration, and reruns from the beginning with the same engine/defaults. Failed and cancelled runs stay terminal; submit a new request ID to deliberately try a new run.

## API

Responses are JSON. Requests must use `Content-Type: application/json`. `Host` must be `127.0.0.1:PORT` or `localhost:PORT`; browser origins must match one of those local origins. The listener binds only to `127.0.0.1`.

| Route | Behavior |
| --- | --- |
| `GET /api/catalog` | Bundled and imported dataset metadata/IDs, supported strategies, defaults, and workload limits |
| `POST /api/datasets/import` | Validate CSV text plus metadata; return `{dataset,created}` with an immutable local dataset ID |
| `POST /api/runs` | Submit `{request_id,dataset,strategy,config?}`; return `{run,created}` |
| `GET /api/runs` | Return `{runs}` with the 100 most recent submissions |
| `GET /api/runs/ID` | Return `{run}` including status, attempt, error, and dataset SHA-256 |
| `GET /api/runs/ID/result` | Return the complete engine document plus service provenance |
| `GET /api/runs/ID/chart?session=YYYY-MM-DD` | Return candles, causal VWAP/EMA overlays, and saved fills for one session of a completed run; session is optional |
| `GET /api/runs/ID/events` | Return `{events}` in committed order |
| `GET /api/runs/ID/reconcile` | Replay cents and whole quantities, compare stored ending balances |
| `POST /api/runs/ID/cancel` | Cancel queued/running work; repeat cancellation is safe |
| `GET /api/providers/questrade/status` | Return `{connection,job}` with connection state, history progress and latest job |
| `POST /api/providers/questrade/connect` | Accept `{refresh_token}` and return `{connection}` without exposing credentials |
| `POST /api/providers/questrade/disconnect` | Accept `{}`; clear credentials and cancel active historical work |
| `GET /api/providers/questrade/symbols?prefix=TEXT` | Return `{symbols}` from the connected provider's symbol search |
| `POST /api/providers/questrade/import` | Accept `{request_id,symbol_id,start_date,end_date,bar_minutes}`; return `{job}` |
| `POST /api/providers/questrade/cancel` | Accept `{job_id}` and return `{job}` |

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

`sample` selects `data/daily_demo.csv`; `opening_demo` selects `data/opening_demo.csv`. Both are synthetic and do not establish market performance. An imported dataset is selected using the `import_...` ID returned by the import route or catalog. Users cannot supply filesystem paths through the API. Strategy names are `SMA_CROSSOVER`, `EMA_CROSSOVER`, `RSI`, and `VWAP_OPENING`.

Successful first submission returns HTTP 201. An identical retry returns 200 and the existing run, including after a service restart or lost response. Reusing `request_id` with a different normalized JSON payload returns 409. Object key ordering is normalized; configuration values remain part of request identity. An omitted config is normalized to `{}`. Use a new request ID for a deliberately new run.

Validation errors return 400, unknown runs/routes 404, unavailable results and terminal-state conflicts 409. Execution/configuration errors found by the engine mark the run `failed` and retain its error. No-trade results are successful runs.

## Local CSV imports

In the browser's **Data** tab, choose a UTF-8 CSV, enter its metadata, and use **Validate & import**. Review the resulting data-quality warnings and select the dataset to return to the workbench. This imports bytes into the local SQLite database; it does not connect to the stated provider. `origin: "imported"` records user-supplied data, not independently verified source, licensing or market authenticity.

The import JSON contains required `name`, `symbol`, `source`, `timezone`, `currency`, `price_adjustment` and `csv` strings. Optional `format` is `standard` (default) or `tradingview`. It also accepts integer `bar_minutes` (0 for daily, 1–30 for intraday), `session_open_minute` and `session_close_minute` (minutes after midnight; defaults 570/960). Session close must follow open on the same date, and the intraday interval must divide that duration. `price_adjustment` is `unknown`, `unadjusted`, `split_adjusted` or `split_and_dividend_adjusted`. Symbols and three-letter currency labels are normalized to uppercase. Standard CSV treats timezone as a user-declared UTC or IANA-style label, without conversion. See [the standard CSV contract](../data/README.md) for row validation.

`POST /api/datasets/import` returns HTTP 201 for a new import or 200 for an identical retry. Its `dataset` includes `id`, `origin`, name/source/symbol/currency/timezone/adjustment declarations, interval/session settings, `kind`/`interval_kind`, row count, first/last timestamps, warnings, `original_sha256`, `dataset_sha256` and `created_at`. The import ID derives from metadata and canonical CSV bytes. Original text formatting changes can produce a distinct import because the original fingerprint is part of provenance, even if canonical bytes match. The registry and canonical snapshots are immutable; there is no update/delete API. Every run captures its `dataset_info`, also included in result provenance, so later selection cannot relabel an earlier run.

`original_sha256` fingerprints the UTF-8 CSV text submitted to the route. `dataset_sha256` fingerprints the normalized CSV actually given to the engine. Header aliases, timestamp spellings, numeric formatting and line endings are normalized without filling gaps or adjusting prices. Raw and canonical CSV are each limited to 8 MiB and at most 100,000 data rows; the enclosing import JSON has a separate 12 MiB limit. A database allows 50 imports, while run submissions retain their 32 KiB JSON limit. Imports do not initiate a backtest automatically through the API.

Imported runs use the declared symbol. For opening VWAP, the service supplies the imported interval/session settings and rejects conflicting overrides; daily imports cannot select opening VWAP. The currency is a display/accounting label for a single nominal currency, not an FX conversion. The service does not infer exchange holidays, early closes, missing whole sessions or corporate actions. Standard CSV must already use the intended local clock; the adapters below perform only their documented timestamp normalization. Extended-hours and zero-volume observations remain in the snapshot with warnings; incomplete observed regular sessions can prevent VWAP trend warm-up.

### TradingView exports

Select TradingView CSV in the Data import form or send `format: "tradingview"`. The adapter accepts one time/timestamp/date/datetime column and exact case-insensitive open/high/low/close/Volume headers in any order. Extra indicator columns are discarded as literal text and named in `ignored_columns` and warnings; their values are not trading inputs. Duplicate headers, ambiguous time/volume columns and ragged rows are rejected. Header bounds are 256 columns, 128 printable characters per name, and 8,000 UTF-8 bytes across ignored names.

Unix seconds and offset-aware ISO timestamps convert into the declared IANA exchange timezone using `zoneinfo`. Naive ISO timestamps are interpreted as already local, with a warning; ambiguous/nonexistent DST wall times fail. Plain daily `YYYY-MM-DD` values retain their date without a UTC-midnight shift. Mixed timestamp modes, epoch milliseconds, non-minute bar starts and duplicate/non-increasing normalized local times fail. Missing timezone data returns an actionable installation error instead of a guessed offset.

The existing strict importer validates the adapted OHLCV values, session grid and bounds. Metadata adds `import_format: "tradingview"`, `timestamp_format` and ignored-column notes. `original_sha256` still fingerprints the original TradingView CSV text, while the registry fingerprints the final canonical engine input separately. TradingView supports [manual chart-data export](https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/); it does [not provide a customer data/indicator API](https://www.tradingview.com/support/solutions/43000474413-i-need-access-to-your-api-in-order-to-get-data-or-indicator-values/). No TradingView account session, scraping or subscription API is used here.

### Questrade historical downloads

Connect explicitly in the Data workspace with a manually obtained API refresh token, search and choose the provider's symbol, then request a historical date range. The access token and rotated refresh token remain inside the local provider client's process memory; they are never returned in status, stored in SQLite or written into dataset provenance. Disconnect fences off in-flight operations and clears credentials. A server restart requires a new manual refresh-token connection. This workflow uses historical prices only, without reading account balances, synchronizing holdings or placing orders.

History requests support daily (`0`) or 1/2/5/10/15/30-minute bars, at most 366 inclusive calendar days, ending no later than the current New York date. One job may be active. Status exposes a credential-free connection state and chunk/row progress. Job states are `queued`, `running`, `completed`, `failed` or `cancelled`; completed jobs contain the saved dataset. A newly accepted request returns 202, while an identical retained request ID returns its existing job with 200. Different parameters under that ID conflict. Job/idempotency memory is limited to the latest 20 jobs in this process and does not survive restart; committed datasets do.

Every public job includes `id`, the original `request_id`, and `status`, plus `dataset` on completion or a redacted `error` on failure. The status endpoint returns the latest job or `null`. Clients can match that job's `request_id` to an uncertain submission after a lost response; a missing or different job does not prove that submission failed. Retrying the original request ID recovers its retained job without a second download.

Only a fully downloaded, validated set of completed candles is published. Partial failure and successful cancellation publish no dataset. Cancellation and publication serialize so cancellation cannot falsely report success after a dataset is committed. Generated CSV remains subject to 8 MiB/100,000-row limits and the shared 50-import registry limit. Source metadata identifies `provider: "Questrade"`, provider symbol ID, listing exchange and requested dates; prices are normalized to `America/New_York`, and adjustment status is recorded as unknown. Entitlements, available history and provider responses may limit requests. Tests use controlled transport fixtures; no actual credentialed Questrade session or user market-data entitlement has been verified in this working session.

## Evaluation range and VWAP diagnostics

`config.backtesting.start_date` and `end_date` select inclusive local evaluation dates. Earlier input remains available for indicator warm-up. No observations in the requested range is an error. Completed VWAP results include `results.strategy_diagnostics`: warm-up and evaluated-bar counts, opening-history readiness, first-ready timing, raw long/short signal counts and HOLD-reason counts. This records strategy output before risk overrides, separately from order rejections. Readiness establishes sufficient indicator history only; it does not establish a tradeable setup. See [the strategy specification](VWAP_STRATEGY.md#evaluation-dates-and-saved-diagnostics) for field semantics. Older immutable reports may lack this field, and non-VWAP reports use `null`.

## Saved-run chart

The chart route reads the immutable input snapshot, saved `dataset_info` and completed result's `effective_config`; it never reads the current source CSV or updates accounting history. The response includes the run ID, dataset SHA-256, `dataset_info`, declared `origin`, `synthetic` flag, symbol/currency/timezone, interval, available sessions, selected session, opening window, EMA periods, OHLCV bars, and matching saved fills/rejections. Imported cadence and session hours come from metadata; an active VWAP run must agree with its saved configuration. Intraday views default to the first BUY session, or the latest available session when no BUY exists. An optional session must be a valid available `YYYY-MM-DD` date within the saved run's active date range.

Session VWAP uses cumulative typical price `(high + low + close) / 3` weighted by volume and resets each session. It includes only declared regular-session bars and is `null` outside those hours. The three reference EMA overlays use the saved VWAP periods and an arithmetic-mean seed. For active VWAP, chart candles and EMA history contain only regular-session bars; SMA/EMA/RSI charts retain all observed bars, including extended hours, and use that full history for their reference EMAs. Earlier warm-up history is included before slicing the view. Unready EMAs and VWAP without positive regular-session volume are `null`. These overlays explain the data without rerunning saved trading decisions. Daily datasets return reference EMAs, `interval_minutes: null`, `session: null`, no session list, and `vwap: null`.

Responses contain at most 1,000 candles, with `total_bars` and `truncated` identifying a capped view. Indicator warm-up is calculated before this view is trimmed. Intraday timestamps and chart times remain exchange-local bar-start times. The terminal-style browser interface uses these returned candles and saved fills, with session selection and the opening window visible beside the strategy controls. Both bundled datasets remain synthetic research examples.

`regular_session_only` identifies the chart's bar scope. `indicator_scope.ema` is `regular_session_bars` or `all_observed_bars`; `indicator_scope.vwap` is `regular_session_only` or `not_applicable`. Each candle has `regular_session: true/false` (daily: `null`), and `outside_session_bar_count` counts extended-hours candles in the returned view. Non-VWAP views preserve early/late saved fills, including dates containing only extended-hours observations.

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
python -m unittest discover -s tests -p test_import_data.py -v
python scripts/recovery_demo.py --engine build/bin/trading_bot.exe
```

The service tests cover concurrent submissions and claims, changed-payload conflicts, stale owners, lease expiry, cancellation orderings, immutable history, partial-exit basis replay, intentional accounting corruption, bounded abandoned retries, HTTP validation, and actual child-process termination before and after transaction commit. When the executable is built, an additional integration test runs and reconciles a real C++ backtest. Set `TRADING_BOT_ENGINE` for a nonstandard build location. That integration test explicitly reports a skip if the executable is unavailable; CTest supplies the built executable.

Mandatory CTest suites cover engine/strategy/configuration behavior, service durability, CLI, chart data, standard/TradingView imports and provider operations. The `chart_data` suite covers causal overlays, metadata, extended-hours preservation and query validation. CSV tests cover strict bounds, provenance and DST normalization; provider fixtures cover bounded read-only history and job lifecycle. These tests do not validate market authenticity, user entitlements or real-market strategy performance.

The recovery demonstration uses the actual C++ engine and an actual Python worker subprocess. The worker deliberately calls `os._exit` immediately before commit (91) or immediately after commit (92), before returning success. The script reopens SQLite, repeats the identical submission, restarts the worker, checks one publication and unique fill/event identities, and replays the portfolio. For the precommit case only, it advances the dead worker's lease to expired in the test database rather than waiting 90 seconds. It asserts that the fixture actually creates fills. This is deterministic crash injection, not a power-failure or filesystem-corruption test.

## Limits and deferred scope

The service allows 16 concurrent HTTP connections, 32 KiB run requests, 12 MiB import requests, 8 MiB CSV snapshots, 100,000 rows per import, 50 imports, 50 queued/running jobs, and 1,000 total runs per database. Engine result files are independently limited to 64 MiB: an accepted import can still produce an oversized report, requiring a smaller backtest date range or input file. Results and events are returned as complete documents. The HTTP connection timeout is 10 seconds, database busy timeout is 15 seconds, and engine timeout is 60 seconds. Capital is limited to 1 through 100,000,000 currency units by the API. The CLI supports a broader documented range. Numeric settings must be finite and satisfy the API bounds; the engine validates cross-field relationships.

SQLite WAL with `synchronous=FULL` is appropriate for this single-machine demo. This is not a public multiuser service: there is no authentication or tenant boundary, network filesystem support, automatic retention, migration framework, resumable event pagination, or production operations claim. The history is immutable through the application, not protected against a privileged database owner. Back up the database with SQLite's backup mechanism; do not copy only the main file while writes are active and assume the WAL is included.

Each C++ backtest executes one sequential portfolio and publishes its entire order outcomes and accounting history atomically. There are no shared-account concurrent order requests, durable pending buying-power reservations, partial fills, or incremental engine checkpoints. Those require a separate order lifecycle and are intentionally deferred. Short signals may be inspected as strategy evidence, but short execution, options, and live orders remain disabled.
