# Release 2.0 verification record

Completed October 5, 2026 (America/Vancouver).

## Starting point and preservation

- Read the project handoff and located the existing checkout at `C:/Users/Danial/Desktop/tradingBot`.
- Starting commit: `003526dcaf8e9efc0eb52a064f0397f512c60edd` (verified as a commit, not just a tree ID). Remote configured by that checkout: `https://github.com/danial-tj/trading-bot-cpp.git`.
- Created `C:/Users/Danial/Documents/ChatGPT/personal website/trading-bot` on `codex/complete-simulator` and copied the eight modified/untracked project files from the Desktop checkout before implementation. The original Desktop checkout was not changed.
- Baseline MSVC configuration succeeded, but the build failed on missing logger symbols and API linker dependencies. CTest discovered zero tests; optional Google Test discovery silently skipped the old suite.

## Implemented

- [x] Offline C++ executable with a documented JSON CLI, vendored JSON parser, strict configuration validation, required test discovery and reproducible source identification.
- [x] Checked integer-cent ledger, whole-share positions, fees/slippage, exact affordable sizing, partial-exit basis, realized/unrealized profit, proper mark-to-market equity, independent run resets and explicit rejections.
- [x] Causal next-open execution, completed-close stop/take-profit decisions, daily/drawdown controls, date-range warmup, final-position valuation and meaningful null metrics.
- [x] SMA, EMA and RSI updates; configurable opening VWAP with 15/30-minute window, candle strength, EMA20/50/200, completed weekly/monthly filters and research-only SHORT outcomes.
- [x] Immutable SQLite dataset snapshots, persistent request idempotency, payload conflicts, atomic result/event publication, stale-claim protection, cancellation, abandoned-job retries and independent accounting replay.
- [x] Local API and browser workflow, automatic sample, adjustable parameters, saved history, balance charts, drawdown, buy-and-hold comparison, readable fill/rejection reasons, JSON export and reconciliation.
- [x] Research workbench with a graphite candlestick chart, readable strategy inspector, saved-run journal, session selection, opening-window context, volume, VWAP/EMA overlays and saved execution markers. Chart data comes from each run's immutable snapshot and saved configuration, with daily data handled separately from intraday VWAP.
- [x] Two generated synthetic datasets, reproducible generators, source/provenance documentation, restart demonstration, benchmark script/report and Windows/Linux/sanitizer CI configuration.

## Validation performed

Environment: Windows, MSVC 19.37.32822, Visual Studio 2022 Build Tools, CMake 4.1, Python 3.11.4, x64 Release. No API keys or broker/network data used.

```text
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release --parallel 4
python scripts/check_test_discovery.py --build build
ctest --test-dir build -C Release --output-on-failure --no-tests=error
python run_example.py --dataset sample --strategy SMA_CROSSOVER --output .local/cli-demo.json
python scripts/recovery_demo.py --engine build/bin/Release/trading_bot.exe
```

All **five CTest suites passed**. The final functional test run took 9.83 seconds on this machine. Suite output: **39 engine cases**, **8 strategy test groups**, **29 configuration/integration checks**, **22 service tests**, and **7 CLI tests**. C++ checks remain active with `NDEBUG`. The following rebuild removed only trailing blank lines and refreshed the source fingerprint; it completed successfully without behavioral changes.

The subsequent chart/design update added an independently registered `chart_data` suite with **11 passing tests**. It verifies causal VWAP/EMA projections, immutable saved inputs and configuration, session/date selection, original fill filtering, daily charts without session VWAP, row bounds, and HTTP query validation. After CMake reconfiguration, all **six CTest suites passed** in 13.98 seconds, including all **22 existing service tests**, real engine integration and crash recovery. Test discovery also confirmed all six suites. The engine and accounting implementation were unchanged by this chart update; this verification reused the existing Release binaries without an unrelated rebuild.

