# Release 2.0 decisions

This is an offline, local simulator. It keeps the C++ engine and adds a Python standard-library service, one SQLite database, and a browser interface. No broker, paid subscription, account connection, or public hosting is involved.

## Accounting and execution

The ledger owns cash, positions, cost basis, fees and realized profit. Risk code never independently updates cash. Whole-share long holdings and one nominal currency per run keep the numeric contract reviewable. Prices, money and cost basis are signed 64-bit cents with checked arithmetic. The shortest round-trip decimal representation of an incoming double is rounded half away from zero to cents; this makes decimal ties such as 1.005 explicit. Indicator calculations and rate calculations use floating point; their monetary outputs are quantized once. Entry fees join cost basis, partial disposals allocate basis proportionally to whole cents, and the final disposal removes all remaining basis.

Signals use completed bars and execute at the next observed open, adjusted for slippage, with commissions applied to the actual fill. Orders cannot use that execution bar's later high, low, close or aggregate volume. The quoted open is assumed executable; there is no spread, liquidity, borrow, or participation model. The simulator rejects unsupported short opportunities instead of interpreting them as sales of stock it does not own.

Stop loss and take profit are checked on completed closes and execute at the next open. They are not intrabar guaranteed stops. Zero disables either threshold. Drawdown and daily-loss limits must be positive; they block increases in exposure and never prevent reductions. Each day's loss baseline is the previous observed close's equity (initial cash on the first day), so overnight gaps count toward the new day's loss. Position allocation is maximum market exposure as a fraction of equity; entry costs also constrain affordable size. There is no ATR sizing feature.

Final holdings stay open, marked to the last close. A signal without another eligible bar receives an explicit unfilled outcome. Win rate counts realized exit fills, including partial exits, rather than pretending each fill is a round trip. Fees enter profit only once. Annualized return and Sharpe are null because the release does not infer a dependable trading calendar/frequency from arbitrary input. An unavailable profit factor is null.

Buy-and-hold is a separate precommitted baseline: spend the maximum affordable balance at the first in-range open with the same entry slippage/commission, then hold at the final mark. It has different exposure from a risk-capped strategy and no invented final sale fee.

## Strategy scope

SMA, EMA, RSI and opening VWAP are separate selectable strategies. SMA/EMA use incremental updates and explicit warmup. RSI uses Wilder smoothing. [The VWAP specification](VWAP_STRATEGY.md) states the interpretation of the user's opening 15–30-minute strong-candle setup, its EMA filters and its completed-period trend proxy. The mentor's precise thresholds were not provided. No claim of reproducing that exact system or validating profitability is made.

## Persistence boundary

Each simulation owns an independent portfolio. A worker can rerun a short deterministic simulation from its immutable dataset snapshot. One transaction publishes its complete result and immutable accounting events. A persistent request key, payload hash, unique result, claim generation and cancellation checks prevent duplicate publication after retries or stale-worker completion. Reconciliation independently rebuilds cash, quantities, basis, realized profit and fees from integer events.

This deliberately does **not** implement a shared brokerage account or independently submitted live orders. Cross-order cash reservations and partial market fills therefore remain outside this release. There is no claim of universal exactly-once delivery or double-entry bookkeeping. See [service guarantees and recovery](SERVICE.md) for the tested transaction boundary.

SQLite provides a reproducible local demo without credentials or another service. It serializes writes and is not presented as a multi-tenant deployment solution. The HTTP server binds only to loopback and restricts accepted Host/Origin, request size, dataset choices, run counts and worker runtime. It has no remote authentication and is not a public hosting server.

## Provenance and limitations

Every report contains effective configuration, engine version, source SHA-256, Git description, simulation assumptions, and a CLI FNV-1a dataset fingerprint. The service additionally stores an immutable dataset snapshot and its SHA-256. The CLI checks its fingerprint before and after a run to detect input changes; use immutable files. FNV-1a is for identity/reproducibility, not malicious-tampering resistance.

Both new datasets are generated fictional OHLCV. Exchange holidays, early closes, daylight-saving conversion, corporate actions, distributions and real historical data licensing are not silently inferred. Missing entire sessions cannot be detected without an external exchange calendar. Fixed session hours must match supplied bars. No credentials are needed to run the demo.

Existing network adapter/reporting examples remain as historical code and are excluded from the release build. The old TLS/cache implementation is not reachable through the new CLI or service; it must be independently repaired and tested before reuse. Previous performance/resume claims are superseded by current test and benchmark evidence.
