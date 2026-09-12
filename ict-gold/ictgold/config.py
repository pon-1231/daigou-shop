"""Configuration objects.

Deliberately JSON-backed (no PyYAML) and deliberately SMALL. Every tunable
number here is a degree of freedom you can overfit with, so the rule is:
a parameter earns its place only if you can state, in one sentence, the market
mechanic it encodes. Count them before every optimisation run.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .state import ModelConfig


@dataclass
class SymbolSpec:
    """Contract maths for the traded instrument (defaults = spot XAUUSD)."""

    name: str = "XAUUSD"
    contract_size: float = 100.0  # ounces per 1.00 lot -> $1 move = $100
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 50.0
    tick: float = 0.01

    def pnl(self, direction: str, entry: float, exit_price: float, lots: float) -> float:
        delta = (exit_price - entry) if direction == "bullish" else (entry - exit_price)
        return delta * self.contract_size * lots

    def round_lots(self, lots: float) -> float:
        stepped = int(lots / self.lot_step) * self.lot_step
        return max(0.0, min(round(stepped, 4), self.max_lot))


@dataclass
class CostModel:
    """Frictions. Gold is expensive to trade; modelling this honestly is the
    difference between a 'profitable' backtest and a real one."""

    spread: float = 0.30  # price units ($) charged on entry and exit
    slippage_entry: float = 0.10
    slippage_stop: float = 0.25  # stops slip worse than limits, especially on news
    commission_per_lot: float = 7.0  # round turn, USD
    # A constant spread is optimistic exactly when it matters most: gold's
    # spread widens sharply during high-impact US data and again at the thin
    # liquidity around the 17:00 NY rollover. These multipliers apply to any
    # position management (stop/target fills) that happens to occur in those
    # windows, whether or not the trade was opened there.
    news_spread_mult: float = 3.0
    rollover_spread_mult: float = 2.0


@dataclass
class RiskConfig:
    starting_equity: float = 10_000.0
    risk_per_trade_pct: float = 0.5
    max_trades_per_day: int = 3
    # NOTE: only unfilled PENDING orders used to count against this too, which
    # meant a limit order that was never going to fill could block every other
    # setup from even being evaluated until it expired - an order that carries
    # no risk was spending the risk budget. Pending orders no longer count;
    # this now gates concurrent OPEN POSITIONS only (the engine only supports
    # one at a time regardless, so this is a placeholder for a future
    # multi-position version, not a live limit today).
    max_concurrent: int = 1
    daily_loss_limit_r: float = 2.0  # stop trading for the day after -2R
    max_stop_atr: float = 3.0  # refuse setups whose stop is absurdly wide
    min_stop_atr: float = 0.8  # ... or absurdly tight (spread noise)
    # A stop must be at least this many round-trip costs wide. On gold a 0.30
    # spread plus 0.25 stop slippage against a 0.60 stop turns a textbook -1R
    # into -1.6R; no win rate survives that. This is the single most
    # underrated filter in the whole config.
    min_stop_cost_mult: float = 4.0
    breakeven_at_r: float | None = 1.0  # move stop to entry at +1R (None = off)
    partial_at_r: float | None = None  # take half off at this R (None = off)
    partial_fraction: float = 0.5
    # Cap the objective at a distance price can plausibly cover in one
    # session. Without this the 'draw on liquidity' happily points at a level
    # two days away and every trade dies at the session flat.
    target_max_atr: float = 12.0
    max_hold_bars: int = 288  # a day of M5; an intraday model should not linger
    # Square up at the actual gold trading-day rollover (17:00 NY, the same
    # boundary sessions.trading_day() uses for daily pools). This used to be
    # 16 - one hour BEFORE the ny_pm (13:30-16:00) and sb_pm (14:00-15:00)
    # killzones even finish, which meant a position opened at 15:45 got about
    # 15 minutes of runway before being flattened. That is not "the setup
    # didn't work", that is a config fighting itself; see the real-data
    # review's killzone table.
    flat_by_ny_hour: int | None = 17


@dataclass
class SetupSpec:
    """One named entry model = one configuration of the same pipeline."""

    name: str
    enabled: bool = True
    killzones: list[str] = field(default_factory=lambda: ["ny_am"])
    require_macro: bool = False

    align_with_htf_bias: bool = True
    allow_counter_trend_on_sweep: bool = False

    require_sweep: bool = True
    sweep_lookback: int = 24  # bars since the stop-run
    min_pool_strength: int = 1

    require_structure: bool = True
    structure_kinds: list[str] = field(default_factory=lambda: ["CHOCH", "BOS"])
    require_displacement: bool = True
    structure_lookback: int = 12

    entry_pd: list[str] = field(default_factory=lambda: ["fvg", "ob"])
    entry_fill: str = "ce"  # ce | proximal | distal
    require_zone: str = "discount"  # discount | premium | any (relative to bias)
    ote_low: float = 0.62
    ote_high: float = 0.79

    stop_from: str = "sweep_extreme"  # sweep_extreme | pd_distal | structure
    stop_buffer_atr: float = 0.35
    target_mode: str = "draw"  # draw | rr
    fixed_rr: float = 2.0
    min_rr: float = 2.0
    max_rr: float = 5.0  # a 12R intraday "target" is a data artefact, not a trade

    # A limit order that fills the instant price TOUCHES the PD array is a
    # blind bet that the array will be respected. It won't always be - price
    # slices through plenty of them. Confirmation waits for the array to
    # actually show a reaction (a rejection candle: touches the zone, closes
    # back out of it, with a wick proving it, not a drift past the line)
    # before committing. See docs/TRADER_REVIEW.md - this is the fix for the
    # single biggest leak that review found.
    require_confirmation: bool = True
    confirm_max_bars: int = 4  # give up waiting for a reaction after this many bars
    confirm_wick_ratio: float = 0.33  # rejection wick as a fraction of the bar's range

    order_valid_bars: int = 12
    min_score: float = 0.55
    weight: float = 1.0


@dataclass
class Config:
    symbol: SymbolSpec = field(default_factory=SymbolSpec)
    costs: CostModel = field(default_factory=CostModel)
    risk: RiskConfig = field(default_factory=RiskConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    htf_model: ModelConfig = field(default_factory=lambda: ModelConfig(swing_lookback=2))

    ltf: str = "M5"  # execution timeframe
    mtf: str = "M15"  # structure timeframe
    htf: str = "H4"  # bias timeframe

    trade_days: list[str] = field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"])
    # Blocks new entries within this many minutes of 08:30 NY, where most
    # high-impact USD data prints (see sessions.in_news_blackout - it's a
    # blunt clock-slot proxy, not a real calendar) and widens the spread
    # assumed for any position management that happens to fall in the same
    # window. This field existed before but was never read anywhere; it is
    # wired in now.
    news_blackout_minutes: int = 15
    setups: list[SetupSpec] = field(default_factory=list)

    @staticmethod
    def default() -> "Config":
        cfg = Config()
        cfg.setups = default_setups()
        return cfg

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @staticmethod
    def from_json(path: str | Path) -> "Config":
        raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        return Config.from_dict(raw)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Config":
        cfg = Config()
        for key, klass in (
            ("symbol", SymbolSpec),
            ("costs", CostModel),
            ("risk", RiskConfig),
            ("model", ModelConfig),
            ("htf_model", ModelConfig),
        ):
            if key in raw:
                setattr(cfg, key, klass(**raw[key]))
        for key in ("ltf", "mtf", "htf", "trade_days", "news_blackout_minutes"):
            if key in raw:
                setattr(cfg, key, raw[key])
        cfg.setups = [SetupSpec(**s) for s in raw.get("setups", [])] or default_setups()
        return cfg


def default_setups() -> list[SetupSpec]:
    """The three playbooks. Each encodes a distinct, falsifiable market story."""
    return [
        SetupSpec(
            # Story: Asia/London builds a range, the NY open runs its stops,
            # then the real move goes the other way from a fresh imbalance.
            name="judas_reversal",
            killzones=["london", "ny_am"],
            align_with_htf_bias=True,
            allow_counter_trend_on_sweep=True,
            require_sweep=True,
            sweep_lookback=24,
            min_pool_strength=2,
            require_structure=True,
            structure_kinds=["CHOCH"],
            require_displacement=True,
            entry_pd=["fvg", "ob"],
            entry_fill="ce",
            require_zone="discount",
            stop_from="sweep_extreme",
            # Fixed 1:2R rather than "ride to the next liquidity pool". A
            # pool-based target is truer to ICT, but it is also an unbounded
            # promise: it can sit 8R away one day and 1.2R away the next, and
            # you cannot pre-commit to a payoff you cannot state in advance.
            # A fixed 1:2 is smaller, boring, and - critically - the same
            # trade every time, which is the only kind you can accumulate a
            # real sample on.
            target_mode="rr",
            fixed_rr=2.0,
            min_rr=2.0,
            order_valid_bars=12,
            min_score=0.55,
        ),
        SetupSpec(
            # Story: inside the 10-11 NY window an imbalance forms that the
            # algorithm must revisit before continuing to the day's objective.
            name="silver_bullet",
            killzones=["sb_am", "sb_pm", "sb_london"],
            align_with_htf_bias=True,
            require_sweep=True,
            sweep_lookback=12,
            min_pool_strength=1,
            require_structure=True,
            structure_kinds=["CHOCH", "BOS"],
            require_displacement=True,
            structure_lookback=8,
            entry_pd=["fvg"],
            entry_fill="proximal",
            require_zone="any",
            stop_from="sweep_extreme",
            target_mode="rr",
            fixed_rr=2.0,
            min_rr=2.0,
            # The whole window is only 8 bars valid - waiting the full default
            # 4 bars for a reaction eats half of it. Confirm faster, and
            # accept a slightly weaker wick, or this setup barely gets to fire.
            confirm_max_bars=2,
            confirm_wick_ratio=0.28,
            order_valid_bars=8,
            min_score=0.5,
        ),
        SetupSpec(
            # Story: trend day. Price retraces into OTE inside a discount and
            # continues toward the standing draw on liquidity.
            name="ote_continuation",
            killzones=["london", "ny_am", "ny_pm"],
            align_with_htf_bias=True,
            allow_counter_trend_on_sweep=False,
            require_sweep=False,
            require_structure=True,
            structure_kinds=["BOS"],
            require_displacement=True,
            structure_lookback=20,
            entry_pd=["ote", "fvg", "ob"],
            entry_fill="ce",
            require_zone="discount",
            # Below the leg's origin, not just under the zone: a stop tucked
            # against the PD array gets picked off by the retest itself.
            stop_from="structure",
            stop_buffer_atr=0.5,
            target_mode="rr",
            fixed_rr=2.0,
            min_rr=2.0,
            order_valid_bars=16,
            min_score=0.6,
        ),
    ]
