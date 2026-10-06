# Opening VWAP strategy

`VWAP_OPENING` is an explicit, configurable interpretation of the requested mentor-inspired setup: a strong directional candle during the first 15 or 30 minutes after the regular market open, with session VWAP, the 20/50/200 intraday EMAs, and completed weekly and monthly trends all agreeing. It does not claim to reproduce the mentor's exact rules or establish a profitable trading system.

## Signal and execution timing

Input timestamps are **bar starts**, in the exchange's local clock: `YYYY-MM-DDTHH:MM:SS` or `YYYY-MM-DD HH:MM[:SS]`. The strategy requires whole-minute intraday timestamps and bars aligned to `bar_minutes` from the configured opening time. It uses a bar only after its OHLCV is complete. The backtester executes eligible signals at the following bar's open, with modeled costs and portfolio/risk checks. It assumes that quoted opening price is executable; there is no bid/ask or volume-participation model. The following bar's eventual high, low, close and total volume cannot be used to decide a fill already made at its open.

For two-minute bars and a 09:30 open, the 09:32 candle completes at 09:34; its earliest fill is the 09:34 bar's open. A 15-minute opening window requires execution strictly before 09:45. A candle starting at 09:44 is excluded because it completes at 09:46. A 30-minute window requires execution strictly before 10:00. Every opening signal also carries an exclusive `valid_until` timestamp so missing next bars cannot defer an opening entry to a later session.

The engine always expects exchange-local timestamps. Standard CSV input must already use that clock. The TradingView CSV adapter converts Unix-second/offset-aware timestamps into the declared IANA timezone, while preserving date-only daily labels; ambiguous or nonexistent naive local DST times are rejected. The separate Questrade historical adapter normalizes provider timestamps before saving its snapshot. Neither adapter supplies an exchange calendar, early-close handling or corporate-action corrections. The fixed session configuration must match the supplied data. Daily CSV files cannot run this strategy meaningfully.

## Exact entry rule

For a long candidate, all of the following must hold on the completed signal candle:

1. Its next-open execution is inside the configured opening window. No previous entry signal has been emitted that session, and the account is flat.
2. Both completed weekly and monthly trends are bullish, using the definition below.
3. The close is above the fast EMA, and fast EMA > medium EMA > slow EMA. Default periods are 20, 50 and 200 **intraday bars**, not days.
4. The session VWAP is higher than its previous positive-volume value; the increase meets `min_vwap_slope_bps`. The close is above VWAP by at least `min_vwap_distance_bps`.
5. Close > open; absolute candle body / full high-low range is at least `min_body_fraction`; (close - low) / (high - low) is at least `min_close_location`; and absolute open-to-close move / open × 10,000 is at least `min_move_bps`.
6. Volume is positive. A prior positive-volume candle exists to measure VWAP direction. All opening bars from the configured open through the current candle have been observed without gaps.

A bearish candidate mirrors each inequality: bearish completed trends; close < fast EMA < medium EMA < slow EMA; falling VWAP; close below VWAP; red candle closing near its low. It emits **SHORT**, never SELL. The current long-only backtester records/rejects this signal rather than simulating borrowed shares. `enable_short_signals=0` disables those opportunity signals. A valid signal is an opportunity, not a guaranteed fill or a position-size recommendation; risk rules determine the executable size.

There is at most one entry *attempt* per session. A rejected entry does not cause repeated attempts later that morning.

## VWAP, trend history and warmup

Session VWAP = Σ[((high + low + close) / 3) × volume] / Σvolume, over observed regular-session bars starting at the configured open. It resets each session. This is an OHLCV approximation to trade-by-trade VWAP. Zero-volume bars add no VWAP weight and cannot trigger entry. Extended-hours bars are ignored.

Intraday EMAs persist across sessions and consume all valid regular-session closes, including zero-volume closes. They are seeded by an arithmetic average of the first N observations, then updated recursively with alpha = 2 / (N + 1). At least 200 regular-session bars are needed for the default slow EMA; do not supply only opening-window bars. A truncated or gapped opening session disables entries for that day.

Weekly groups use Monday-start calendar weeks. Monthly groups use calendar months. At the first bar in a new group, the previous group's last observed regular-session close becomes its completed close. The **current unfinished week/month never influences the higher-timeframe trend**. The initial observed group is conservatively discarded because it may begin midweek or midmonth. At the next observed session, a known missing opening/interior/closing bar invalidates the affected weekly and monthly groups. When an invalid group rolls over, that timeframe's trend history resets and must warm up again. Supply complete regular-session history with actual closes; the strategy cannot infer missing entire sessions or the exchange's holiday calendar. This strict rule means a real early-close day requires appropriately prepared session configuration/data; the fixed-hours strategy does not silently guess its schedule.

The adjustable higher-timeframe proxy is `trend_ema_period`, default **2**, independently applied to completed weekly closes and completed monthly closes. Bullish means the latest completed close is above its completed-close EMA and that EMA rose from its preceding value. Bearish means below a falling EMA. With period 1, direction is simply latest completed close versus preceding completed close. Flat or conflicting trends block entry. This short default is a practical starting proxy, **not a confirmed detail of the mentor's method**. It can be lengthened after testing; for example, period 20 requires substantially more monthly history.

