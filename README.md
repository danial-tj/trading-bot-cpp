# Opening Bell · C++ trading simulator

A reproducible local research app for inspecting strategy signals, simulated fills, fees and portfolio balances. C++17 handles market data, indicators, risk and fixed-point accounting. A Python standard-library service saves runs and immutable activity in SQLite. The browser provides an integrated dark workspace for candlesticks, volume, VWAP/EMA overlays, saved fill markers, performance curves and a buy-and-hold comparison.

**Release 2.0 is an offline simulator.** The bundled data is synthetic. Broker execution, borrowed-share short execution and options are not implemented. Bearish VWAP setups are recorded as unfilled research signals. A backtest is not evidence of a durable trading advantage.

## Start on Windows

Requires CMake 3.16+, Visual Studio 2022 Build Tools with the C++ workload, and Python 3.9+. The old MinGW GCC 6.3 installation is insufficient for the C++17 standard-library features used here. The JSON dependency is vendored and builds need no downloads or API keys.

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release --parallel 4
ctest --test-dir build -C Release --output-on-failure --no-tests=error
python run_example.py --serve
```

Open http://127.0.0.1:8765. The interface loads a reproducible VWAP sample on its first visit. Choose a strategy, change its parameters and run another simulation. Stop the server with Ctrl+C. Results remain in `.local/trading.sqlite3` across restarts.

Select a session to inspect its saved candles. Toggle the overlay labels, switch between candles and a closing-price line, or use Opening window / Full session and the pan/zoom controls. Arrow keys inspect candles, Home/End select the first/last bar, and dragging pans the chart. Overview, Trades and Run details expose performance, unfilled signals, export and balance verification. Selecting a saved run restores its setup as the starting point for a new run; edits made while it loads are preserved.

Use **Data → Import CSV** to add one symbol's historical OHLCV file. Supply its source, symbol, exchange-local timezone, price currency, adjustment status, candle interval and regular-session hours. **Validate & import** saves a local immutable dataset; review its warnings, then select it for a simulation. The two synthetic examples remain available. Imported means user-provided: authenticity, licensing and market accuracy are not independently verified, and no prices are downloaded or streamed.

Imports accept UTF-8 CSV up to **8 MiB and 100,000 rows**, with at most **50 imported datasets** per database. Daily data supports SMA, EMA and RSI; opening VWAP requires fixed-minute intraday history. See [data format and provenance](data/README.md) for exact bounds and timestamp rules. Large imports can exceed the worker's separate 64 MiB result limit; use a smaller backtest date range if needed.

The design uses black/charcoal surfaces, self-hosted Manrope, compact toolbars and a docked strategy inspector. Thin rules organize the chart and results without rounded dashboard cards or an oversized page header. TradingView informed the chart workflow; the portfolio toolkit and [21st.dev Trade Journal Table](https://21st.dev/@ssychui/components/trade-journal-table) informed the hierarchy and journal. See [DESIGN.md](DESIGN.md) for the applied references and interaction contract. Everything runs locally; no provider account or connection is implied.

## Start on Linux

Requires a C++17 compiler and standard library (GCC 11+ or Clang with a recent standard library recommended), CMake and Python 3.9+.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure --no-tests=error
python3 run_example.py --serve
```

Windows MSVC builds and tests were run locally. Linux and sanitizer jobs are configured in `.github/workflows/ci.yml`; their remote runs have not been established by this working session.

## Use the engine directly

```powershell
.\build\bin\Release\trading_bot.exe --data data/opening_demo.csv --strategy VWAP_OPENING --config examples/config_vwap_opening.json --output report.json
python run_example.py --dataset sample --strategy SMA_CROSSOVER
python scripts/recovery_demo.py --engine build/bin/Release/trading_bot.exe
```

On Linux, use `build/bin/trading_bot`. `--config` is optional; omitted settings get documented defaults. An explicitly missing or invalid config fails. Unknown settings, duplicate JSON keys, unsupported short execution and invalid data fail with JSON errors on stderr and a nonzero exit. Successful JSON goes to stdout unless `--output` is supplied. `--help` lists the arguments.

The launcher also accepts `--engine PATH`, `--config PATH`, `--output PATH` and `--port PORT` for serving. The browser selects bundled or locally imported dataset IDs. The CLI accepts a CSV path directly; it does not add that file to the browser's dataset library.

## Opening-session VWAP

The requested setup is a **strong candle within the first 15 or 30 minutes**, aligned with session VWAP, 20/50/200 intraday EMAs and completed weekly/monthly trends. This release makes that interpretation explicit and configurable:

