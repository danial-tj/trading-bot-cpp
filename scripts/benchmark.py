"""Measure core C++ work separately from complete CLI wall latency. No downloads."""
import argparse
import csv
import hashlib
import json
import os
import platform
from pathlib import Path
import statistics
import subprocess
import time


def measure(command, repetitions):
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    times = []
    for _ in range(repetitions):
        start = time.perf_counter_ns()
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        times.append((time.perf_counter_ns() - start) / 1_000_000)
    return {"milliseconds": times, "median_ms": statistics.median(times), "warmup_iterations_excluded": 1}


def cmake_metadata(build_directory):
    cache = build_directory / "CMakeCache.txt"
    wanted = ("CMAKE_BUILD_TYPE:", "CMAKE_CXX_COMPILER:", "CMAKE_CXX_FLAGS:", "CMAKE_CXX_FLAGS_RELEASE:", "CMAKE_GENERATOR:", "ENABLE_SANITIZERS:")
    return [line for line in cache.read_text(encoding="utf-8").splitlines() if line.startswith(wanted)] if cache.exists() else ["CMakeCache.txt unavailable"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path, help="benchmark_engine executable")
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "opening_demo.csv")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--strategy", default="VWAP_OPENING")
    parser.add_argument("--bars", type=int, default=39000)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 30:
        parser.error("--repetitions must be 1..30")
    engine, core, data = args.engine.resolve(), args.core.resolve(), args.data.resolve()
    if not all(path.is_file() for path in (engine, core, data)):
        parser.error("engine, core benchmark and dataset must exist")
    core_command = [str(core), "--bars", str(args.bars), "--repetitions", str(args.repetitions), "--strategy", args.strategy]
    core_report = json.loads(subprocess.run(core_command, check=True, capture_output=True, text=True).stdout)
    cli_command = [str(engine), "--data", str(data), "--strategy", args.strategy]
    if args.config:
        cli_command += ["--config", str(args.config.resolve())]
    # Provenance collection is an additional untimed run, separate from the timed runs/warmup.
    provenance = json.loads(subprocess.run(cli_command, check=True, capture_output=True, text=True).stdout)
    cli_report = measure(cli_command, args.repetitions)
    startup_report = measure([str(engine), "--version"], args.repetitions)
    with data.open(newline="", encoding="utf-8-sig") as stream:
        input_rows = max(0, sum(1 for _ in csv.reader(stream)) - 1)
    report = {
        "purpose": "Measured local synthetic performance; not an investment result or a production capacity promise",
        "environment": {
            "platform": platform.platform(), "machine": platform.machine(),
            "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unavailable"),
            "logical_cpus": os.cpu_count(), "python": platform.python_version(),
            "engine_executable_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
            "core_executable_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
            "engine_source_sha256": provenance.get("engine_source_sha256"),
            "engine_version": provenance.get("engine_version"),
            "engine_commit": provenance.get("engine_commit"),
            "provenance_collection": "additional untimed CLI run; hashes identify the measured executables and their compiled source identity",
            "cmake": cmake_metadata(args.build_dir.resolve() if args.build_dir else engine.parent.parent),
            "arguments": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        },
        "core_in_memory": core_report,
        "complete_cli": {
            **cli_report, "input_rows": input_rows, "dataset_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
            "includes": "process launch, configuration, CSV read/validation, backtest, full JSON construction/serialization; stdout discarded",
            "excludes": "HTTP service, browser rendering, report file storage",
        },
        "version_process_baseline": {
            **startup_report, "includes": "process launch and --version handling; not subtracted from other measurements",
        },
        "comparison_limit": "Core uses generated in-memory data; CLI uses the named dataset. Do not subtract their timings to infer exact report overhead. Use each as its labeled measurement.",
    }
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