For period N, N completed closes seed the EMA, plus another completed close establishes its slope. The initial possibly partial group does not count. Default N=2 therefore needs three completed weeks and months after the first observed group. Beginning a file in January ordinarily means a monthly trend first becomes available in May. Warmup returns HOLD with an explicit diagnostic; it does not invent missing history. Backtest date filters must warm the strategy on earlier supplied bars before the requested evaluation start.

## Evaluation dates and saved diagnostics

The workbench evaluation start/end dates map to `backtesting.start_date` and `backtesting.end_date`, inclusive exchange-local calendar dates. Earlier supplied bars warm indicators before portfolio evaluation starts; bars after the end date are not evaluated. A date filter cannot create missing history, so obtain/import the warm-up months as well as the period being tested. A date range containing no observations is an error, not a successful zero-trade result.

New VWAP reports include `results.strategy_diagnostics`. They separate `warmup_bars` from evaluation observations, count regular evaluated bars and eligible opening bars, report intraday/trend/both-ready opening counts, and retain raw long/short signals and HOLD reasons. `first_ready_timestamp` is the first eligible opening bar with sufficient intraday and completed-timeframe history. It does not imply matching trend directions, a strong candle, an available entry attempt, a flat portfolio or risk approval. The recorded reasons come from the actual strategy output before portfolio risk overrides; order rejections remain a separate result field.

`opening_hold_reasons` isolates opening opportunities from ordinary outside-window HOLDs. `last_diagnostics` records indicator/history/readiness state, while `last_regular_timestamp` and `last_bar_regular_session` identify whether the last observation belonged to the regular session. Timestamps identify the bar start; diagnostics were observed after that bar completed. Older saved reports remain immutable and may not contain this summary; non-VWAP reports use `strategy_diagnostics: null`.

## Exit rule

An existing long emits SELL when a completed close falls below session VWAP, or when candle completion reaches `session_close_minute - exit_buffer_minutes`. The quantity is the existing long position. The theoretical mirror emits COVER for an existing short. Time exits remain subject to next-bar availability. A final signal without a following bar is cancelled, and remaining positions are marked to the last close with unrealized P&L; the backtester does not invent a final liquidation. Missing late-session data can leave an overnight or end-of-file position and must not be treated as a faithful day-trading simulation.

Stop loss, take profit, trade sizing and costs are separate portfolio/backtest controls. This strategy does not place a live order or promise to prevent overnight exposure in real markets.

## Parameters

| Parameter | Default | Meaning / accepted range |
| --- | ---: | --- |
| `bar_minutes` | 2 | Whole minutes, 1–30; must match input interval and divide session duration exactly |
| `session_open_minute` | 570 | Minutes after midnight, 0–1438; 570 = 09:30 |
| `session_close_minute` | 960 | Minutes after midnight, 1–1439, after open; 960 = 16:00 |
| `opening_window_minutes` | 30 | Whole minutes, 1–120; commonly 15 or 30; no longer than session |
| `fast_ema` | 20 | Whole period, 1–100000 |
| `medium_ema` | 50 | Whole period, 2–100000; greater than fast |
| `slow_ema` | 200 | Whole period, 3–100000; greater than medium |
| `min_body_fraction` | 0.60 | Minimum absolute body / range, 0–1 |
| `min_close_location` | 0.75 | Long closes in top quarter by default; short in bottom quarter, 0–1 |
| `min_move_bps` | 10 | Minimum absolute body move in basis points, 0–10000 |
| `min_vwap_slope_bps` | 0 | Minimum directional change from previous VWAP, 0–10000; direction must still be strict |
| `min_vwap_distance_bps` | 0 | Minimum directional close distance from VWAP, 0–10000; close must still be strictly on correct side |
| `trend_ema_period` | 2 | Completed-close trend EMA period, 1–120 |
| `exit_buffer_minutes` | 4 | Whole minutes before close, at least bar_minutes, shorter than session |
| `enable_short_signals` | 1 | 0 or 1; short signals remain rejected by the long-only simulator |

All numeric parameters must be finite. Invalid fractional periods, unknown keys and inconsistent bounds are rejected. `initialize(get_parameters())` completely resets a strategy for a reproducible replay. `diagnostics()` exposes VWAP/EMA values, history counts, trend directions, readiness and the most recent signal/HOLD reason.

## Included synthetic fixture

`data/opening_demo.csv` contains 38,025 entirely fictional two-minute bars from January through September 2024, with full 09:30–16:00 weekday sessions. The price rises through May and declines afterward, deliberately producing both long setups and later bearish opportunities after warmup. Weekdays that are real exchange holidays are still included; this is a deterministic software exercise, not historical market data. It must not be used to select profitable parameter values or describe real historical returns.

Regenerate it with `python scripts/generate_vwap_demo.py`. The generator has no network or external-data dependencies. Strategy checks in `tests/test_strategies.cpp` use explicit exceptions rather than assertions disabled by release builds, covering parameter validation, warmup, weighted VWAP, opposing trends, bearish mirroring, window cutoffs, zero volume, resets, repeatability and unfinished-period exclusion.

The existing SMA and EMA strategies now use incremental crossover calculations and reset cleanly. RSI uses Wilder smoothing, buys a recovery above the oversold threshold, and exits a long when RSI crosses down through the overbought threshold; flat RSI is 50. These are distinct strategies, not extra opening-VWAP requirements.