- Default 30-minute window after 09:30, with two-minute candles. Entries must execute before 10:00; the 15-minute setting ends before 09:45.
- Bullish body at least 60% of the candle range, closing in the top quarter, with at least a 10-basis-point body move.
- Close above rising session VWAP and above a bullish 20 > 50 > 200 EMA stack. Bearish opportunities mirror the rules.
- Completed weekly and monthly close trends must agree. The configurable default uses a 2-period EMA of each completed timeframe; this is a starting proxy, not a confirmed rule from the mentor.
- Full prior sessions warm up the EMAs and higher-timeframe history. Unfinished weeks/months are excluded. Known gapped/truncated sessions invalidate trend history.
- Exit on a close below VWAP or near the configured session close; actual fills still need another bar.

Read the exact [strategy specification](docs/VWAP_STRATEGY.md), including timestamp semantics, thresholds, warmup and calendar limitations. The existing SMA, EMA and RSI strategies remain independently selectable. All strategies execute after a completed signal bar, at the next observed open with configured costs.

## What is verified

- Checked integer-cent cash, fees, cost basis and realized profit, with whole shares; no unavailable-cash spending or excess selling.
- Correct marks include both cash and holdings. Partial exits allocate basis; final holdings remain open and marked rather than being silently liquidated.
- Run state resets, future candles cannot influence previous opening fills, and errors remain distinguishable from zero-trade results.
- Persistent request idempotency, payload-conflict rejection, worker leases/generations, transactional immutable publication, cancellation and independent replay of accounting history.
- Real subprocess crash tests before commit and after commit/before acknowledgement. Retried jobs do not publish duplicate results or duplicate accounting events.
- Seven mandatory CTest suites: engine, strategies, config/integration, service/recovery, CLI, immutable chart data and CSV imports. Tests do not disappear in Release builds or when Google Test is absent. Optional chart geometry checks run with `node tests/test_chart_frontend.mjs`. Import verification uses controlled fixtures; it does not establish real-market-data performance.

See [engineering decisions](docs/DECISIONS.md), [service/API and recovery](docs/SERVICE.md), [local verification record](docs/PROGRESS.md), and [data provenance](data/README.md). Some old root-level example tests/docs remain for history and are not the release test suite.

## Reproducibility and performance

Reports retain all effective parameters, engine version, Git description, source SHA-256, dataset fingerprint, fills, outcomes, integer accounting events and simulation assumptions. The service stores an immutable snapshot of the submitted dataset, its SHA-256 and source metadata. Imports also retain a separate fingerprint of the original submitted CSV text. Export JSON or use the interface's reconciliation action to inspect a saved result.

```powershell
python scripts/check_test_discovery.py --build build
python scripts/benchmark.py --engine build/bin/Release/trading_bot.exe --core build/bin/Release/benchmark_engine.exe --build-dir build --repetitions 5 --output docs/benchmark-results.json
```

The benchmark separates preloaded in-memory engine work from complete CLI latency, including CSV and JSON. It labels synthetic data and records compiler, configuration, repetitions, CPU and memory information where available. Its standalone naive-vs-rolling SMA comparison verifies matching numerical checksums; it is not a claim that the complete application is faster by that ratio. See the recorded benchmark for measured results and scope.

For supported GCC/Clang builds, `-DENABLE_SANITIZERS=ON` enables AddressSanitizer and UndefinedBehaviorSanitizer. CI checks expected suite discovery so a missing suite fails the build.

## Limits of this release

Each simulation has its own portfolio. There is no shared-account order reservation service, partial market fills, options pricing, brokerage adapter, live trading, public authentication, exchange calendar, corporate-action processing or real market data subscription. VWAP uses OHLCV typical-price approximation and assumes complete regular sessions in exchange-local time. Execution assumes the next quoted open is available, with no liquidity/participation model.

The imported currency code labels one nominal simulation currency; it performs no FX conversion or account-currency reconciliation. The declared timezone labels timestamps already in exchange-local time; no timezone/DST conversion occurs. Session hours are fixed within each dataset, with no overnight sessions, holiday/half-day calendar or automatic corporate-action adjustments. Buy-and-hold uses the full available balance, while a strategy may use a smaller allocation. Stops are close-based decisions and can execute beyond their thresholds after a gap. Unsupported annualized/Sharpe metrics are null rather than fabricated zero values.

The old network adapter code is excluded from the release build. Its previous TLS/cache implementation requires separate repair and verification before any reuse. No credentials or user accounts are required for the finished local workflow.