The real-worker recovery demonstration passed both scenarios: a process exit before commit recovers on attempt 2; an exit after commit/before acknowledgement retains attempt 1. Both yield one published result, 12 logical fills, 373 accounting events, cash 1,049,770 cents, realized profit 49,770 cents and fees 1,209 cents; replay agrees. These are synthetic-fixture outputs and demonstrate only the documented transactional publication boundary.

Browser verification: loaded the sample, submitted a 15-minute VWAP run through the form, saw completion and the effective settings, and used the reconciliation control to verify saved balances. Checked desktop rendering and a 375px viewport (360px client area with scrollbar), with no document horizontal overflow; activity tables scroll within their own region. No browser errors were recorded. A real form-validation issue in the candle-strength step increment was found and fixed. [Preview](demo-preview.jpg).

Previous terminal iteration (superseded by the research-desk redesign below): visually reviewed TradingView's feature page and IBKR Desktop, then implemented the local dark chart workspace. Browser checks covered candles/line switching, VWAP visibility, keyboard candle inspection and its accessible announcement, session selection, full-session/opening-window zoom, trade pagination and unfilled SHORT outcomes, and balance reconciliation. Submitted a completed 30-minute VWAP run and a completed daily SMA run through that form; daily charts disable session VWAP and opening-window controls. Saved-run selection persisted across refresh and restored its effective setup. Checked 1440px desktop, 768px tablet and 375px phone layouts; tablet/phone had no document horizontal overflow, and key mobile controls had 44px targets. No browser warnings/errors were recorded. Exact 15-minute shading uses fractional two-minute candle boundaries. Standalone chart geometry checks passed with `node tests/test_chart_frontend.mjs`.

Previous light research-desk iteration (superseded by the dark revision below): the interface used the project's portfolio toolkit, off-white surfaces, Manrope, a graphite chart and a separate saved-run journal. Browser checks completed a new 15-minute VWAP run using the keyboard window selector and the top Run button, restored a saved run's settings, retained the Saved runs destination across reload, switched to the line chart, used pan/opening controls, inspected Trades and verified accounting reconciliation. The 768px tablet layout had no document horizontal overflow. At a 375px phone viewport, document client and scroll widths both measured 368px. The readable phone layout, Edit rules jump, strategy form and 44px window/pan/zoom targets were checked.

The revised service passed all **23 tests**, including an HTTP check of the bundled WOFF2 font, its MIME type, exact bytes and unchanged security headers. Chart geometry checks passed. JavaScript syntax and static element-reference checks passed. Draft edits are preserved while initial results or submissions are pending, and chart controls remain disabled while their data loads.

Current dark revision: the user preferred the black theme and requested less generic structure and controls. The workbench now uses a compact toolbar and adjoining chart/results/setup panels, paired rule labels and actual parameter values, one desktop Run action and a form Run action at smaller widths. Trades scroll within a 460px maximum-height region with a sticky table header. Hover, pressed and focus states are immediate; no animation or transform is applied. [DESIGN.md](../DESIGN.md) records the current contract.

Browser checks covered dark layouts at 1040px, 1440px and 375px. Desktop document client/scroll widths both measured 1433px; phone widths both measured 368px. Keyboard window selection worked, a new 30-minute backtest completed, and the phone Edit rules jump and strategy settings were checked. JavaScript syntax and chart geometry tests passed. No additional backend test run is claimed for this visual revision. Current application captures are [desktop preview](demo-preview.jpg) and [phone preview](demo-mobile.jpg); the generated PNG remains a separate historical composition reference.

