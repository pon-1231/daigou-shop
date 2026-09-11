"""Tests for the invariants that, if broken, silently fake a profitable strategy.

The three that matter most:
  * swings are published only after their confirmation bar (no lookahead),
  * higher timeframes are fed only closed bars,
  * an order never fills on the bar that produced its signal.
Everything else is arithmetic.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ictgold.backtest import Backtester
from ictgold.config import Config
from ictgold.core import UTC, Candle, align_index, resample
from ictgold.data import load_csv, save_csv, synthetic_m5
from ictgold.metrics import MIN_N_FOR_T, required_sample, summarize, walk_forward_folds
from ictgold.sessions import KILLZONES, active_killzones, trading_day
from ictgold.state import BEAR, BULL, MarketModel, ModelConfig


def c(minute: int, o: float, h: float, l: float, cl: float) -> Candle:
    return Candle(datetime(2024, 6, 3, 12, 0, tzinfo=UTC) + timedelta(minutes=minute), o, h, l, cl)


class TestCore(unittest.TestCase):
    def test_resample_aggregates_ohlc(self):
        bars = [c(i * 5, 10 + i, 12 + i, 8 + i, 11 + i) for i in range(6)]
        m15 = resample(bars, "M15")
        self.assertEqual(len(m15), 2)
        self.assertEqual(m15[0].open, bars[0].open)
        self.assertEqual(m15[0].close, bars[2].close)
        self.assertEqual(m15[0].high, max(b.high for b in bars[:3]))
        self.assertEqual(m15[0].low, min(b.low for b in bars[:3]))

    def test_resample_buckets_are_aligned_not_sequential(self):
        # Starting mid-bucket must not create a bucket straddling the boundary.
        start = datetime(2024, 6, 3, 12, 10, tzinfo=UTC)
        bars = [Candle(start + timedelta(minutes=5 * i), 1, 2, 0.5, 1.5) for i in range(4)]
        m15 = resample(bars, "M15")
        self.assertEqual(m15[0].ts, datetime(2024, 6, 3, 12, 0, tzinfo=UTC))
        self.assertEqual(m15[1].ts, datetime(2024, 6, 3, 12, 15, tzinfo=UTC))

    def test_align_index_excludes_unclosed_bar(self):
        bars = [Candle(datetime(2024, 6, 3, h, tzinfo=UTC), 1, 2, 0, 1) for h in range(5)]
        self.assertEqual(align_index(bars, datetime(2024, 6, 3, 2, 30, tzinfo=UTC)), 2)
        self.assertEqual(align_index(bars, datetime(2024, 6, 3, 0, 0, tzinfo=UTC)), -1)

    def test_candle_rejects_naive_timestamp(self):
        with self.assertRaises(ValueError):
            Candle(datetime(2024, 6, 3, 12, 0), 1, 2, 0, 1)


class TestSessions(unittest.TestCase):
    def test_killzones_follow_dst(self):
        # 14:05 UTC is 10:05 NY in July (EDT) but 09:05 NY in January (EST).
        summer = datetime(2024, 7, 10, 14, 5, tzinfo=UTC)
        winter = datetime(2024, 1, 10, 14, 5, tzinfo=UTC)
        self.assertIn("sb_am", active_killzones(summer))
        self.assertNotIn("sb_am", active_killzones(winter))

    def test_asia_window_wraps_midnight(self):
        self.assertTrue(KILLZONES["asia"].wraps_midnight)
        # 02:00 UTC in January = 21:00 NY the previous day.
        self.assertIn("asia", active_killzones(datetime(2024, 1, 11, 2, 0, tzinfo=UTC)))

    def test_trading_day_rolls_at_17_ny(self):
        before = datetime(2024, 6, 3, 20, 0, tzinfo=UTC)  # 16:00 NY
        after = datetime(2024, 6, 3, 22, 0, tzinfo=UTC)   # 18:00 NY
        self.assertEqual(trading_day(before), "2024-06-03")
        self.assertEqual(trading_day(after), "2024-06-04")


class TestMarketModel(unittest.TestCase):
    def test_swing_is_published_only_after_confirmation(self):
        m = MarketModel(ModelConfig(swing_lookback=2))
        seq = [(1, 2, 0, 1.5), (2, 3, 1, 2.5), (3, 9, 2, 8),  # index 2 = the peak
               (4, 5, 3, 4), (3, 4, 2, 3), (2, 3, 1, 2)]
        for i, (o, h, l, cl) in enumerate(seq):
            m.update(c(i * 5, o, h, l, cl))
            if i < 4:
                self.assertEqual([s.idx for s in m.swing_highs], [],
                                 f"swing leaked at bar {i}, before its confirmation bar")
        self.assertIn(2, [s.idx for s in m.swing_highs])
        self.assertEqual(m.swing_highs[0].confirmed_idx, 4)

    def test_bullish_fvg_detected_and_filled(self):
        m = MarketModel(ModelConfig(atr_period=2, fvg_min_atr=0.0))
        m.update(c(0, 100, 101, 99, 100))     # bar 0: high 101
        m.update(c(5, 100, 105, 100, 104))
        m.update(c(10, 104, 108, 103, 107))   # low 103 > 101 -> gap 101..103
        gaps = m.live_fvgs(BULL)
        self.assertEqual(len(gaps), 1)
        self.assertAlmostEqual(gaps[0].bottom, 101)
        self.assertAlmostEqual(gaps[0].top, 103)
        self.assertAlmostEqual(gaps[0].ce, 102)
        m.update(c(15, 107, 107, 100, 100.5))  # trades all the way through
        self.assertEqual(m.live_fvgs(BULL), [])

    def test_bearish_fvg_detected(self):
        m = MarketModel(ModelConfig(atr_period=2, fvg_min_atr=0.0))
        m.update(c(0, 100, 101, 99, 99.5))
        m.update(c(5, 99, 99, 94, 95))
        m.update(c(10, 95, 97, 93, 94))       # high 97 < low 99 -> gap 97..99
        gaps = m.live_fvgs(BEAR)
        self.assertEqual(len(gaps), 1)
        self.assertAlmostEqual(gaps[0].bottom, 97)
        self.assertAlmostEqual(gaps[0].top, 99)

    def test_sweep_needs_close_back_inside(self):
        m = MarketModel(ModelConfig(swing_lookback=1, atr_period=2))
        # Build and confirm a swing high at 110.
        for i, (o, h, l, cl) in enumerate([(100, 102, 99, 101), (101, 110, 100, 109),
                                           (109, 109, 104, 105), (105, 106, 103, 104)]):
            m.update(c(i * 5, o, h, l, cl))
        self.assertTrue(any(abs(p.price - 110) < 1e-9 for p in m.live_pools("BSL")))
        # Wick above 110 but close below = liquidity taken and rejected.
        m.update(c(20, 106, 112, 105, 107))
        self.assertEqual(len(m.sweeps), 1)
        self.assertEqual(m.sweeps[0].direction, BEAR)

    def test_close_through_level_consumes_rather_than_sweeps(self):
        m = MarketModel(ModelConfig(swing_lookback=1, atr_period=2))
        for i, (o, h, l, cl) in enumerate([(100, 102, 99, 101), (101, 110, 100, 109),
                                           (109, 109, 104, 105), (105, 106, 103, 104)]):
            m.update(c(i * 5, o, h, l, cl))
        m.update(c(20, 106, 115, 105, 114))  # closes above: a run, not a sweep
        self.assertEqual(m.sweeps, [])
        pool = next(p for p in m.pools if abs(p.price - 110) < 1e-9)
        self.assertIsNotNone(pool.consumed_idx)

    def test_premium_discount_split_at_equilibrium(self):
        m = MarketModel(ModelConfig(swing_lookback=1, atr_period=2))
        seq = [(100, 101, 99, 100), (100, 100, 90, 91), (91, 95, 90, 94),
               (94, 110, 93, 109), (109, 110, 104, 105), (105, 107, 103, 104)]
        for i, row in enumerate(seq):
            m.update(c(i * 5, *row))
        dr = m.dealing_range()
        self.assertIsNotNone(dr)
        self.assertEqual(dr.zone(dr.low + dr.size * 0.25), "discount")
        self.assertEqual(dr.zone(dr.low + dr.size * 0.75), "premium")
        self.assertAlmostEqual(dr.retracement(dr.equilibrium), 0.5)

    def test_structure_labels_choch_when_direction_flips(self):
        m = MarketModel(ModelConfig(swing_lookback=1, atr_period=3, fvg_min_atr=0.0))
        candles = synthetic_m5(400, seed=3)
        for k in candles:
            m.update(k)
        kinds = {e.kind for e in m.events}
        self.assertTrue(kinds.issubset({"BOS", "CHOCH"}))
        self.assertTrue(m.events, "no structure events on 400 bars of data")
        for a, b in zip(m.events, m.events[1:]):
            if a.direction != b.direction:
                self.assertEqual(b.kind, "CHOCH",
                                 "a break against the prevailing trend must be a CHOCH")


class TestPipelineAndBacktest(unittest.TestCase):
    def _cfg(self) -> Config:
        cfg = Config.default()
        cfg.risk.starting_equity = 10_000.0
        return cfg

    def test_no_signal_outside_killzone(self):
        from ictgold.pipeline import EntryPipeline
        cfg = self._cfg()
        # 23:00 UTC = 19:00 NY: no configured killzone is open.
        candles = synthetic_m5(600, seed=5, start=datetime(2024, 3, 4, 23, 0, tzinfo=UTC))
        ltf, htf = MarketModel(cfg.model), MarketModel(cfg.htf_model)
        for k in candles[:200]:
            ltf.update(k)
        for k in resample(candles[:200], cfg.htf):
            htf.update(k)
        quiet = [k for k in candles[:200] if not active_killzones(k.ts)]
        self.assertTrue(quiet)
        results = EntryPipeline(cfg).run(ltf, htf, 10_000.0)
        # The final bar drives the evaluation; assert the stage that vetoes.
        if not active_killzones(ltf.bars[-1].ts, ["london", "ny_am", "ny_pm",
                                                  "sb_am", "sb_pm", "sb_london"]):
            self.assertTrue(all(r.veto_stage == "time" for r in results))

    def test_order_never_fills_on_its_signal_bar(self):
        cfg = self._cfg()
        res = Backtester(cfg).run(synthetic_m5(8000, seed=9))
        for t in res.trades:
            self.assertGreater(t.entry_ts, t.signal_ts,
                               "an order filled on the bar that generated it")

    def test_stop_wins_when_a_bar_contains_both_levels(self):
        cfg = self._cfg()
        cfg.costs.spread = 0.0
        cfg.costs.slippage_entry = 0.0
        cfg.costs.slippage_stop = 0.0
        cfg.costs.commission_per_lot = 0.0
        from ictgold.backtest import OpenPosition, Trade
        t = Trade(setup="x", direction=BULL, signal_ts=c(0, 1, 1, 1, 1).ts,
                  entry_ts=c(0, 1, 1, 1, 1).ts, exit_ts=None, entry=2000.0,
                  stop=1990.0, target=2020.0, lots=0.1, risk_usd=100.0, score=0.6,
                  killzone="ny_am", dow="Mon", day="2024-06-03")
        pos = OpenPosition(t, stop=1990.0, target=2020.0, remaining_lots=0.1, entry_bar=0)
        bt = Backtester(cfg)
        equity, closed = bt._manage(pos, c(5, 2000, 2025, 1985, 2010), 1, 10_000.0)
        self.assertTrue(closed)
        self.assertEqual(t.exit_reason, "stop")
        self.assertAlmostEqual(t.r, -1.0, places=6)

    def test_costs_make_a_stop_out_worse_than_minus_one_r(self):
        cfg = self._cfg()
        from ictgold.backtest import OpenPosition, Trade
        t = Trade(setup="x", direction=BULL, signal_ts=c(0, 1, 1, 1, 1).ts,
                  entry_ts=c(0, 1, 1, 1, 1).ts, exit_ts=None, entry=2000.0,
                  stop=1990.0, target=2020.0, lots=0.1, risk_usd=100.0, score=0.6,
                  killzone="ny_am", dow="Mon", day="2024-06-03")
        pos = OpenPosition(t, stop=1990.0, target=2020.0, remaining_lots=0.1, entry_bar=0)
        Backtester(cfg)._manage(pos, c(5, 2000, 2005, 1985, 1995), 1, 10_000.0)
        self.assertLess(t.r, -1.0)

    def test_risk_per_trade_is_respected_by_position_size(self):
        cfg = self._cfg()
        res = Backtester(cfg).run(synthetic_m5(8000, seed=4))
        for t in res.trades:
            planned = abs(t.entry - t.stop) * cfg.symbol.contract_size * t.lots
            self.assertLessEqual(planned, t.risk_usd * 1.35,
                                 "position size exceeds the configured risk")

    def test_stop_respects_the_cost_floor(self):
        cfg = self._cfg()
        floor = (cfg.costs.spread + cfg.costs.slippage_entry + cfg.costs.slippage_stop) \
            * cfg.risk.min_stop_cost_mult
        res = Backtester(cfg).run(synthetic_m5(8000, seed=4))
        for t in res.trades:
            self.assertGreaterEqual(abs(t.entry - t.stop), floor * 0.95)

    def test_disabled_setup_produces_nothing(self):
        cfg = self._cfg()
        for s in cfg.setups:
            s.enabled = False
        res = Backtester(cfg).run(synthetic_m5(3000, seed=2))
        self.assertEqual(res.trades, [])
        self.assertEqual(res.signals_generated, 0)

    def test_daily_trade_cap_is_enforced(self):
        cfg = self._cfg()
        cfg.risk.max_trades_per_day = 1
        res = Backtester(cfg).run(synthetic_m5(20000, seed=6))
        per_day: dict[str, int] = {}
        for t in res.trades:
            per_day[t.day] = per_day.get(t.day, 0) + 1
        self.assertTrue(all(v <= 1 for v in per_day.values()), per_day)


class TestMetrics(unittest.TestCase):
    def _trades(self, rs: list[float]):
        from ictgold.backtest import Trade
        out = []
        for i, r in enumerate(rs):
            t = Trade(setup="s", direction=BULL, signal_ts=c(i, 1, 1, 1, 1).ts,
                      entry_ts=c(i, 1, 1, 1, 1).ts, exit_ts=c(i + 1, 1, 1, 1, 1).ts,
                      entry=2000, stop=1990, target=2020, lots=0.1, risk_usd=100,
                      score=0.6, killzone="ny_am", dow="Mon", day="2024-06-03")
            t.r = r
            t.pnl = r * 100
            out.append(t)
        return out

    def test_summary_arithmetic(self):
        s = summarize(self._trades([2.0, -1.0, -1.0, 3.0]), 10_000)
        self.assertEqual(s.trades, 4)
        self.assertEqual(s.wins, 2)
        self.assertAlmostEqual(s.win_rate, 0.5)
        self.assertAlmostEqual(s.total_r, 3.0)
        self.assertAlmostEqual(s.expectancy_r, 0.75)
        self.assertAlmostEqual(s.profit_factor, 500 / 200)

    def test_max_drawdown_measures_peak_to_trough(self):
        s = summarize(self._trades([1.0, -1.0, -1.0, -1.0, 2.0]), 10_000)
        self.assertAlmostEqual(s.max_dd_r, 3.0)
        self.assertEqual(s.max_consec_losses, 3)

    def test_required_sample_grows_with_noise(self):
        tight = required_sample(0.2, 1.0)
        noisy = required_sample(0.2, 2.0)
        self.assertGreater(noisy, tight)
        self.assertEqual(required_sample(-0.1, 1.0), -1)

    def test_no_t_statistic_below_the_sample_floor(self):
        few = summarize(self._trades([-1.2, -1.19]), 10_000)
        self.assertEqual(few.t_stat, 0.0, "a t-stat from 2 trades is a division artefact")
        many = summarize(self._trades([2.0, -1.0] * MIN_N_FOR_T), 10_000)
        self.assertNotEqual(many.t_stat, 0.0)

    def test_walk_forward_oos_segments_do_not_overlap(self):
        folds = walk_forward_folds(10_000, folds=5, is_ratio=0.7)
        self.assertEqual(len(folds), 5)
        for f in folds:
            self.assertLess(f.is_start, f.is_end)
            self.assertEqual(f.oos_start, f.is_end)
        for a, b in zip(folds, folds[1:]):
            self.assertLessEqual(a.oos_end, b.oos_start)


class TestDataIO(unittest.TestCase):
    def test_csv_roundtrip(self):
        import tempfile
        candles = synthetic_m5(50, seed=1)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            save_csv(candles, p)
            back = load_csv(p)
        self.assertEqual(len(back), len(candles))
        self.assertAlmostEqual(back[0].open, candles[0].open)
        self.assertEqual(back[0].ts, candles[0].ts)

    def test_broker_timezone_is_converted_to_utc(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.csv"
            p.write_text("time,open,high,low,close,volume\n"
                         "2024-06-03 10:00:00,2300,2301,2299,2300.5,10\n", encoding="utf-8")
            utc = load_csv(p, source_tz="UTC")[0]
            broker = load_csv(p, source_tz="Europe/Athens")[0]  # UTC+3 in June
        self.assertEqual(utc.ts.hour, 10)
        self.assertEqual(broker.ts.hour, 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
