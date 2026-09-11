"""Event-driven backtester.

Execution assumptions are deliberately pessimistic, because an optimistic
backtest is not a backtest, it is a sales brochure:

  * Signals are produced only at bar CLOSE and can never fill on that bar.
  * Higher-timeframe models are fed only bars that have already closed.
  * If a bar's range contains both the stop and the target, the STOP is taken.
  * Spread, entry slippage, worse stop slippage and commission all apply.
  * Breakeven and partial moves are armed on the bar AFTER the trigger.

If you later swap any of these for something friendlier, the equity curve will
improve and the strategy will not. Don't.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import Config
from .core import Candle, resample, tf_minutes
from .pipeline import EntryPipeline, PipelineResult, Signal
from .sessions import NY, day_of_week, trading_day
from .state import BULL, MarketModel


@dataclass
class Trade:
    setup: str
    direction: str
    signal_ts: datetime
    entry_ts: datetime
    exit_ts: datetime | None
    entry: float
    stop: float
    target: float
    lots: float
    risk_usd: float
    score: float
    killzone: str
    dow: str
    day: str
    exit_price: float | None = None
    exit_reason: str = ""
    pnl: float = 0.0
    r: float = 0.0
    mae_r: float = 0.0  # worst excursion against us, in R
    mfe_r: float = 0.0  # best excursion in our favour, in R
    bars_held: int = 0
    planned_rr: float = 0.0
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "setup": self.setup, "side": "LONG" if self.direction == BULL else "SHORT",
            "signal_ts": self.signal_ts.isoformat(), "entry_ts": self.entry_ts.isoformat(),
            "exit_ts": self.exit_ts.isoformat() if self.exit_ts else None,
            "entry": round(self.entry, 2), "stop": round(self.stop, 2),
            "target": round(self.target, 2), "exit": round(self.exit_price, 2) if self.exit_price else None,
            "lots": self.lots, "pnl": round(self.pnl, 2), "r": round(self.r, 3),
            "mae_r": round(self.mae_r, 2), "mfe_r": round(self.mfe_r, 2),
            "planned_rr": round(self.planned_rr, 2), "score": round(self.score, 3),
            "killzone": self.killzone, "dow": self.dow, "day": self.day,
            "bars_held": self.bars_held, "exit_reason": self.exit_reason,
        }


@dataclass
class PendingOrder:
    signal: Signal
    created_bar: int


@dataclass
class OpenPosition:
    trade: Trade
    stop: float
    target: float
    remaining_lots: float
    entry_bar: int
    be_armed: bool = False
    partial_done: bool = False
    realised: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[tuple[datetime, float]]
    starting_equity: float
    veto_counts: dict[str, int]
    signals_generated: int
    bars: int

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1][1] if self.equity_curve else self.starting_equity


class Backtester:
    def __init__(self, cfg: Config, weights: dict[str, float] | None = None,
                 collect_traces: bool = False) -> None:
        self.cfg = cfg
        self.pipeline = EntryPipeline(cfg, weights)
        self.collect_traces = collect_traces
        self.traces: list[PipelineResult] = []

    def run(self, ltf_candles: list[Candle]) -> BacktestResult:
        cfg = self.cfg
        htf_candles = resample(ltf_candles, cfg.htf)
        mtf_candles = resample(ltf_candles, cfg.mtf)

        ltf_model = MarketModel(cfg.model)
        htf_model = MarketModel(cfg.htf_model)
        mtf_model = MarketModel(cfg.model)
        htf_fed = mtf_fed = 0
        htf_min, mtf_min = tf_minutes(cfg.htf), tf_minutes(cfg.mtf)

        equity = cfg.risk.starting_equity
        curve: list[tuple[datetime, float]] = []
        trades: list[Trade] = []
        pending: list[PendingOrder] = []
        position: OpenPosition | None = None
        veto: dict[str, int] = {}
        signals = 0

        day = None
        day_trades = 0
        day_r = 0.0

        for i, bar in enumerate(ltf_candles):
            cur_day = trading_day(bar.ts)
            if cur_day != day:
                day, day_trades, day_r = cur_day, 0, 0.0

            # 1) Feed higher timeframes with bars that have ALREADY closed.
            while htf_fed < len(htf_candles) and _closes_at(htf_candles[htf_fed], htf_min) <= bar.ts:
                htf_model.update(htf_candles[htf_fed]); htf_fed += 1
            while mtf_fed < len(mtf_candles) and _closes_at(mtf_candles[mtf_fed], mtf_min) <= bar.ts:
                mtf_model.update(mtf_candles[mtf_fed]); mtf_fed += 1

            # 2) Manage the open position against this bar.
            if position is not None:
                equity, closed = self._manage(position, bar, i, equity)
                if closed:
                    trades.append(position.trade)
                    day_r += position.trade.r
                    position = None

            # 3) Work pending limit orders (never on their own signal bar).
            still: list[PendingOrder] = []
            for order in pending:
                if i <= order.signal.bar_idx:
                    still.append(order); continue
                if i > order.signal.expires_bar:
                    continue  # cancelled: the setup went stale
                if position is not None:
                    still.append(order); continue
                if _touches(bar, order.signal.entry):
                    position = self._open(order.signal, bar, i, equity)
                    day_trades += 1
                    equity, closed = self._manage(position, bar, i, equity, fill_bar=True)
                    if closed:
                        trades.append(position.trade)
                        day_r += position.trade.r
                        position = None
                else:
                    still.append(order)
            pending = still

            # 4) The bar is now closed: update the execution model with it.
            ltf_model.update(bar)

            # 5) Evaluate the pipe.
            blocked = (
                position is not None
                or day_trades >= cfg.risk.max_trades_per_day
                or day_r <= -abs(cfg.risk.daily_loss_limit_r)
                or len(pending) >= cfg.risk.max_concurrent
            )
            if not blocked:
                signal, results = self.pipeline.best(ltf_model, htf_model, equity, mtf_model)
                if self.collect_traces:
                    self.traces.extend(results)
                for r in results:
                    if r.signal is None and r.veto_stage:
                        veto[r.veto_stage] = veto.get(r.veto_stage, 0) + 1
                if signal is not None:
                    signals += 1
                    pending.append(PendingOrder(signal, i))

            curve.append((bar.ts, equity))

        return BacktestResult(trades, curve, cfg.risk.starting_equity, veto, signals, len(ltf_candles))

    # -- execution internals ------------------------------------------------

    def _open(self, sig: Signal, bar: Candle, i: int, equity: float) -> OpenPosition:
        cfg = self.cfg
        half_spread = cfg.costs.spread / 2.0
        slip = cfg.costs.slippage_entry
        fill = sig.entry + half_spread + slip if sig.direction == BULL else sig.entry - half_spread - slip
        kz = ",".join(sig.evidence.get("time", {}).get("killzones", [])) or "?"
        trade = Trade(
            setup=sig.setup, direction=sig.direction, signal_ts=sig.ts, entry_ts=bar.ts,
            exit_ts=None, entry=fill, stop=sig.stop, target=sig.target, lots=sig.lots,
            risk_usd=sig.risk_usd, score=sig.score, killzone=kz, dow=day_of_week(bar.ts),
            day=trading_day(bar.ts), planned_rr=sig.rr, evidence=sig.evidence,
        )
        return OpenPosition(trade, sig.stop, sig.target, sig.lots, entry_bar=i)

    def _manage(self, pos: OpenPosition, bar: Candle, i: int, equity: float,
                fill_bar: bool = False) -> tuple[float, bool]:
        cfg = self.cfg
        t = pos.trade
        long = t.direction == BULL
        # t.stop is the ORIGINAL stop and never mutates; pos.stop is the live one
        # that breakeven moves. All R measurements use the original distance.
        base_risk = abs(t.entry - t.stop) or 1e-9
        t.bars_held = i - pos.entry_bar

        # Excursions, measured in R against the ORIGINAL stop distance.
        adverse = (t.entry - bar.low) if long else (bar.high - t.entry)
        favour = (bar.high - t.entry) if long else (t.entry - bar.low)
        t.mae_r = max(t.mae_r, adverse / base_risk)
        t.mfe_r = max(t.mfe_r, favour / base_risk)

        half_spread = cfg.costs.spread / 2.0
        hit_stop = bar.low <= pos.stop if long else bar.high >= pos.stop
        hit_target = bar.high >= pos.target if long else bar.low <= pos.target

        exit_price = None
        reason = ""
        if hit_stop:  # pessimistic tie-break: the stop wins
            exit_price = pos.stop - half_spread - cfg.costs.slippage_stop if long else \
                pos.stop + half_spread + cfg.costs.slippage_stop
            reason = "stop" if not pos.be_armed else "breakeven"
        elif hit_target:
            exit_price = pos.target - half_spread if long else pos.target + half_spread
            reason = "target"
        else:
            ny_hour = bar.ts.astimezone(NY).hour
            timeout = t.bars_held >= cfg.risk.max_hold_bars
            session_end = cfg.risk.flat_by_ny_hour is not None and ny_hour >= cfg.risk.flat_by_ny_hour \
                and bar.ts.astimezone(NY).weekday() < 5
            if timeout or session_end:
                exit_price = bar.close - half_spread if long else bar.close + half_spread
                reason = "timeout" if timeout else "session_flat"

        if exit_price is None:
            # Risk reduction arms only once the bar has closed beyond the level.
            r_now = ((bar.close - t.entry) if long else (t.entry - bar.close)) / base_risk
            if cfg.risk.partial_at_r and not pos.partial_done and r_now >= cfg.risk.partial_at_r:
                cut = cfg.symbol.round_lots(pos.remaining_lots * cfg.risk.partial_fraction)
                if cut >= cfg.symbol.min_lot:
                    px = bar.close - half_spread if long else bar.close + half_spread
                    gain = cfg.symbol.pnl(t.direction, t.entry, px, cut)
                    gain -= cfg.costs.commission_per_lot * cut
                    pos.realised += gain
                    equity += gain
                    pos.remaining_lots = round(pos.remaining_lots - cut, 4)
                    pos.partial_done = True
            if cfg.risk.breakeven_at_r and not pos.be_armed and r_now >= cfg.risk.breakeven_at_r:
                pos.stop = t.entry
                pos.be_armed = True
            return equity, False

        gross = cfg.symbol.pnl(t.direction, t.entry, exit_price, pos.remaining_lots)
        gross -= cfg.costs.commission_per_lot * pos.remaining_lots
        total = pos.realised + gross
        equity += gross
        t.exit_price = exit_price
        t.exit_ts = bar.ts
        t.exit_reason = reason
        t.pnl = total
        t.r = total / t.risk_usd if t.risk_usd else 0.0
        return equity, True


def _closes_at(c: Candle, minutes: int) -> datetime:
    from datetime import timedelta
    return c.ts + timedelta(minutes=minutes)


def _touches(bar: Candle, price: float) -> bool:
    return bar.low <= price <= bar.high
