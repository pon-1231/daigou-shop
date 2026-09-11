"""Session / killzone clock.

Every ICT time reference is New York local time, so DST is handled by the OS
tz database rather than a hardcoded UTC offset. Getting this wrong silently
shifts every killzone by an hour for half the year and quietly destroys a
backtest, so it lives in one small, tested module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Window:
    """A daily time window in New York local time.

    Windows that wrap past midnight (Asia) are supported: `start > end` means
    the window spans into the next calendar day.
    """

    name: str
    start: time
    end: time

    def contains(self, ts_utc: datetime) -> bool:
        local = ts_utc.astimezone(NY).time()
        if self.start <= self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end

    @property
    def wraps_midnight(self) -> bool:
        return self.start > self.end


def _w(name: str, start: str, end: str) -> Window:
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    return Window(name, time(sh, sm), time(eh, em))


# Canonical ICT killzones, New York local time.
KILLZONES: dict[str, Window] = {
    "asia": _w("asia", "20:00", "00:00"),
    "london": _w("london", "02:00", "05:00"),
    "ny_am": _w("ny_am", "08:30", "11:00"),
    "london_close": _w("london_close", "10:00", "12:00"),
    "ny_pm": _w("ny_pm", "13:30", "16:00"),
    # Silver Bullet: one-hour windows with the tightest historical edge.
    "sb_london": _w("sb_london", "03:00", "04:00"),
    "sb_am": _w("sb_am", "10:00", "11:00"),
    "sb_pm": _w("sb_pm", "14:00", "15:00"),
}

# Ranges whose highs/lows act as liquidity pools for the following session.
RANGE_WINDOWS: dict[str, Window] = {
    "asia_range": _w("asia_range", "20:00", "00:00"),
    "london_range": _w("london_range", "02:00", "05:00"),
}


def active_killzones(ts_utc: datetime, names: list[str] | None = None) -> list[str]:
    keys = names if names is not None else list(KILLZONES)
    return [k for k in keys if k in KILLZONES and KILLZONES[k].contains(ts_utc)]


def in_any_killzone(ts_utc: datetime, names: list[str]) -> bool:
    return any(KILLZONES[n].contains(ts_utc) for n in names if n in KILLZONES)


def in_macro(ts_utc: datetime, lead: int = 10, lag: int = 10) -> bool:
    """ICT 'macro': the :50-:10 window straddling each hour turn."""
    minute = ts_utc.astimezone(NY).minute
    return minute >= 60 - lead or minute < lag


def ny_time(ts_utc: datetime) -> datetime:
    return ts_utc.astimezone(NY)


def trading_day(ts_utc: datetime, roll_hour: int = 17) -> str:
    """Label the trading day a timestamp belongs to (rolls at 17:00 NY)."""
    local = ts_utc.astimezone(NY)
    if local.hour >= roll_hour:
        local = local + timedelta(days=1)
    return local.strftime("%Y-%m-%d")


def day_of_week(ts_utc: datetime) -> str:
    return ts_utc.astimezone(NY).strftime("%a")


def is_weekend_gap(prev: datetime, cur: datetime) -> bool:
    return (cur - prev) > timedelta(hours=6)