Menu refinement: directly inspected [TradingView's actual chart interval dropdown](https://www.tradingview.com/chart/) and consulted the [WAI-ARIA combobox pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/). All four native select popups were replaced with anchored dark menus, selected ticks, bounded scrolling and keyboard controls; session choices include search and an empty-results message. Validation now focuses the invalid field with an inline dark error instead of a browser popup. The chart is inert during loading; range buttons reflect the actual view and disable at their limits, and zoom updates the displayed quote.

Browser checks opened all four desktop menus and confirmed their dark appearance. Searching for June 3 selected that chart session. Choosing SMA and daily data through the new controls produced a completed run, after which the saved VWAP setup was restored. Invalid capital focused the red-marked field with its inline message and no native popup. Escape, Tab and Shift+Tab returned or advanced focus correctly. At 375px, the phone menu remained within the 368px page width and its list scrolled. Expanded chart-state/geometry tests and JavaScript syntax checks passed. Actual menu captures: [desktop dropdown](dropdown-preview.jpg) and [phone dropdown](dropdown-mobile.jpg).

Final measured binaries and source fingerprints, hardware, flags and repeated timings are in [benchmark-results.json](benchmark-results.json), with interpretation in [BENCHMARK.md](BENCHMARK.md). Peak process memory was not measured; no memory or production-throughput claim is made.

## Deliberate limits and remaining external verification

- Live execution, actual short positions, options, brokerage accounts, subscriptions and shared-account order reservations are not part of this local simulator release. Bearish VWAP signals are visible but unexecuted.
- The mentor's exact definition of candle strength and higher-timeframe trend remains unconfirmed. The configurable starting rules are documented, not presented as a validated trading edge.
- Synthetic data does not validate market performance. Real-data evaluation needs correctly licensed, complete intraday history and appropriate calendar/corporate-action preparation.
- Linux/sanitizer CI jobs have been authored but not executed remotely in this session. Local WSL inventory was unavailable, so no Linux success is claimed. No repository changes were pushed or published.
- The legacy C++ network adapters remain excluded. The later Python Questrade workflow below is a separate bounded historical-data implementation; the legacy adapters' TLS/cache/provider behavior is not considered repaired.
- The local server has no public authentication or multi-tenant deployment model. No external usability feedback was collected, and no one was messaged.

The finishable local research workflow is implemented and verified on Windows. Follow-up brokerage or real-market work can proceed separately without changing what this release claims.

Final menu check: at 812×375 the open session menu stayed inside the viewport (top 177px, bottom 367px), with document width 805px and scroll width 805px. The activity menu switched to real unfilled-signal rows. The final browser console contained no warnings or errors. Both dropdown screenshots were saved from the working application.


## Historical CSV import milestone · 2026-10-06

Added a Data workspace for local OHLCV CSV imports, a persistent immutable dataset registry, strict format/price/session validation, original-file and canonical snapshot fingerprints, and saved source/symbol/timezone/currency/adjustment metadata. Imported labels mean user-provided, not independently verified market data. No live data feed or provider account is connected.

The chart now uses each imported dataset's interval and session boundaries. Crossover/RSI views retain extended-hours bars and fills; opening VWAP stays in regular sessions. Reference VWAP excludes extended hours. Saved-run details and exports retain source metadata, and the interface formats money in the declared simulation currency without FX conversion. Loading saved settings clears stale form errors, daily/VWAP validation focuses the dataset control, and a failed chart request has a session-preserving retry.

Validation: all **seven CTest suites passed** in **17.53 seconds**, using the existing unchanged C++ Release binary. This includes **34 service tests**, **30 import tests**, and **20 chart tests**, plus engine, strategy, configuration and CLI suites. The service suite includes concurrent import deduplication, registry limits, immutable data/metadata, HTTP security/body limits, imported-run execution and an eight-thread legacy-schema migration regression. Standalone chart geometry tests and JavaScript syntax checks passed; suite discovery lists all seven required suites.

Browser checks imported the bundled two-minute opening fixture under the explicit name “QA · synthetic opening sessions”, ran Opening VWAP, and confirmed its symbol, source and interval. A BOM-prefixed daily synthetic fixture imported as “QA · synthetic daily CAD”, correctly rejected Opening VWAP with focus on the invalid selector, then completed EMA crossover. Its stored original fingerprint matches the exact BOM-bearing file bytes, exported metadata retains CAD/QADAILY, and independent accounting replay passes. These fixtures establish import correctness only, not market performance.

Invalid capital followed by saved-run selection restores valid settings and clears the old error. Stopping the owned local service caused a chart failure; restarting it and using Retry chart restored the requested May 2 session. Imported datasets and completed runs survived the restart and browser reload. At 390×844 the Data workspace and open interval menu fit within the 383px document width without horizontal overflow. The final reloaded browser console contained no warnings/errors. Actual captures: [Data workspace](import-preview.jpg) and [mobile interval menu](import-mobile.jpg).

The local test imports and results are in the ignored SQLite database. No real historical market dataset has been evaluated in this milestone. The next research step is to supply appropriately sourced historical sessions and evaluate the 15/30-minute settings with costs and held-out periods before adding live quotes/paper execution.

## Historical sources and evaluation diagnostics · 2026-10-06

Added a TradingView CSV format selector with matching file/timezone guidance and source-specific library labels. The adapter reads native export headers in any order, retains exact original-text provenance, identifies ignored indicator columns and delegates strict OHLCV validation to the existing importer. Unix-second and offset-aware timestamps normalize through the declared IANA timezone; daily date labels remain unchanged, and ambiguous/nonexistent naive DST times fail. Pinned `tzdata` supplies timezone rules on Windows. This uses TradingView's manual export, with no TradingView login, scraping or customer market-data API.

Added the optional read-only Questrade historical workflow: explicit refresh-token connection, symbol lookup, a bounded date/interval request, progress and cancellation, and complete-snapshot publication into the existing immutable registry. Only one download can be active. Tokens and rotated refresh credentials remain in process memory and are cleared on disconnect/restart; provider job identity is also process-local. No token appears in persisted dataset/run provenance, no partial download is published, and no account balances, orders or live stream are used. Actual user credentials, entitlements and a real Questrade download have not been verified in this working session.

Evaluation start/end controls preserve earlier input for warm-up. New VWAP reports save actual readiness counts, raw signal counts, first-ready timing and HOLD-reason summaries before portfolio risk overrides. These distinguish unavailable history from entry filters while keeping sufficient history separate from a valid or executable trade. Previous immutable reports are preserved without inventing diagnostics.

All **10 CTest suites passed** in **20.97 seconds** after a fresh Windows Release build. Review then fixed refreshable-session handling, cancellation immediately after token rotation, uncertain-submit recovery and calendar-date validation. The three affected suites passed again in **18.93 seconds**: **35 service tests, 31 Questrade client tests and 25 provider-job tests**. The engine suite includes **48 cases**, including readiness, separate warm-up counts, raw signals before risk overrides and evaluation end-date causality. The unchanged standard/TradingView import suites contain 30 and 15 tests respectively. Separate JavaScript checks cover chart geometry, Gregorian dates, expired-but-refreshable sessions, lost-response recovery and scoped error clearing. Real worker crash recovery before/after publication also passed.

The browser imported 38,025 explicitly synthetic candles in TradingView format, converting UTC seconds to New York bar starts and listing the extra EMA column as ignored. A May 1–31 evaluation retained 16,770 prior warm-up bars, observed 322 eligible opening candles and recorded 23 buy signals. The original CSV fingerprint matches the fixture bytes; saved accounting independently reconciles. The dataset, evaluation dates and diagnostics survived a service restart. These are workflow checks, not real market performance evidence.

An isolated temporary service with a fake provider verified connect, exact-symbol selection, invalid-date focus, download progress, successful library publication, cancellation and disconnect during download. It was stopped after testing; no fake provider was installed in the normal service. At 390×844 both Data and signal review have 383px content/scroll width with no horizontal overflow. The format menu, historical interval menu and evaluation fields retain the dark theme. The final browser console contains no warnings/errors. Actual captures: [historical sources](history-preview.jpg), [mobile CSV format](history-mobile.jpg), and [saved signal diagnostics](diagnostics-preview.jpg).

The earlier CSV milestone was pushed as `78fe92c`. This phase's Questrade network path still requires the user's manual authorization token and a successful real-data download before its external connection can be claimed as verified. Live quotes and order execution are not implemented.
