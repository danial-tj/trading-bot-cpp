"""Deterministic fictional 2-minute OHLCV; no network, no investment performance claims."""
import csv
from datetime import date, datetime, time, timedelta
from pathlib import Path


def generate(destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    day, end = date(2024, 1, 2), date(2024, 10, 1)
    price, rows = 100.0, 0
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        while day < end:
            if day.weekday() < 5:
                direction = 1 if day < date(2024, 6, 1) else -1
                opening = datetime.combine(day, time(9, 30))
                for index in range(195):
                    opening_price = price
                    body = (0.06 if index == 0 else 0.28 if index == 1 else 0.002) * direction
                    price += body
                    stamp = (opening + timedelta(minutes=2 * index)).isoformat()
                    writer.writerow([stamp, f"{opening_price:.6f}", f"{max(opening_price, price) + .02:.6f}",
                                     f"{min(opening_price, price) - .02:.6f}", f"{price:.6f}", 2000 if index < 15 else 1000])
                    rows += 1
            day += timedelta(days=1)
    return rows


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "data" / "opening_demo.csv"
    print(f"Wrote {generate(target)} fictional bars to {target}")
