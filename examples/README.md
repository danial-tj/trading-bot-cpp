# Offline example configurations

All files here use the validated Release 2.0 schema. They merge with explicit engine defaults; unknown or invalid settings fail.

| File | Select strategy | Difference |
| --- | --- | --- |
| config_vwap_opening.json | VWAP_OPENING | 30-minute opening window, EMA20/50/200, strong-candle thresholds |
| config_vwap_15_minutes.json | VWAP_OPENING | 15-minute opening window |
| config_conservative_sma.json | SMA_CROSSOVER | 5% allocation, SMA10/30 |
| config_aggressive_sma.json | SMA_CROSSOVER | 25% allocation, SMA3/9 |
| config_ema_swing.json | EMA_CROSSOVER | EMA12/26 |
| config_rsi_scalping.json | RSI | RSI7, oversold30/overbought70 |

Historical filenames do not establish that a preset is profitable, safe, or suitable for scalping. See the root README for execution assumptions and data provenance.

Example: `python run_example.py --strategy VWAP_OPENING --config examples/config_vwap_15_minutes.json`.
