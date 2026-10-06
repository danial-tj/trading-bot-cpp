# Local performance measurement

The checked-in `benchmark-results.json` records one measured run on this Windows computer, using the MSVC Release build (`/O2 /Ob2 /DNDEBUG`) and five timed repetitions after one excluded warmup. The report includes each timing, the processor description, logical CPU count, Python version, compiler family, build flags, arguments, CSV fingerprint, both executable SHA-256 hashes and the engine's compiled source SHA-256 identity. Provenance is obtained in an additional untimed CLI run. Local load and hardware affect the results; these are not production capacity promises.

| Measurement | Input and scope | Median |
| --- | --- | ---: |
| In-memory C++ engine | 39,000 generated fictional bars; VWAP strategy, risk checks, ledger, statistics and result collection; no process startup, input generation, CSV reading or JSON serialization | 107.6419 ms |
| Complete CLI | 38,025 fictional opening-demo bars; process startup, configuration, CSV reading, simulation, comparison result and full JSON serialization to discarded stdout | 646.6579 ms |
| Naive SMA200 calculation | Recomputes each 200-price window over the generated input | 2.4152 ms |
| Incremental SMA200 calculation | Equivalent rolling sums over the same generated input, with matching output checksum | 0.0928 ms |

The core measurement corresponds to approximately 362,312 bars/second for that particular synthetic case. Its generated price path and trade count differ from the complete CLI fixture. **Do not subtract the two timings to claim an exact reporting or startup overhead.** A separate `--version` process baseline is included in the JSON without being subtracted from either result. Browser rendering and HTTP service latency are outside both measurements.

The SMA comparison demonstrates the local cost of recomputing a window versus maintaining a rolling sum. It compares equivalent arithmetic kernels, not historical versions of the whole trading engine. The report checks their output checksums and alternates timing order. It does not measure investment performance, signal quality or a live broker's latency.

Peak process memory was not measured. Event counts or estimates based on algorithm/storage size must not be described as observed resident-memory usage.

Reproduce the measurement after building the project:

```powershell
python scripts/benchmark.py --engine build/bin/Release/trading_bot.exe --core build/bin/Release/benchmark_engine.exe --strategy VWAP_OPENING --repetitions 5 --build-dir build --output docs/benchmark-results.json
```

The core executable can also run by itself:

```powershell
build/bin/Release/benchmark_engine.exe --bars 39000 --repetitions 5 --strategy VWAP_OPENING
```

No external data or network request is used. The Python wrapper writes the report only when `--output` is supplied; rerunning it replaces that report with the new measurements. Adjust executable paths for other CMake generators or platforms.
