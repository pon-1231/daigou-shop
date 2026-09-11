"""ictgold - an auditable ICT entry pipeline for XAUUSD.

Zero third-party dependencies on purpose: it must run on your laptop, on a
VPS, and inside a web request handler without a build step.
"""

from .config import Config, RiskConfig, SetupSpec, SymbolSpec, CostModel
from .core import Candle, resample, tf_minutes
from .state import MarketModel, ModelConfig
from .pipeline import EntryPipeline, PipelineResult, Signal, compute_bias
from .backtest import Backtester, BacktestResult, Trade
from . import metrics

__version__ = "0.1.0"
__all__ = [
    "Config", "RiskConfig", "SetupSpec", "SymbolSpec", "CostModel",
    "Candle", "resample", "tf_minutes", "MarketModel", "ModelConfig",
    "EntryPipeline", "PipelineResult", "Signal", "compute_bias",
    "Backtester", "BacktestResult", "Trade", "metrics",
]
