"""Performance measurement, honesty checks, and the breakdowns you actually
improve the strategy with.

A single headline win rate is close to useless. What moves a strategy forward
is the *conditional* table: which setup, in which killzone, on which day, at
which score bucket, actually pays. That is what this module produces, plus the
statistics that tell you whether the numbers mean anything at all at your
sample size.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import mean, stdev

from .backtest import BacktestResult, Trade


@dataclass
class Summary:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    total_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    payoff: float = 0.0
    profit_factor: float = 0.0
    max_dd_r: float = 0.0
    max_dd_pct: float = 0.0
    max_consec_losses: int = 0
    net_pnl: float = 0.0
    return_pct: float = 0.0
    t_stat: float = 0.0
    p_positive: float = 0.0
    avg_mae_r: float = 0.0
    avg_mfe_r: float = 0.0

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


MIN_N_FOR_T = 10  # below this, a t-statistic is noise dressed as evidence


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def summarize(trades: list[Trade], starting_equity: float = 10_000.0) -> Summary:
    s = Summary()
    if not trades:
        return s
    rs = [t.r for t in trades]
    s.trades = len(rs)
    s.wins = sum(1 for r in rs if r > 0.02)
    s.losses = sum(1 for r in rs if r < -0.02)
    s.breakeven = s.trades - s.wins - s.losses
    s.win_rate = s.wins / s.trades
    s.total_r = sum(rs)
    s.expectancy_r = s.total_r / s.trades
    wins = [r for r in rs if r > 0.02]
    losses = [r for r in rs if r < -0.02]
    s.avg_win_r = mean(wins) if wins else 0.0
    s.avg_loss_r = mean(losses) if losses else 0.0
    s.payoff = abs(s.avg_win_r / s.avg_loss_r) if s.avg_loss_r else 0.0
    gross_win = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
    s.profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    s.net_pnl = sum(t.pnl for t in trades)
    s.return_pct = s.net_pnl / starting_equity * 100.0 if starting_equity else 0.0
    s.avg_mae_r = mean(t.mae_r for t in trades)
    s.avg_mfe_r = mean(t.mfe_r for t in trades)

    peak = 0.0
    cum = 0.0
    dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    s.max_dd_r = dd

    eq = starting_equity
    peak_eq = starting_equity
    dd_pct = 0.0
    for t in trades:
        eq += t.pnl
        peak_eq = max(peak_eq, eq)
        dd_pct = max(dd_pct, (peak_eq - eq) / peak_eq * 100.0 if peak_eq else 0.0)
    s.max_dd_pct = dd_pct

    streak = best = 0
    for r in rs:
        streak = streak + 1 if r < -0.02 else 0
        best = max(best, streak)
    s.max_consec_losses = best

    # Sample (n-1) standard deviation, and no t-statistic at all below 10
    # trades: with n=2 the formula happily reports t=-65, which is not a
    # finding, it is a division artefact. Refusing to print a number is more
    # useful than printing a confident wrong one.
    sd = stdev(rs) if len(rs) > 1 else 0.0
    if sd > 0 and len(rs) >= MIN_N_FOR_T:
        s.t_stat = s.expectancy_r / (sd / math.sqrt(len(rs)))
        s.p_positive = _norm_cdf(s.t_stat)  # crude one-sided read, not gospel
    return s


def breakdown(trades: list[Trade], key: str) -> dict[str, Summary]:
    """Group trades by an attribute (setup / killzone / dow / exit_reason)."""
    groups: dict[str, list[Trade]] = {}
    for t in trades:
        groups.setdefault(str(getattr(t, key, "?")), []).append(t)
    return {k: summarize(v) for k, v in sorted(groups.items())}


def score_buckets(trades: list[Trade], edges: tuple[float, ...] = (0.5, 0.6, 0.7, 0.8)) -> dict[str, Summary]:
    """Does a higher confluence score actually pay more? If not, the score is
    decoration and its weights need refitting (or deleting)."""
    groups: dict[str, list[Trade]] = {}
    for t in trades:
        label = f"<{edges[0]:.2f}"
        for lo, hi in zip(edges, edges[1:] + (1.01,)):
            if lo <= t.score < hi:
                label = f"{lo:.2f}-{hi:.2f}" if hi <= 1.0 else f">={lo:.2f}"
                break
        groups.setdefault(label, []).append(t)
    return {k: summarize(v) for k, v in sorted(groups.items())}


@dataclass
class MonteCarlo:
    runs: int
    median_dd_r: float
    p95_dd_r: float
    worst_dd_r: float
    prob_negative: float
    prob_ruin: float
    median_total_r: float


def monte_carlo(trades: list[Trade], runs: int = 2000, ruin_r: float = 20.0,
                seed: int = 11) -> MonteCarlo:
    """Reshuffle trade ORDER (not outcomes) to see the drawdowns you could
    plausibly have lived through. The sequence you happened to get is one draw
    from this distribution, and it is usually kinder than the median."""
    rs = [t.r for t in trades]
    if not rs:
        return MonteCarlo(0, 0, 0, 0, 0, 0, 0)
    rng = random.Random(seed)
    dds: list[float] = []
    totals: list[float] = []
    ruined = 0
    for _ in range(runs):
        rng.shuffle(rs)
        peak = cum = dd = 0.0
        for r in rs:
            cum += r
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        dds.append(dd)
        totals.append(cum)
        if dd >= ruin_r:
            ruined += 1
    dds.sort(); totals.sort()
    return MonteCarlo(
        runs=runs,
        median_dd_r=dds[len(dds) // 2],
        p95_dd_r=dds[int(len(dds) * 0.95)],
        worst_dd_r=dds[-1],
        prob_negative=sum(1 for t in totals if t < 0) / len(totals),
        prob_ruin=ruined / runs,
        median_total_r=totals[len(totals) // 2],
    )


def required_sample(expectancy_r: float, sd_r: float, confidence: float = 1.96) -> int:
    """How many trades before the measured edge clears its own noise floor?

    Answering 'is 40 trades enough?' with arithmetic instead of hope is the
    single cheapest upgrade to a trading process.
    """
    if expectancy_r <= 0 or sd_r <= 0:
        return -1
    return int(math.ceil((confidence * sd_r / expectancy_r) ** 2))


# --- walk-forward -----------------------------------------------------------

@dataclass
class Fold:
    index: int
    is_start: int
    is_end: int
    oos_start: int
    oos_end: int


def walk_forward_folds(n_bars: int, folds: int = 5, is_ratio: float = 0.7) -> list[Fold]:
    """Anchored-rolling split: optimise on IS, judge ONLY on OOS.

    The OOS segments never overlap, so concatenating them gives one continuous
    out-of-sample track record - the only number worth showing anyone.
    """
    out: list[Fold] = []
    block = n_bars // folds
    if block < 10:
        return out
    for k in range(folds):
        start = k * block
        end = min(n_bars, start + block)
        is_end = start + int(block * is_ratio)
        out.append(Fold(k, start, is_end, is_end, end))
    return out


# --- reporting --------------------------------------------------------------

def _row(name: str, s: Summary) -> str:
    return (f"{name:<22}{s.trades:>6}{s.win_rate * 100:>8.1f}%{s.expectancy_r:>9.3f}"
            f"{s.total_r:>9.1f}{s.profit_factor:>8.2f}{s.max_dd_r:>9.1f}")


def _header() -> str:
    return (f"{'group':<22}{'n':>6}{'win%':>9}{'exp(R)':>9}"
            f"{'totR':>9}{'PF':>8}{'maxDD_R':>9}\n" + "-" * 72)


def report(result: BacktestResult, *, mc_runs: int = 2000) -> str:
    trades = result.trades
    s = summarize(trades, result.starting_equity)
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("ICT GOLD PIPELINE - BACKTEST REPORT")
    lines.append("=" * 72)
    lines.append(f"bars processed      : {result.bars}")
    lines.append(f"signals generated   : {result.signals_generated}")
    lines.append(f"trades filled       : {s.trades}")
    lines.append(f"win rate            : {s.win_rate * 100:.1f}%  "
                 f"({s.wins}W / {s.losses}L / {s.breakeven}BE)")
    lines.append(f"expectancy          : {s.expectancy_r:+.3f} R per trade")
    lines.append(f"total               : {s.total_r:+.1f} R   "
                 f"net ${s.net_pnl:,.0f}  ({s.return_pct:+.1f}%)")
    lines.append(f"avg win / avg loss  : {s.avg_win_r:+.2f}R / {s.avg_loss_r:+.2f}R  "
                 f"(payoff {s.payoff:.2f})")
    lines.append(f"profit factor       : {s.profit_factor:.2f}")
    lines.append(f"max drawdown        : {s.max_dd_r:.1f} R  ({s.max_dd_pct:.1f}% of equity)")
    lines.append(f"max consec losses   : {s.max_consec_losses}")
    lines.append(f"avg MAE / MFE       : {s.avg_mae_r:.2f}R / {s.avg_mfe_r:.2f}R")
    if s.trades >= MIN_N_FOR_T:
        lines.append(f"t-stat of expectancy: {s.t_stat:.2f}  "
                     f"(>2.0 before you believe it)")
    else:
        lines.append(f"t-stat of expectancy: n/a (need >= {MIN_N_FOR_T} trades)")
    rs = [t.r for t in trades]
    if len(rs) > 1:
        need = required_sample(s.expectancy_r, stdev(rs))
        lines.append(f"sample needed       : {need if need > 0 else 'n/a (no positive edge)'}"
                     f"  (have {len(rs)})")

    for title, table in (
        ("BY SETUP", breakdown(trades, "setup")),
        ("BY KILLZONE", breakdown(trades, "killzone")),
        ("BY DAY", breakdown(trades, "dow")),
        ("BY EXIT", breakdown(trades, "exit_reason")),
        ("BY SCORE", score_buckets(trades)),
    ):
        lines.append("")
        lines.append(title)
        lines.append(_header())
        for k, v in table.items():
            lines.append(_row(k, v))

    if trades:
        mc = monte_carlo(trades, runs=mc_runs)
        lines.append("")
        lines.append("MONTE CARLO (trade order reshuffled)")
        lines.append("-" * 72)
        lines.append(f"median drawdown   : {mc.median_dd_r:.1f} R")
        lines.append(f"95th pct drawdown : {mc.p95_dd_r:.1f} R   <- size your risk against THIS")
        lines.append(f"worst drawdown    : {mc.worst_dd_r:.1f} R")
        lines.append(f"P(total < 0)      : {mc.prob_negative * 100:.1f}%")
        lines.append(f"P(dd >= 20R)      : {mc.prob_ruin * 100:.1f}%")

    lines.append("")
    lines.append("PIPELINE VETOES (where setups died)")
    lines.append("-" * 72)
    total_v = sum(result.veto_counts.values()) or 1
    for stage, n in sorted(result.veto_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"{stage:<22}{n:>10}  {n / total_v * 100:>6.1f}%")
    return "\n".join(lines)
