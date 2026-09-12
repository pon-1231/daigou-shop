"""Command line entry point:  python -m ictgold <command>

    demo        run the pipeline over synthetic data (smoke test only)
    backtest    run over a real OHLCV csv and print the full report
    walkforward out-of-sample evaluation, fold by fold
    scan        evaluate the most recent bar and emit a signal as JSON
    explain     dump the full stage-by-stage trace around a timestamp
    init-config write a default config file you can edit
    chart       render a trade journal (backtest --trades) as candlestick charts
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .backtest import Backtester
from .chart import render_html
from .config import Config
from .core import resample, tf_minutes
from .data import load_csv, synthetic_m5
from .metrics import (monte_carlo, report, summarize, walk_forward_folds)
from .pipeline import EntryPipeline
from .state import MarketModel


def _load(args) -> list:
    if getattr(args, "csv", None):
        candles = load_csv(args.csv, source_tz=args.tz)
        if not candles:
            print(f"no candles parsed from {args.csv}", file=sys.stderr)
            sys.exit(2)
        return candles
    return synthetic_m5(bars=args.bars, seed=args.seed)


def _config(args) -> Config:
    return Config.from_json(args.config) if getattr(args, "config", None) else Config.default()


def cmd_demo(args) -> int:
    cfg = _config(args)
    candles = synthetic_m5(bars=args.bars, seed=args.seed)
    bt = Backtester(cfg)
    res = bt.run(candles)
    print(report(res, mc_runs=args.mc))
    print()
    print("!" * 72)
    print("SYNTHETIC DATA. The generator injects the very stop-runs this strategy")
    print("hunts, so these numbers prove the code runs - nothing more. Replace with")
    print("real XAUUSD data before drawing a single conclusion.")
    print("!" * 72)
    return 0


def cmd_backtest(args) -> int:
    cfg = _config(args)
    candles = _load(args)
    print(f"loaded {len(candles)} {cfg.ltf} candles  "
          f"{candles[0].ts.date()} -> {candles[-1].ts.date()}", file=sys.stderr)
    bt = Backtester(cfg)
    res = bt.run(candles)
    text = report(res, mc_runs=args.mc)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    if args.trades:
        Path(args.trades).write_text(
            json.dumps([t.to_dict() for t in res.trades], indent=2), encoding="utf-8")
        print(f"\ntrade journal -> {args.trades}", file=sys.stderr)
    return 0


def cmd_walkforward(args) -> int:
    cfg = _config(args)
    candles = _load(args)
    folds = walk_forward_folds(len(candles), folds=args.folds, is_ratio=args.is_ratio)
    if not folds:
        print("not enough data for the requested folds", file=sys.stderr)
        return 2
    all_oos = []
    print(f"{'fold':<6}{'IS n':>8}{'IS exp':>10}{'OOS n':>8}{'OOS exp':>10}{'OOS totR':>10}")
    print("-" * 52)
    for f in folds:
        is_res = Backtester(cfg).run(candles[f.is_start:f.is_end])
        oos_res = Backtester(cfg).run(candles[f.oos_start:f.oos_end])
        is_s = summarize(is_res.trades)
        oos_s = summarize(oos_res.trades)
        all_oos.extend(oos_res.trades)
        print(f"{f.index:<6}{is_s.trades:>8}{is_s.expectancy_r:>10.3f}"
              f"{oos_s.trades:>8}{oos_s.expectancy_r:>10.3f}{oos_s.total_r:>10.1f}")
    print("-" * 52)
    agg = summarize(all_oos, cfg.risk.starting_equity)
    print(f"COMBINED OUT-OF-SAMPLE: n={agg.trades} win={agg.win_rate * 100:.1f}% "
          f"exp={agg.expectancy_r:+.3f}R total={agg.total_r:+.1f}R "
          f"maxDD={agg.max_dd_r:.1f}R t={agg.t_stat:.2f}")
    if agg.trades < 30:
        print(f"\n*** {agg.trades} out-of-sample trades is not a result. ***")
        print("    Use more data, or widen the setups - do NOT draw a conclusion from this.")
    elif all_oos:
        mc = monte_carlo(all_oos)
        print(f"OOS monte carlo: median DD {mc.median_dd_r:.1f}R, "
              f"p95 DD {mc.p95_dd_r:.1f}R, P(total<0) {mc.prob_negative * 100:.1f}%")
    return 0


def _build_models(cfg: Config, candles: list):
    """Replay history into the three models, honouring bar-close alignment."""
    from datetime import timedelta
    htf_c = resample(candles, cfg.htf)
    mtf_c = resample(candles, cfg.mtf)
    ltf_m, htf_m, mtf_m = MarketModel(cfg.model), MarketModel(cfg.htf_model), MarketModel(cfg.model)
    hi = mi = 0
    hmin, mmin = tf_minutes(cfg.htf), tf_minutes(cfg.mtf)
    for bar in candles:
        while hi < len(htf_c) and htf_c[hi].ts + timedelta(minutes=hmin) <= bar.ts:
            htf_m.update(htf_c[hi]); hi += 1
        while mi < len(mtf_c) and mtf_c[mi].ts + timedelta(minutes=mmin) <= bar.ts:
            mtf_m.update(mtf_c[mi]); mi += 1
        ltf_m.update(bar)
    return ltf_m, htf_m, mtf_m


def cmd_scan(args) -> int:
    cfg = _config(args)
    candles = _load(args)
    tail = candles[-args.window:] if args.window and len(candles) > args.window else candles
    ltf_m, htf_m, mtf_m = _build_models(cfg, tail)
    pipe = EntryPipeline(cfg)
    signal, results = pipe.best(ltf_m, htf_m, cfg.risk.starting_equity, mtf_m)
    payload = {
        "symbol": cfg.symbol.name,
        "as_of": tail[-1].ts.isoformat(),
        "price": tail[-1].close,
        "signal": signal.to_dict() if signal else None,
        "setups": [
            {"setup": r.setup, "fired": r.signal is not None,
             "veto_stage": r.veto_stage,
             "trace": [{"stage": o.stage, "passed": o.passed, "reason": o.reason} for o in r.trace]}
            for r in results
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_explain(args) -> int:
    cfg = _config(args)
    candles = _load(args)
    at = datetime.fromisoformat(args.at)
    if at.tzinfo is None:
        print("--at needs a timezone, e.g. 2024-06-03T13:35:00+00:00", file=sys.stderr)
        return 2
    upto = [c for c in candles if c.ts <= at]
    if not upto:
        print("no candles at or before that timestamp", file=sys.stderr)
        return 2
    tail = upto[-args.window:] if args.window and len(upto) > args.window else upto
    ltf_m, htf_m, mtf_m = _build_models(cfg, tail)
    pipe = EntryPipeline(cfg)
    atr = f"{ltf_m.atr_value:.2f}" if ltf_m.atr_value else "n/a"
    print(f"as of {tail[-1].ts.isoformat()}  close={tail[-1].close:.2f}  atr={atr}")
    print(f"ltf trend={ltf_m.trend}  htf trend={htf_m.trend}  mtf trend={mtf_m.trend}")
    dr = ltf_m.dealing_range()
    if dr:
        print(f"dealing range {dr.low:.2f}-{dr.high:.2f}  "
              f"zone={dr.zone(tail[-1].close)} ({dr.retracement(tail[-1].close):.2f})")
    print("live pools: " + ", ".join(
        f"{p.kind}@{p.price:.2f}({p.source},s{p.strength})" for p in ltf_m.live_pools()[-8:]))
    for r in pipe.run(ltf_m, htf_m, cfg.risk.starting_equity, mtf_m):
        print(f"\n--- {r.setup} ---")
        for o in r.trace:
            print(f"  [{'PASS' if o.passed else 'VETO'}] {o.stage:<10} {o.reason}")
        if r.signal:
            print(f"  => SIGNAL {json.dumps(r.signal.to_dict()['side'])} "
                  f"entry={r.signal.entry:.2f} stop={r.signal.stop:.2f} "
                  f"target={r.signal.target:.2f} rr={r.signal.rr:.2f}")
    return 0


def cmd_chart(args) -> int:
    candles = load_csv(args.csv, source_tz=args.tz) if args.csv else synthetic_m5(bars=args.bars, seed=args.seed)
    if not candles:
        print(f"no candles parsed from {args.csv}", file=sys.stderr)
        return 2
    trades = json.loads(Path(args.trades).read_text(encoding="utf-8"))
    n = render_html(candles, trades, args.out, lookback=args.lookback, lookahead=args.lookahead,
                    limit=args.limit, setup=args.setup, outcome=args.outcome)
    if n == 0:
        print("no trades matched / rendered - check --setup, --outcome, and that --csv covers "
              "the same period as --trades", file=sys.stderr)
        return 2
    print(f"wrote {n} trade card(s) -> {args.out}", file=sys.stderr)
    return 0


def cmd_init_config(args) -> int:
    Config.default().to_json(args.path)
    print(f"wrote {args.path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ictgold", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, csv_required: bool = False):
        sp.add_argument("--csv", required=csv_required, help="OHLCV csv on the execution timeframe")
        sp.add_argument("--tz", default="UTC", help="timezone of the csv timestamps (broker time!)")
        sp.add_argument("--config", help="json config file")
        sp.add_argument("--bars", type=int, default=20_000, help="synthetic bars when no --csv")
        sp.add_argument("--seed", type=int, default=7)

    d = sub.add_parser("demo", help="synthetic smoke test")
    common(d); d.add_argument("--mc", type=int, default=2000)
    d.set_defaults(func=cmd_demo)

    b = sub.add_parser("backtest", help="backtest a csv")
    common(b, csv_required=True)
    b.add_argument("--out", help="write the text report here")
    b.add_argument("--trades", help="write the trade journal json here")
    b.add_argument("--mc", type=int, default=2000)
    b.set_defaults(func=cmd_backtest)

    w = sub.add_parser("walkforward", help="out-of-sample folds")
    common(w, csv_required=False)
    w.add_argument("--folds", type=int, default=5)
    w.add_argument("--is-ratio", dest="is_ratio", type=float, default=0.7)
    w.set_defaults(func=cmd_walkforward)

    s = sub.add_parser("scan", help="evaluate the latest bar, emit json")
    common(s); s.add_argument("--window", type=int, default=3000)
    s.set_defaults(func=cmd_scan)

    e = sub.add_parser("explain", help="stage-by-stage trace at a timestamp")
    common(e); e.add_argument("--at", required=True, help="ISO timestamp with offset")
    e.add_argument("--window", type=int, default=3000)
    e.set_defaults(func=cmd_explain)

    i = sub.add_parser("init-config", help="write a default config json")
    i.add_argument("path", nargs="?", default="config/xauusd.json")
    i.set_defaults(func=cmd_init_config)

    ch = sub.add_parser("chart", help="render a trade journal as candlestick charts (html)")
    ch.add_argument("--csv", help="the SAME csv used to produce --trades")
    ch.add_argument("--tz", default="UTC", help="timezone of the csv timestamps (broker time!)")
    ch.add_argument("--bars", type=int, default=20_000, help="synthetic bars when no --csv")
    ch.add_argument("--seed", type=int, default=7)
    ch.add_argument("--trades", required=True, help="trade journal json from `backtest --trades`")
    ch.add_argument("--out", default="chart.html")
    ch.add_argument("--lookback", type=int, default=15, help="bars shown before entry")
    ch.add_argument("--lookahead", type=int, default=8, help="bars shown after exit")
    ch.add_argument("--limit", type=int, help="only render the most recent N (matching) trades")
    ch.add_argument("--setup", help="only this setup, e.g. judas_reversal")
    ch.add_argument("--outcome", choices=["win", "loss", "flat"], help="only this outcome bucket")
    ch.set_defaults(func=cmd_chart)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
