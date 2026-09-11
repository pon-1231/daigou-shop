"""Core data types: candles, timeframes, resampling.

Pure stdlib on purpose - this engine must run anywhere without a pip install.
All timestamps are timezone-aware UTC internally; display/session logic converts
to New York time because every ICT concept is anchored to the NY session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

UTC = timezone.utc


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. `ts` is the bar's OPEN time, timezone-aware UTC."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("Candle.ts must be timezone-aware")
        if self.high < self.low:
            raise ValueError(f"high < low at {self.ts}")

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_up(self) -> bool:
        return self.close > self.open

    @property
    def is_down(self) -> bool:
        return self.close < self.open

    @property
    def body_ratio(self) -> float:
        """Body as a fraction of total range. Displacement candles run high."""
        r = self.range
        return self.body / r if r > 0 else 0.0

    def midpoint(self) -> float:
        """Consequent encroachment when applied to a gap; equilibrium of a range."""
        return (self.high + self.low) / 2.0


# --- Timeframes -------------------------------------------------------------

TIMEFRAMES: dict[str, int] = {
    "M1": 1,
    "M3": 3,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
    "W1": 10080,
}


def tf_minutes(tf: str) -> int:
    key = tf.upper()
    if key not in TIMEFRAMES:
        raise KeyError(f"unknown timeframe {tf!r}; known: {sorted(TIMEFRAMES)}")
    return TIMEFRAMES[key]


def _bucket_start(ts: datetime, minutes: int, day_anchor_utc_minutes: int) -> datetime:
    """Return the opening timestamp of the bucket `ts` belongs to.

    Intraday buckets align to midnight UTC. Daily and above align to
    `day_anchor_utc_minutes`, the minutes-past-UTC-midnight at which the trading
    day rolls (17:00 New York for the CME gold session).
    """
    if minutes < 1440:
        epoch_min = int(ts.timestamp() // 60)
        return datetime.fromtimestamp((epoch_min // minutes) * minutes * 60, UTC)
    shifted = ts - timedelta(minutes=day_anchor_utc_minutes)
    epoch_min = int(shifted.timestamp() // 60)
    start = datetime.fromtimestamp((epoch_min // minutes) * minutes * 60, UTC)
    return start + timedelta(minutes=day_anchor_utc_minutes)


def resample(
    candles: Sequence[Candle],
    target_tf: str,
    *,
    day_anchor_utc_minutes: int = 21 * 60,
) -> list[Candle]:
    """Aggregate lower-timeframe candles into `target_tf`.

    The default day anchor (21:00 UTC) is 17:00 New York during EST. Feed a
    config-derived value when you care about DST exactness on the daily chart.
    """
    minutes = tf_minutes(target_tf)
    out: list[Candle] = []
    cur_start: datetime | None = None
    o = h = l = c = 0.0
    vol = 0.0
    for candle in candles:
        start = _bucket_start(candle.ts, minutes, day_anchor_utc_minutes)
        if cur_start is None or start != cur_start:
            if cur_start is not None:
                out.append(Candle(cur_start, o, h, l, c, vol))
            cur_start = start
            o, h, l, c, vol = candle.open, candle.high, candle.low, candle.close, candle.volume
        else:
            h = max(h, candle.high)
            l = min(l, candle.low)
            c = candle.close
            vol += candle.volume
    if cur_start is not None:
        out.append(Candle(cur_start, o, h, l, c, vol))
    return out


def align_index(htf: Sequence[Candle], ts: datetime) -> int:
    """Index of the last HTF candle that has fully CLOSED at time `ts`.

    Returns -1 when no HTF candle has closed yet. This is the single most
    important anti-lookahead guard in the whole engine: higher-timeframe bias
    may only ever be read from bars that are already in the past.
    """
    lo, hi = 0, len(htf) - 1
    best = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if htf[mid].ts < ts:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def true_range(prev_close: float | None, c: Candle) -> float:
    if prev_close is None:
        return c.range
    return max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))


class WilderATR:
    """Incremental Wilder ATR. Used for volatility-normalised thresholds."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self.value: float | None = None
        self._seed: list[float] = []
        self._prev_close: float | None = None

    def update(self, c: Candle) -> float | None:
        tr = true_range(self._prev_close, c)
        self._prev_close = c.close
        if self.value is None:
            self._seed.append(tr)
            if len(self._seed) >= self.period:
                self.value = sum(self._seed) / len(self._seed)
        else:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value
