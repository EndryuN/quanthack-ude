"""Tracks equity history and computes the competition's own metrics:
MaxDD and the non-annualized 15-minute Sharpe. Persists to disk so a restart
mid-round does not lose the equity curve (Sharpe needs the full series)."""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field

FIFTEEN_MIN = 15 * 60


@dataclass
class Metrics:
    equity: float
    peak_equity: float
    max_drawdown: float       # fraction, e.g. 0.12 == 12%
    cur_drawdown: float
    sharpe_15m: float
    n_samples: int
    return_pct: float         # vs initial equity


@dataclass
class StateTracker:
    initial_equity: float
    state_dir: str = "state"
    _samples: list = field(default_factory=list)   # [[epoch, equity], ...] @15m grid
    _peak: float = 0.0
    _max_dd: float = 0.0
    _last_bucket: int = -1

    def __post_init__(self):
        os.makedirs(self.state_dir, exist_ok=True)
        self._peak = self.initial_equity
        self._load()

    @property
    def _path(self):
        return os.path.join(self.state_dir, "equity_samples.json")

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    d = json.load(f)
                self._samples = d.get("samples", [])
                self._peak = d.get("peak", self.initial_equity)
                self._max_dd = d.get("max_dd", 0.0)
                self._last_bucket = d.get("last_bucket", -1)
            except Exception:
                pass

    def _save(self):
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"samples": self._samples, "peak": self._peak,
                       "max_dd": self._max_dd, "last_bucket": self._last_bucket}, f)
        os.replace(tmp, self._path)

    def update(self, equity: float, now: float | None = None) -> Metrics:
        now = now if now is not None else time.time()

        # peak / drawdown update continuously (MaxDD is a continuous max)
        if equity > self._peak:
            self._peak = equity
        cur_dd = (self._peak - equity) / self._peak if self._peak > 0 else 0.0
        if cur_dd > self._max_dd:
            self._max_dd = cur_dd

        # 15-min grid sampling for Sharpe (one sample per 15-min bucket)
        bucket = int(now // FIFTEEN_MIN)
        if bucket != self._last_bucket:
            self._samples.append([now, equity])
            self._last_bucket = bucket
            self._save()

        return Metrics(
            equity=equity,
            peak_equity=self._peak,
            max_drawdown=self._max_dd,
            cur_drawdown=cur_dd,
            sharpe_15m=self.sharpe(),
            n_samples=len(self._samples),
            return_pct=(equity - self.initial_equity) / self.initial_equity,
        )

    def sharpe(self) -> float:
        """Non-annualized Sharpe from 15-min equity returns, per the rules.
        Std==0 -> 0. Mirrors the competition formula exactly."""
        eq = [s[1] for s in self._samples]
        if len(eq) < 2:
            return 0.0
        rets = [(eq[i] - eq[i - 1]) / eq[i - 1] for i in range(1, len(eq))
                if eq[i - 1] != 0]
        if len(rets) < 1:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        return mean / std

    def n_return_obs(self) -> int:
        """Number of valid 15-min return observations (rules cap Sharpe rank at
        50 if < 8)."""
        return max(0, len(self._samples) - 1)
