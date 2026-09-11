"""Incremental ICT market model.

Feed it closed candles one at a time; it maintains swings, market structure,
fair value gaps, order blocks and liquidity pools in a single O(n) pass.

The whole module obeys one rule: **a fact may only exist on the bar at which it
became knowable in real time.** A fractal swing at index i is not confirmed
until i+n bars have printed, so it is published at i+n, not i. Every backtest
result in this repo is worthless without that discipline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .core import Candle, WilderATR
from .sessions import RANGE_WINDOWS, trading_day

BULL = "bullish"
BEAR = "bearish"


@dataclass
class Swing:
    idx: int
    ts: datetime
    price: float
    kind: str  # "high" | "low"
    confirmed_idx: int
    broken_idx: int | None = None


@dataclass
class StructureEvent:
    idx: int
    ts: datetime
    kind: str  # "BOS" (continuation) | "CHOCH" (character change)
    direction: str  # BULL | BEAR
    level: float  # the swing price that was broken
    displacement: bool  # did the breaking leg leave an FVG behind?
    leg_start_idx: int


@dataclass
class FVG:
    """Fair value gap / imbalance: a 3-candle inefficiency."""

    idx: int  # index of the third candle
    ts: datetime
    direction: str  # BULL = gap below price, a discount to buy into
    top: float
    bottom: float
    touched_idx: int | None = None
    ce_tagged_idx: int | None = None  # consequent encroachment (50%) reached
    filled_idx: int | None = None  # fully traded through
    invalidated_idx: int | None = None  # closed beyond the far side

    @property
    def ce(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def is_live(self) -> bool:
        return self.filled_idx is None and self.invalidated_idx is None

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass
class OrderBlock:
    idx: int
    ts: datetime
    direction: str  # BULL = demand zone built from the last down candle
    top: float
    bottom: float
    origin_event_idx: int
    mitigated_idx: int | None = None
    invalidated_idx: int | None = None
    breaker: bool = False

    @property
    def ce(self) -> float:
        return (self.top + self.bottom) / 2.0

    def is_live(self) -> bool:
        return self.invalidated_idx is None

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass
class LiquidityPool:
    """A price level where stops rest and the market is drawn to."""

    price: float
    kind: str  # "BSL" (buy-side, above price) | "SSL" (sell-side, below)
    source: str  # swing | equal | pdh | pdl | asia_high | ...
    created_idx: int
    ts: datetime
    strength: int = 1  # equal highs/lows and session levels score higher
    swept_idx: int | None = None
    consumed_idx: int | None = None  # closed through: a run, not a sweep

    def is_live(self) -> bool:
        return self.swept_idx is None and self.consumed_idx is None


@dataclass
class Sweep:
    """Liquidity taken and rejected: wick through the pool, close back inside."""

    idx: int
    ts: datetime
    pool: LiquidityPool
    direction: str  # BULL when sell-side was swept (expect reversal up)
    excursion: float  # how far beyond the level the wick reached


@dataclass
class DealingRange:
    low: float
    high: float
    low_idx: int
    high_idx: int

    @property
    def equilibrium(self) -> float:
        return (self.low + self.high) / 2.0

    @property
    def size(self) -> float:
        return self.high - self.low

    def retracement(self, price: float) -> float:
        """0.0 at range low, 1.0 at range high."""
        if self.size <= 0:
            return 0.5
        return (price - self.low) / self.size

    def zone(self, price: float) -> str:
        r = self.retracement(price)
        if r > 0.5:
            return "premium"
        if r < 0.5:
            return "discount"
        return "equilibrium"


@dataclass
class ModelConfig:
    swing_lookback: int = 2  # fractal strength; 2 = 5-bar fractal
    atr_period: int = 14
    equal_tol_atr: float = 0.15  # how close two swings must be to count as equal
    equal_lookback: int = 40  # bars to search back for an equal partner
    displacement_body_ratio: float = 0.5
    displacement_atr_mult: float = 1.0
    ob_search_bars: int = 12  # how far back to hunt the origin candle of a leg
    max_age_bars: int = 600  # prune objects older than this to bound memory
    sweep_max_close_back: int = 1  # bars allowed to close back inside the level
    fvg_min_atr: float = 0.05  # ignore micro-gaps that are just spread noise


class MarketModel:
    """Stateful, streaming ICT analyser for one timeframe."""

    def __init__(self, cfg: ModelConfig | None = None) -> None:
        self.cfg = cfg or ModelConfig()
        self.bars: list[Candle] = []
        self.atr = WilderATR(self.cfg.atr_period)
        self.atr_value: float | None = None

        self.swings: list[Swing] = []
        self.swing_highs: list[Swing] = []
        self.swing_lows: list[Swing] = []

        self.trend: str | None = None
        self.events: list[StructureEvent] = []
        self.fvgs: list[FVG] = []
        self.order_blocks: list[OrderBlock] = []
        self.pools: list[LiquidityPool] = []
        self.sweeps: list[Sweep] = []

        # Session / daily reference levels.
        self._day: str | None = None
        self._day_high = float("-inf")
        self._day_low = float("inf")
        self.prev_day_high: float | None = None
        self.prev_day_low: float | None = None
        self._range_acc: dict[str, tuple[float, float]] = {}
        self._range_active: dict[str, bool] = {k: False for k in RANGE_WINDOWS}
        self.session_levels: dict[str, tuple[float, float]] = {}

        # Unbroken structural references.
        self._ref_high: Swing | None = None
        self._ref_low: Swing | None = None

    # -- public API ---------------------------------------------------------

    @property
    def i(self) -> int:
        """Index of the most recently processed bar."""
        return len(self.bars) - 1

    def update(self, c: Candle) -> None:
        self.bars.append(c)
        self.atr_value = self.atr.update(c)
        i = self.i
        self._roll_day(c, i)
        self._roll_session_ranges(c, i)
        self._detect_fvg(i)
        self._confirm_swings(i)
        self._update_fvg_state(i)
        self._update_pool_state(i)
        self._update_ob_state(i)
        self._detect_structure(i)
        if i % 200 == 0:
            self._prune(i)

    def dealing_range(self) -> DealingRange | None:
        """Most recent confirmed swing low/high pair, for premium/discount."""
        if not self.swing_highs or not self.swing_lows:
            return None
        h = self.swing_highs[-1]
        l = self.swing_lows[-1]
        if h.price <= l.price:
            return None
        return DealingRange(low=l.price, high=h.price, low_idx=l.idx, high_idx=h.idx)

    def live_fvgs(self, direction: str | None = None) -> list[FVG]:
        return [f for f in self.fvgs if f.is_live() and (direction is None or f.direction == direction)]

    def live_obs(self, direction: str | None = None) -> list[OrderBlock]:
        return [o for o in self.order_blocks if o.is_live() and (direction is None or o.direction == direction)]

    def live_pools(self, kind: str | None = None) -> list[LiquidityPool]:
        return [p for p in self.pools if p.is_live() and (kind is None or p.kind == kind)]

    def recent_sweeps(self, within: int) -> list[Sweep]:
        return [s for s in self.sweeps if self.i - s.idx <= within]

    def recent_events(self, within: int) -> list[StructureEvent]:
        return [e for e in self.events if self.i - e.idx <= within]

    def draw_on_liquidity(self, direction: str, max_distance: float | None = None) -> LiquidityPool | None:
        """Nearest untouched pool in the direction of travel: the objective.

        Proximity dominates. Strength only breaks ties among pools sitting
        within half an ATR of each other - otherwise a distant pile of equal
        highs becomes a magnet and every target turns into an unreachable 10R
        fantasy that the session close will take away from you.
        """
        price = self.bars[-1].close
        band = (self.atr_value or 0.0) * 0.5
        side = "BSL" if direction == BULL else "SSL"
        cands = [p for p in self.live_pools(side)
                 if (p.price > price if direction == BULL else p.price < price)]
        if max_distance is not None:
            cands = [p for p in cands if abs(p.price - price) <= max_distance]
        if not cands:
            return None
        nearest = min(cands, key=lambda p: abs(p.price - price))
        close_by = [p for p in cands if abs(p.price - price) <= abs(nearest.price - price) + band]
        return max(close_by, key=lambda p: (p.strength, -abs(p.price - price)))

    # -- internals ----------------------------------------------------------

    def _roll_day(self, c: Candle, i: int) -> None:
        day = trading_day(c.ts)
        if self._day is None:
            self._day = day
        elif day != self._day:
            if self._day_high > float("-inf"):
                self.prev_day_high = self._day_high
                self.prev_day_low = self._day_low
                self._add_pool(self._day_high, "BSL", "pdh", i, c.ts, strength=3)
                self._add_pool(self._day_low, "SSL", "pdl", i, c.ts, strength=3)
            self._day = day
            self._day_high = float("-inf")
            self._day_low = float("inf")
        self._day_high = max(self._day_high, c.high)
        self._day_low = min(self._day_low, c.low)

    def _roll_session_ranges(self, c: Candle, i: int) -> None:
        for name, win in RANGE_WINDOWS.items():
            inside = win.contains(c.ts)
            if inside:
                hi, lo = self._range_acc.get(name, (float("-inf"), float("inf")))
                self._range_acc[name] = (max(hi, c.high), min(lo, c.low))
                self._range_active[name] = True
            elif self._range_active.get(name):
                hi, lo = self._range_acc.pop(name, (float("-inf"), float("inf")))
                self._range_active[name] = False
                if hi > float("-inf"):
                    self.session_levels[name] = (hi, lo)
                    tag = name.replace("_range", "")
                    self._add_pool(hi, "BSL", f"{tag}_high", i, c.ts, strength=2)
                    self._add_pool(lo, "SSL", f"{tag}_low", i, c.ts, strength=2)

    def _detect_fvg(self, i: int) -> None:
        if i < 2:
            return
        a, c = self.bars[i - 2], self.bars[i]
        atr = self.atr_value or 0.0
        min_size = atr * self.cfg.fvg_min_atr
        if c.low > a.high and (c.low - a.high) > min_size:
            self.fvgs.append(FVG(i, c.ts, BULL, top=c.low, bottom=a.high))
        elif c.high < a.low and (a.low - c.high) > min_size:
            self.fvgs.append(FVG(i, c.ts, BEAR, top=a.low, bottom=c.high))

    def _confirm_swings(self, i: int) -> None:
        n = self.cfg.swing_lookback
        p = i - n
        if p < n:
            return
        highs = [b.high for b in self.bars[p - n : p + n + 1]]
        lows = [b.low for b in self.bars[p - n : p + n + 1]]
        bar = self.bars[p]
        if bar.high == max(highs) and highs.index(max(highs)) == n:
            s = Swing(p, bar.ts, bar.high, "high", confirmed_idx=i)
            self.swings.append(s)
            self.swing_highs.append(s)
            self._ref_high = s
            self._register_swing_liquidity(s, i)
        if bar.low == min(lows) and lows.index(min(lows)) == n:
            s = Swing(p, bar.ts, bar.low, "low", confirmed_idx=i)
            self.swings.append(s)
            self.swing_lows.append(s)
            self._ref_low = s
            self._register_swing_liquidity(s, i)

    def _register_swing_liquidity(self, s: Swing, i: int) -> None:
        """Publish the swing as a liquidity pool, upgrading equal highs/lows."""
        atr = self.atr_value or 0.0
        tol = atr * self.cfg.equal_tol_atr
        kind = "BSL" if s.kind == "high" else "SSL"
        peers = self.swing_highs if s.kind == "high" else self.swing_lows
        strength = 1
        for prev in reversed(peers[:-1]):
            if s.idx - prev.idx > self.cfg.equal_lookback:
                break
            if tol > 0 and abs(prev.price - s.price) <= tol:
                strength = 4  # relative equal highs/lows: the juiciest stop pool
                for pool in self.pools:
                    if pool.is_live() and pool.source == "swing" and abs(pool.price - prev.price) < 1e-9:
                        pool.strength = max(pool.strength, 4)
                        pool.source = "equal"
                break
        self._add_pool(s.price, kind, "equal" if strength == 4 else "swing", i, s.ts, strength)

    def _add_pool(self, price: float, kind: str, source: str, i: int, ts: datetime, strength: int = 1) -> None:
        for p in self.pools:
            if p.is_live() and p.kind == kind and abs(p.price - price) < 1e-9:
                p.strength = max(p.strength, strength)
                return
        self.pools.append(LiquidityPool(price, kind, source, i, ts, strength))

    def _update_fvg_state(self, i: int) -> None:
        c = self.bars[i]
        for f in self.fvgs:
            if not f.is_live() or f.idx >= i:
                continue
            if f.direction == BULL:
                if c.low <= f.top:
                    f.touched_idx = f.touched_idx or i
                if c.low <= f.ce:
                    f.ce_tagged_idx = f.ce_tagged_idx or i
                if c.low <= f.bottom:
                    f.filled_idx = i
                if c.close < f.bottom:
                    f.invalidated_idx = i
            else:
                if c.high >= f.bottom:
                    f.touched_idx = f.touched_idx or i
                if c.high >= f.ce:
                    f.ce_tagged_idx = f.ce_tagged_idx or i
                if c.high >= f.top:
                    f.filled_idx = i
                if c.close > f.top:
                    f.invalidated_idx = i

    def _update_pool_state(self, i: int) -> None:
        c = self.bars[i]
        for p in self.pools:
            if not p.is_live() or p.created_idx >= i:
                continue
            if p.kind == "BSL" and c.high > p.price:
                if c.close < p.price:
                    p.swept_idx = i
                    self.sweeps.append(Sweep(i, c.ts, p, BEAR, c.high - p.price))
                else:
                    p.consumed_idx = i
            elif p.kind == "SSL" and c.low < p.price:
                if c.close > p.price:
                    p.swept_idx = i
                    self.sweeps.append(Sweep(i, c.ts, p, BULL, p.price - c.low))
                else:
                    p.consumed_idx = i

    def _update_ob_state(self, i: int) -> None:
        c = self.bars[i]
        for ob in self.order_blocks:
            if not ob.is_live() or ob.idx >= i:
                continue
            if ob.direction == BULL:
                if c.low <= ob.top:
                    ob.mitigated_idx = ob.mitigated_idx or i
                if c.close < ob.bottom:
                    ob.invalidated_idx = i
            else:
                if c.high >= ob.bottom:
                    ob.mitigated_idx = ob.mitigated_idx or i
                if c.close > ob.top:
                    ob.invalidated_idx = i

    def _is_displacement_leg(self, start: int, end: int, direction: str) -> bool:
        """A real expansion leg leaves an FVG and moves more than 1 ATR."""
        atr = self.atr_value or 0.0
        has_gap = any(
            f.direction == direction and start <= f.idx <= end for f in self.fvgs
        )
        if not has_gap:
            return False
        seg = self.bars[start : end + 1]
        if not seg:
            return False
        span = max(b.high for b in seg) - min(b.low for b in seg)
        return atr <= 0 or span >= atr * self.cfg.displacement_atr_mult

    def _detect_structure(self, i: int) -> None:
        c = self.bars[i]
        # Bullish break: close above the last unbroken confirmed swing high.
        ref_h = self._ref_high
        if ref_h is not None and ref_h.broken_idx is None and c.close > ref_h.price:
            ref_h.broken_idx = i
            kind = "CHOCH" if self.trend == BEAR else "BOS"
            leg_start = self._leg_start(i, BULL)
            self._emit(StructureEvent(i, c.ts, kind, BULL, ref_h.price,
                                      self._is_displacement_leg(leg_start, i, BULL), leg_start))
            self.trend = BULL
            self._build_order_block(leg_start, i, BULL, kind)
            self._ref_high = self._next_unbroken("high")
        ref_l = self._ref_low
        if ref_l is not None and ref_l.broken_idx is None and c.close < ref_l.price:
            ref_l.broken_idx = i
            kind = "CHOCH" if self.trend == BULL else "BOS"
            leg_start = self._leg_start(i, BEAR)
            self._emit(StructureEvent(i, c.ts, kind, BEAR, ref_l.price,
                                      self._is_displacement_leg(leg_start, i, BEAR), leg_start))
            self.trend = BEAR
            self._build_order_block(leg_start, i, BEAR, kind)
            self._ref_low = self._next_unbroken("low")

    def _next_unbroken(self, kind: str) -> Swing | None:
        pool = self.swing_highs if kind == "high" else self.swing_lows
        for s in reversed(pool):
            if s.broken_idx is None:
                return s
        return None

    def _leg_start(self, i: int, direction: str) -> int:
        """Walk back to where the impulse leg began (the extreme before it)."""
        lo = max(0, i - self.cfg.ob_search_bars)
        seg = self.bars[lo : i + 1]
        if direction == BULL:
            j = min(range(len(seg)), key=lambda k: seg[k].low)
        else:
            j = max(range(len(seg)), key=lambda k: seg[k].high)
        return lo + j

    def _build_order_block(self, leg_start: int, i: int, direction: str, event_kind: str) -> None:
        """The last opposing candle before the displacement leg."""
        lo = max(0, leg_start - self.cfg.ob_search_bars)
        found: Candle | None = None
        found_idx = -1
        for k in range(min(i, len(self.bars) - 1), lo - 1, -1):
            b = self.bars[k]
            if direction == BULL and b.is_down:
                found, found_idx = b, k
                break
            if direction == BEAR and b.is_up:
                found, found_idx = b, k
                break
        if found is None:
            return
        ob = OrderBlock(
            idx=found_idx,
            ts=found.ts,
            direction=direction,
            top=found.high,
            bottom=found.low,
            origin_event_idx=i,
            breaker=(event_kind == "CHOCH"),
        )
        self.order_blocks.append(ob)

    def _emit(self, e: StructureEvent) -> None:
        self.events.append(e)

    def _prune(self, i: int) -> None:
        cutoff = i - self.cfg.max_age_bars
        if cutoff <= 0:
            return
        self.fvgs = [f for f in self.fvgs if f.is_live() or f.idx > cutoff]
        self.order_blocks = [o for o in self.order_blocks if o.is_live() or o.idx > cutoff]
        self.pools = [p for p in self.pools if p.is_live() or p.created_idx > cutoff]
        self.sweeps = [s for s in self.sweeps if s.idx > cutoff]
        self.events = [e for e in self.events if e.idx > cutoff]
