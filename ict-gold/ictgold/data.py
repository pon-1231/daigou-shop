"""Data loading and a synthetic generator for smoke tests.

CSV NOTE, and this matters more than anything else in this file: most retail
gold data ships in *broker* time (often UTC+2/+3), not UTC. Load it as UTC by
mistake and every killzone in your backtest is shifted by hours - the results
will look like a strategy result but they are a clock bug. Always pass
`source_tz` and then eyeball one known session open before trusting anything.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import UTC, Candle
from .sessions import NY

_TS_KEYS = ("time", "timestamp", "date", "datetime", "ts", "<date>", "gmt time")
_FMTS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%d.%m.%Y %H:%M:%S.%f",
    "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
)


def _parse_ts(raw: str, source_tz: str) -> datetime:
    raw = raw.strip().replace("Z", "+00:00")
    if raw.isdigit():
        v = int(raw)
        if v > 10_000_000_000:  # milliseconds
            v //= 1000
        return datetime.fromtimestamp(v, UTC)
    tz = UTC if source_tz.upper() == "UTC" else ZoneInfo(source_tz)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = None
        for fmt in _FMTS:
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            raise ValueError(f"unparseable timestamp: {raw!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)


def load_csv(path: str | Path, *, source_tz: str = "UTC") -> list[Candle]:
    """Read an OHLCV csv. Column names are matched case-insensitively."""
    rows: list[Candle] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return []
        lower = {(f or "").strip().lower(): f for f in reader.fieldnames}
        ts_key = next((lower[k] for k in _TS_KEYS if k in lower), None)
        date_key = lower.get("date")
        time_key = lower.get("time")
        if ts_key is None and not (date_key and time_key):
            raise ValueError(f"no timestamp column in {reader.fieldnames}")

        def col(*names: str) -> str:
            for n in names:
                if n in lower:
                    return lower[n]
            raise ValueError(f"missing column {names} in {reader.fieldnames}")

        o, h, l, c = col("open", "o", "<open>"), col("high", "h", "<high>"), col("low", "l", "<low>"), col("close", "c", "<close>")
        vkey = lower.get("volume") or lower.get("vol") or lower.get("tickvol")
        for row in reader:
            if date_key and time_key and ts_key in (date_key, None):
                raw_ts = f"{row[date_key]} {row[time_key]}"
            else:
                raw_ts = row[ts_key]
            try:
                rows.append(Candle(
                    _parse_ts(raw_ts, source_tz),
                    float(row[o]), float(row[h]), float(row[l]), float(row[c]),
                    float(row[vkey]) if vkey and row.get(vkey) else 0.0,
                ))
            except (ValueError, TypeError):
                continue  # header repeats / blank lines
    rows.sort(key=lambda x: x.ts)
    return rows


def save_csv(candles: list[Candle], path: str | Path) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.ts.isoformat(), f"{c.open:.2f}", f"{c.high:.2f}",
                        f"{c.low:.2f}", f"{c.close:.2f}", f"{c.volume:.0f}"])


# --- synthetic data ---------------------------------------------------------

# Volatility multipliers by New York hour. Gold is dead in the Asian afternoon
# and violent at the London and New York opens; a flat-vol random walk would
# make the killzone logic look meaningless for the wrong reason.
_HOUR_VOL = {
    0: 0.6, 1: 0.6, 2: 1.3, 3: 1.5, 4: 1.3, 5: 1.0, 6: 0.9, 7: 1.2,
    8: 1.8, 9: 1.7, 10: 1.6, 11: 1.1, 12: 0.9, 13: 1.1, 14: 1.3, 15: 1.0,
    16: 0.5, 17: 0.4, 18: 0.5, 19: 0.6, 20: 0.7, 21: 0.7, 22: 0.6, 23: 0.6,
}


def synthetic_m5(
    bars: int = 20_000,
    start_price: float = 2350.0,
    seed: int = 7,
    start: datetime | None = None,
    sweep_prob: float = 0.30,
) -> list[Candle]:
    """Generate M5 candles with session volatility and engineered stop-runs.

    THIS IS A SMOKE TEST FIXTURE, NOT A MARKET. Any equity curve produced from
    it measures whether the code runs, never whether the edge is real. The
    sweep injections are literally the pattern the strategy looks for, so of
    course it finds them. Never quote these numbers as performance.
    """
    rng = random.Random(seed)
    ts = start or datetime(2024, 1, 1, 22, 0, tzinfo=UTC)
    price = start_price
    out: list[Candle] = []
    recent_high = price
    recent_low = price
    pending_reversal = 0
    rev_dir = 0

    while len(out) < bars:
        ny = ts.astimezone(NY)
        if ny.weekday() >= 5:  # weekend: market shut
            ts += timedelta(minutes=5)
            continue
        # Scaled so M5 ATR lands near 1.8, which is where real XAUUSD sits.
        vol = 1.45 * _HOUR_VOL.get(ny.hour, 1.0)
        drift = 0.0

        # At a killzone open, occasionally run the recent extreme then reverse.
        if ny.hour in (2, 8, 10) and ny.minute == 0 and rng.random() < sweep_prob:
            rev_dir = 1 if rng.random() < 0.5 else -1
            pending_reversal = rng.randint(6, 14)
            # push through the extreme first (the Judas move)
            target = (recent_high + 1.2 * vol) if rev_dir < 0 else (recent_low - 1.2 * vol)
            drift = (target - price) / 3.0

        if pending_reversal > 0:
            pending_reversal -= 1
            if pending_reversal < 9:
                drift += rev_dir * vol * 0.55

        o = price
        step = rng.gauss(drift, vol)
        c = o + step
        wick = abs(rng.gauss(0, vol * 0.6))
        h = max(o, c) + wick * rng.random()
        l = min(o, c) - wick * rng.random()
        out.append(Candle(ts, round(o, 2), round(h, 2), round(l, 2), round(c, 2),
                          round(abs(step) * 100 + 50)))
        price = c
        recent_high = max(h, recent_high * 0.999 + h * 0.001)
        recent_low = min(l, recent_low * 0.999 + l * 0.001)
        if len(out) % 288 == 0:  # daily reset of the extremes
            window = out[-288:]
            recent_high = max(b.high for b in window)
            recent_low = min(b.low for b in window)
        ts += timedelta(minutes=5)
    return out
