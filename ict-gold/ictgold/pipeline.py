"""THE PIPE - a staged, auditable ICT entry pipeline.

Seven stages, evaluated in strict order. Every stage can veto, and every stage
records *why* it passed or vetoed. The trace is as important as the signal:
a setup that never fires is a bug you cannot find without it, and the trace is
what the future TradingView dashboard renders.

    1. TIME       killzone / day-of-week / macro window
    2. BIAS       higher-timeframe draw on liquidity
    3. LIQUIDITY  has a stop pool just been swept and rejected?
    4. STRUCTURE  did the market then shift character with displacement?
    5. PD ARRAY   where is the discounted entry inside that leg? (FVG/OB/OTE)
    6. RISK       stop, target, R:R, position size, hard sanity limits
    7. SCORE      weighted confluence gate

Stage order is not cosmetic. Liquidity BEFORE structure encodes the actual
sequence ICT describes: stops get taken first, the shift comes second. Swap
them and you are trading a different, worse idea.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .config import Config, SetupSpec
from .sessions import KILLZONES, day_of_week, in_macro
from .state import BEAR, BULL, MarketModel, Sweep


@dataclass
class Bias:
    direction: str | None  # BULL | BEAR | None
    strength: float  # 0..1
    reason: str
    draw: float | None = None  # HTF target price, if one is standing


@dataclass
class EntryZone:
    kind: str  # fvg | ob | ote
    top: float
    bottom: float
    entry: float
    fresh: bool
    ref: object | None = None

    @property
    def ce(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class Signal:
    ts: datetime
    bar_idx: int
    setup: str
    direction: str
    entry: float
    stop: float
    target: float
    rr: float
    score: float
    lots: float
    risk_usd: float
    expires_bar: int
    evidence: dict = field(default_factory=dict)

    @property
    def side(self) -> str:
        return "LONG" if self.direction == BULL else "SHORT"

    def to_dict(self) -> dict:
        d = {
            "ts": self.ts.isoformat(),
            "setup": self.setup,
            "side": self.side,
            "entry": round(self.entry, 2),
            "stop": round(self.stop, 2),
            "target": round(self.target, 2),
            "rr": round(self.rr, 2),
            "score": round(self.score, 3),
            "lots": self.lots,
            "risk_usd": round(self.risk_usd, 2),
        }
        d["evidence"] = self.evidence
        return d


@dataclass
class StageOutcome:
    stage: str
    passed: bool
    reason: str
    data: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    ts: datetime
    setup: str
    trace: list[StageOutcome]
    signal: Signal | None = None

    @property
    def veto_stage(self) -> str | None:
        for o in self.trace:
            if not o.passed:
                return o.stage
        return None


# --- scoring weights --------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, float] = {
    "sweep_strength": 0.20,
    "displacement": 0.15,
    "choch": 0.10,
    "zone_depth": 0.15,
    "killzone": 0.10,
    "rr": 0.15,
    "htf_bias": 0.10,
    "freshness": 0.05,
}

# Killzone quality prior. Start flat-ish, then REPLACE these with the per-window
# expectancy your own backtest measures. That replacement is the training loop.
KILLZONE_QUALITY: dict[str, float] = {
    "sb_am": 1.0,
    "ny_am": 0.9,
    "london": 0.85,
    "sb_london": 0.85,
    "sb_pm": 0.7,
    "london_close": 0.6,
    "ny_pm": 0.55,
    "asia": 0.3,
}


def compute_bias(htf: MarketModel) -> Bias:
    """Higher-timeframe read: where is price being drawn to, and from where?"""
    if not htf.bars or htf.trend is None:
        return Bias(None, 0.0, "htf: no structure yet")

    direction = htf.trend
    price = htf.bars[-1].close
    strength = 0.45
    notes = [f"htf trend={direction}"]

    dr = htf.dealing_range()
    if dr is not None:
        zone = dr.zone(price)
        notes.append(f"htf {zone} ({dr.retracement(price):.2f})")
        # Buying a discount in an uptrend is the whole game; buying a premium
        # in an uptrend is how accounts die slowly.
        if (direction == BULL and zone == "discount") or (direction == BEAR and zone == "premium"):
            strength += 0.25
        elif (direction == BULL and zone == "premium") or (direction == BEAR and zone == "discount"):
            strength -= 0.15

    recent = htf.recent_sweeps(within=3)
    for s in recent:
        if s.direction == direction:
            strength += 0.15
            notes.append(f"htf swept {s.pool.source}")
            break

    last = htf.events[-1] if htf.events else None
    if last is not None and htf.i - last.idx <= 3 and last.displacement:
        strength += 0.1
        notes.append(f"htf {last.kind} w/ displacement")

    draw_pool = htf.draw_on_liquidity(direction)
    return Bias(direction, max(0.0, min(1.0, strength)), "; ".join(notes),
                draw_pool.price if draw_pool else None)


# --- pipeline ---------------------------------------------------------------


@dataclass
class PipelineContext:
    ts: datetime
    cfg: Config
    setup: SetupSpec
    ltf: MarketModel
    htf: MarketModel
    equity: float
    mtf: MarketModel | None = None
    bias: Bias | None = None
    direction: str | None = None
    sweep: Sweep | None = None
    event: object | None = None
    zone: EntryZone | None = None
    parts: dict = field(default_factory=dict)  # per-factor scores, 0..1


Stage = Callable[[PipelineContext], StageOutcome]


def stage_time(ctx: PipelineContext) -> StageOutcome:
    s = ctx.setup
    dow = day_of_week(ctx.ts)
    if dow not in ctx.cfg.trade_days:
        return StageOutcome("time", False, f"day {dow} not traded")
    active = [k for k in s.killzones if k in KILLZONES and KILLZONES[k].contains(ctx.ts)]
    if not active:
        return StageOutcome("time", False, f"outside killzones {s.killzones}")
    if s.require_macro and not in_macro(ctx.ts):
        return StageOutcome("time", False, "outside macro window")
    ctx.parts["killzone"] = max(KILLZONE_QUALITY.get(k, 0.5) for k in active)
    return StageOutcome("time", True, f"in {'+'.join(active)} on {dow}", {"killzones": active})


def stage_bias(ctx: PipelineContext) -> StageOutcome:
    bias = compute_bias(ctx.htf)
    # Intermediate timeframe acts as a veto-by-degradation, not a hard gate:
    # when M15 structure opposes the H4 read we are early, not wrong.
    if ctx.mtf is not None and ctx.mtf.trend is not None and bias.direction is not None:
        if ctx.mtf.trend != bias.direction:
            bias = Bias(bias.direction, max(0.0, bias.strength - 0.2),
                        bias.reason + "; mtf opposed", bias.draw)
        else:
            bias = Bias(bias.direction, min(1.0, bias.strength + 0.05),
                        bias.reason + "; mtf aligned", bias.draw)
    ctx.bias = bias
    ctx.parts["htf_bias"] = bias.strength
    if ctx.setup.align_with_htf_bias and bias.direction is None:
        return StageOutcome("bias", False, "no htf bias")
    return StageOutcome("bias", True, bias.reason,
                        {"direction": bias.direction, "strength": round(bias.strength, 2),
                         "draw": bias.draw})


def stage_liquidity(ctx: PipelineContext) -> StageOutcome:
    s = ctx.setup
    if not s.require_sweep:
        ctx.direction = ctx.bias.direction if ctx.bias else None
        ctx.parts["sweep_strength"] = 0.0
        if ctx.direction is None:
            return StageOutcome("liquidity", False, "no direction without sweep or bias")
        return StageOutcome("liquidity", True, "sweep not required by setup")

    candidates = [
        sw for sw in ctx.ltf.recent_sweeps(s.sweep_lookback)
        if sw.pool.strength >= s.min_pool_strength
    ]
    if not candidates:
        return StageOutcome("liquidity", False,
                            f"no sweep (strength>={s.min_pool_strength}) in last {s.sweep_lookback} bars")

    sweep = max(candidates, key=lambda sw: (sw.pool.strength, sw.idx))
    ctx.sweep = sweep
    ctx.direction = sweep.direction
    ctx.parts["sweep_strength"] = min(1.0, sweep.pool.strength / 4.0)

    bias_dir = ctx.bias.direction if ctx.bias else None
    if s.align_with_htf_bias and bias_dir is not None and sweep.direction != bias_dir:
        if not (s.allow_counter_trend_on_sweep and sweep.pool.strength >= 3):
            return StageOutcome("liquidity", False,
                                f"sweep {sweep.direction} fights htf bias {bias_dir}")
    return StageOutcome("liquidity", True,
                        f"swept {sweep.pool.source} @ {sweep.pool.price:.2f}",
                        {"pool": sweep.pool.source, "price": round(sweep.pool.price, 2),
                         "strength": sweep.pool.strength, "bars_ago": ctx.ltf.i - sweep.idx})


def stage_structure(ctx: PipelineContext) -> StageOutcome:
    s = ctx.setup
    if not s.require_structure:
        ctx.parts.setdefault("displacement", 0.0)
        ctx.parts.setdefault("choch", 0.0)
        return StageOutcome("structure", True, "structure not required by setup")

    events = [
        e for e in ctx.ltf.recent_events(s.structure_lookback)
        if e.direction == ctx.direction and e.kind in s.structure_kinds
    ]
    if ctx.sweep is not None:
        # Sequence matters: the shift must come AFTER the stop run.
        events = [e for e in events if e.idx >= ctx.sweep.idx]
    if not events:
        return StageOutcome("structure", False,
                            f"no {'/'.join(s.structure_kinds)} {ctx.direction} in last "
                            f"{s.structure_lookback} bars")
    event = events[-1]
    if s.require_displacement and not event.displacement:
        return StageOutcome("structure", False, f"{event.kind} without displacement")

    ctx.event = event
    ctx.parts["displacement"] = 1.0 if event.displacement else 0.0
    ctx.parts["choch"] = 1.0 if event.kind == "CHOCH" else 0.5
    return StageOutcome("structure", True, f"{event.kind} {event.direction} @ {event.level:.2f}",
                        {"kind": event.kind, "level": round(event.level, 2),
                         "displacement": event.displacement,
                         "bars_ago": ctx.ltf.i - event.idx})


def _impulse_range(ctx: PipelineContext) -> tuple[float, float, int]:
    """Low, high and start index of the leg we intend to retrace into."""
    m = ctx.ltf
    start = getattr(ctx.event, "leg_start_idx", None)
    if start is None:
        start = ctx.sweep.idx if ctx.sweep else max(0, m.i - 20)
    if ctx.sweep is not None:
        start = min(start, ctx.sweep.idx)
    seg = m.bars[start : m.i + 1]
    if not seg:
        b = m.bars[-1]
        return b.low, b.high, m.i
    return min(b.low for b in seg), max(b.high for b in seg), start


def stage_pd_array(ctx: PipelineContext) -> StageOutcome:
    s = ctx.setup
    m = ctx.ltf
    price = m.bars[-1].close
    lo, hi, start = _impulse_range(ctx)
    span = hi - lo
    if span <= 0:
        return StageOutcome("pd_array", False, "degenerate impulse range")
    mid = (hi + lo) / 2.0
    after = ctx.event.idx if ctx.event is not None else start

    zones: list[EntryZone] = []

    if "fvg" in s.entry_pd:
        for f in m.live_fvgs(ctx.direction):
            if f.idx < after or f.touched_idx is not None:
                continue
            entry = {"ce": f.ce, "proximal": (f.top if ctx.direction == BULL else f.bottom),
                     "distal": (f.bottom if ctx.direction == BULL else f.top)}[s.entry_fill]
            zones.append(EntryZone("fvg", f.top, f.bottom, entry, fresh=True, ref=f))

    if "ob" in s.entry_pd:
        for ob in m.live_obs(ctx.direction):
            if ob.idx < start or ob.mitigated_idx is not None:
                continue
            entry = {"ce": ob.ce, "proximal": (ob.top if ctx.direction == BULL else ob.bottom),
                     "distal": (ob.bottom if ctx.direction == BULL else ob.top)}[s.entry_fill]
            zones.append(EntryZone("ob", ob.top, ob.bottom, entry, fresh=True, ref=ob))

    if "ote" in s.entry_pd:
        if ctx.direction == BULL:
            z_hi = hi - span * s.ote_low
            z_lo = hi - span * s.ote_high
        else:
            z_lo = lo + span * s.ote_low
            z_hi = lo + span * s.ote_high
        zones.append(EntryZone("ote", max(z_hi, z_lo), min(z_hi, z_lo),
                               (z_hi + z_lo) / 2.0, fresh=True))

    if not zones:
        return StageOutcome("pd_array", False, f"no fresh {'/'.join(s.entry_pd)} in the leg")

    # Entry must still be in front of price: we are placing a limit into a
    # retracement, never chasing an extension.
    if ctx.direction == BULL:
        zones = [z for z in zones if z.entry < price]
    else:
        zones = [z for z in zones if z.entry > price]
    if not zones:
        return StageOutcome("pd_array", False, "all PD arrays already behind price")

    if s.require_zone != "any":
        want_discount = (s.require_zone == "discount") == (ctx.direction == BULL)
        zones = [z for z in zones if (z.entry < mid) == want_discount]
        if not zones:
            return StageOutcome("pd_array", False,
                                f"no PD array in {s.require_zone} of the leg")

    # Nearest to price = highest probability of actually being reached.
    zone = min(zones, key=lambda z: abs(z.entry - price))
    ctx.zone = zone

    depth = (mid - zone.entry) / (span / 2.0) if ctx.direction == BULL else (zone.entry - mid) / (span / 2.0)
    ctx.parts["zone_depth"] = max(0.0, min(1.0, depth))
    ctx.parts["freshness"] = 1.0 if zone.fresh else 0.4
    return StageOutcome("pd_array", True,
                        f"{zone.kind} entry {zone.entry:.2f} ({zone.bottom:.2f}-{zone.top:.2f})",
                        {"kind": zone.kind, "entry": round(zone.entry, 2),
                         "top": round(zone.top, 2), "bottom": round(zone.bottom, 2),
                         "leg": [round(lo, 2), round(hi, 2)]})


def stage_risk(ctx: PipelineContext) -> StageOutcome:
    s = ctx.setup
    m = ctx.ltf
    zone = ctx.zone
    assert zone is not None
    atr = m.atr_value or 0.0
    if atr <= 0:
        return StageOutcome("risk", False, "atr not warmed up")

    buf = atr * s.stop_buffer_atr
    lo, hi, start = _impulse_range(ctx)
    if s.stop_from == "sweep_extreme" and ctx.sweep is not None:
        anchor = m.bars[ctx.sweep.idx]
        raw = anchor.low if ctx.direction == BULL else anchor.high
    elif s.stop_from == "structure":
        raw = lo if ctx.direction == BULL else hi
    else:  # pd_distal
        raw = zone.bottom if ctx.direction == BULL else zone.top
    stop = raw - buf if ctx.direction == BULL else raw + buf

    entry = zone.entry
    dist = (entry - stop) if ctx.direction == BULL else (stop - entry)
    if dist <= 0:
        return StageOutcome("risk", False, "stop on the wrong side of entry")
    if dist < atr * ctx.cfg.risk.min_stop_atr:
        return StageOutcome("risk", False, f"stop too tight ({dist:.2f} < {atr * ctx.cfg.risk.min_stop_atr:.2f})")
    if dist > atr * ctx.cfg.risk.max_stop_atr:
        return StageOutcome("risk", False, f"stop too wide ({dist:.2f} > {atr * ctx.cfg.risk.max_stop_atr:.2f})")
    round_trip = ctx.cfg.costs.spread + ctx.cfg.costs.slippage_entry + ctx.cfg.costs.slippage_stop
    min_by_cost = round_trip * ctx.cfg.risk.min_stop_cost_mult
    if dist < min_by_cost:
        return StageOutcome("risk", False,
                            f"stop {dist:.2f} inside the cost floor {min_by_cost:.2f} "
                            f"({ctx.cfg.risk.min_stop_cost_mult}x round-trip friction)")

    max_dist = atr * ctx.cfg.risk.target_max_atr
    if s.target_mode == "draw":
        pool = m.draw_on_liquidity(ctx.direction, max_distance=max_dist)
        target = pool.price if pool is not None else (
            entry + dist * s.fixed_rr if ctx.direction == BULL else entry - dist * s.fixed_rr)
        if ctx.bias and ctx.bias.draw is not None and abs(ctx.bias.draw - entry) <= max_dist:
            # Extend to the HTF objective only when it is still inside the
            # distance price can realistically cover before we go flat.
            if (ctx.direction == BULL and ctx.bias.draw > target) or (
                ctx.direction == BEAR and ctx.bias.draw < target):
                target = ctx.bias.draw
    else:
        target = entry + dist * s.fixed_rr if ctx.direction == BULL else entry - dist * s.fixed_rr

    reward = (target - entry) if ctx.direction == BULL else (entry - target)
    if reward <= 0:
        return StageOutcome("risk", False, "target behind entry")
    if reward > max_dist:
        reward = max_dist
        target = entry + reward if ctx.direction == BULL else entry - reward
    rr = reward / dist
    if rr < s.min_rr:
        return StageOutcome("risk", False, f"rr {rr:.2f} < min {s.min_rr}")
    if rr > s.max_rr:
        rr = s.max_rr
        target = entry + dist * rr if ctx.direction == BULL else entry - dist * rr

    risk_usd = ctx.equity * ctx.cfg.risk.risk_per_trade_pct / 100.0
    per_lot = dist * ctx.cfg.symbol.contract_size
    lots = ctx.cfg.symbol.round_lots(risk_usd / per_lot) if per_lot > 0 else 0.0
    if lots < ctx.cfg.symbol.min_lot:
        return StageOutcome("risk", False, "position size rounds to zero")

    if s.target_mode == "draw":
        # Draw-on-liquidity targets float, so "how far past the minimum" is
        # real information about how good this particular setup is.
        ctx.parts["rr"] = max(0.0, min(1.0, (rr - s.min_rr) / 3.0))
    else:
        # Fixed target_mode ("rr") makes rr == min_rr by construction on
        # every signal that reaches this line (anything less already vetoed
        # above) - the "how far past the minimum" formula would silently
        # score every trade 0.0 here and quietly drag the whole confluence
        # score down by this factor's weight for no market reason. Full
        # credit for clearing the fixed bar is the honest reading.
        ctx.parts["rr"] = 1.0
    ctx.parts["_final"] = {"entry": entry, "stop": stop, "target": target, "rr": rr,
                           "lots": lots, "risk_usd": risk_usd, "atr": atr}
    return StageOutcome("risk", True,
                        f"entry {entry:.2f} stop {stop:.2f} target {target:.2f} rr {rr:.2f} lots {lots}",
                        {"entry": round(entry, 2), "stop": round(stop, 2),
                         "target": round(target, 2), "rr": round(rr, 2), "lots": lots})


def stage_score(ctx: PipelineContext, weights: dict[str, float]) -> StageOutcome:
    total = 0.0
    used = 0.0
    detail = {}
    for key, w in weights.items():
        if key in ctx.parts:
            v = float(ctx.parts[key])
            total += w * v
            used += w
            detail[key] = round(v, 2)
    score = total / used if used > 0 else 0.0
    if score < ctx.setup.min_score:
        return StageOutcome("score", False, f"score {score:.2f} < {ctx.setup.min_score}", detail)
    ctx.parts["_score"] = score
    return StageOutcome("score", True, f"score {score:.2f}", detail)


class EntryPipeline:
    """Runs every enabled setup at one bar close and returns the best signal."""

    def __init__(self, cfg: Config, weights: dict[str, float] | None = None) -> None:
        self.cfg = cfg
        self.weights = dict(weights or DEFAULT_WEIGHTS)

    def evaluate_setup(self, setup: SetupSpec, ltf: MarketModel, htf: MarketModel,
                       equity: float, mtf: MarketModel | None = None) -> PipelineResult:
        ts = ltf.bars[-1].ts
        ctx = PipelineContext(ts=ts, cfg=self.cfg, setup=setup, ltf=ltf, htf=htf,
                              equity=equity, mtf=mtf)
        trace: list[StageOutcome] = []
        for stage in (stage_time, stage_bias, stage_liquidity, stage_structure,
                      stage_pd_array, stage_risk):
            outcome = stage(ctx)
            trace.append(outcome)
            if not outcome.passed:
                return PipelineResult(ts, setup.name, trace)
        outcome = stage_score(ctx, self.weights)
        trace.append(outcome)
        if not outcome.passed:
            return PipelineResult(ts, setup.name, trace)

        f = ctx.parts["_final"]
        signal = Signal(
            ts=ts,
            bar_idx=ltf.i,
            setup=setup.name,
            direction=ctx.direction or BULL,
            entry=f["entry"],
            stop=f["stop"],
            target=f["target"],
            rr=f["rr"],
            score=ctx.parts["_score"],
            lots=f["lots"],
            risk_usd=f["risk_usd"],
            expires_bar=ltf.i + setup.order_valid_bars,
            evidence={o.stage: {"reason": o.reason, **o.data} for o in trace},
        )
        return PipelineResult(ts, setup.name, trace, signal)

    def run(self, ltf: MarketModel, htf: MarketModel, equity: float,
            mtf: MarketModel | None = None) -> list[PipelineResult]:
        return [
            self.evaluate_setup(s, ltf, htf, equity, mtf)
            for s in self.cfg.setups if s.enabled
        ]

    def best(self, ltf: MarketModel, htf: MarketModel, equity: float,
             mtf: MarketModel | None = None) -> tuple[Signal | None, list[PipelineResult]]:
        results = self.run(ltf, htf, equity, mtf)
        signals = [r.signal for r in results if r.signal is not None]
        if not signals:
            return None, results
        best = max(signals, key=lambda s: s.score * next(
            (x.weight for x in self.cfg.setups if x.name == s.setup), 1.0))
        return best, results
