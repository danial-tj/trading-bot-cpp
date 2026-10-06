"""Reproducible synthetic daily OHLCV, not historical market observations."""
from datetime import date, timedelta
import csv
import math
from pathlib import Path

def generate(path):
    day = date(2024, 1, 2)
    previous = 100.0
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        for i in range(360):
            while day.weekday() >= 5:
                day += timedelta(days=1)
            opening = previous + .12 * math.sin(i * .7)
            closing = 100 + i * .025 + 8 * math.sin(i * .09) + 2 * math.sin(i * .23)
            writer.writerow([day.isoformat(), f'{opening:.2f}', f'{max(opening, closing) + .7:.2f}',
                             f'{min(opening, closing) - .6:.2f}', f'{closing:.2f}', 100000 + (i % 17) * 7000])
            previous = closing
            day += timedelta(days=1)

if __name__ == '__main__':
    generate(Path(__file__).resolve().parents[1] / 'data' / 'daily_demo.csv')
