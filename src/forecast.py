"""
Prophet forecasting of net customer cash flow per branch x currency,
plus a deterministic vault-position reconstruction layer on top.

Design decision: forecast the *customer-driven* flow only. CIT movements are
operational decisions (when treasury sends a van), not demand, so they are
added back deterministically instead of being forecast.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from prophet import Prophet

logging.getLogger("prophet").setLevel(logging.ERROR)
logging.getLogger("cmdstanpy").setLevel(logging.ERROR)

NG_HOLIDAYS = pd.DataFrame({
    "holiday": "ng_public_holiday",
    "ds": pd.to_datetime([
        "2024-01-01", "2024-03-29", "2024-04-01", "2024-04-10", "2024-05-01", "2024-06-12", "2024-06-17", "2024-10-01", "2024-12-25", "2024-12-26",
        "2025-01-01", "2025-03-31", "2025-04-18", "2025-04-21", "2025-05-01", "2025-06-06", "2025-06-12", "2025-10-01", "2025-12-25", "2025-12-26",
        "2026-01-01", "2026-03-20", "2026-04-03", "2026-04-06", "2026-05-01", "2026-05-27", "2026-06-12",
    ]),
    "lower_window": -1, "upper_window": 3,  # rush before, catch-up after
})


def _regressors(ds: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({
        "ds": ds,
        "month_end": ((ds.dt.day >= 25) | (ds.dt.day <= 2)).astype(int),
        "festive": ((ds.dt.month == 12) & (ds.dt.day >= 10)).astype(int),
    })


def fit_predict(hist: pd.DataFrame, future_dates: pd.Series) -> pd.DataFrame:
    """hist: columns ds, y. Returns ds, yhat, yhat_lower, yhat_upper for future_dates."""
    m = Prophet(holidays=NG_HOLIDAYS, weekly_seasonality=True, yearly_seasonality=True, daily_seasonality=False,
                # additive: net flow crosses zero, so multiplicative seasonality is unstable
                seasonality_mode="additive", changepoint_prior_scale=0.05, interval_width=0.8)
    m.add_regressor("month_end", mode="additive")
    m.add_regressor("festive", mode="additive")
    m.fit(hist.merge(_regressors(hist.ds), on="ds"))
    return m.predict(_regressors(pd.Series(pd.to_datetime(future_dates.values))))[["ds", "yhat", "yhat_lower", "yhat_upper"]]


def seasonal_naive(hist: pd.DataFrame, future_dates: pd.Series) -> np.ndarray:
    """Benchmark: same weekday, average of the last 4 weeks."""
    h = hist.assign(dow=hist.ds.dt.dayofweek).tail(20)
    by_dow = h.groupby("dow").y.mean()
    return future_dates.dt.dayofweek.map(by_dow).fillna(h.y.mean()).to_numpy()


def wape(actual, pred) -> float:
    return float(np.sum(np.abs(actual - pred)) / np.sum(np.abs(actual)))


def reconstruct_position(opening: float, net_outflow_fc: np.ndarray, planned_cit: np.ndarray | None = None) -> np.ndarray:
    """Deterministic layer: projected closing vault = opening - cumulative forecast outflow + planned CIT."""
    cit = np.zeros_like(net_outflow_fc) if planned_cit is None else planned_cit
    return opening + np.cumsum(cit - net_outflow_fc)


def days_until_floor(position: np.ndarray, floor: float) -> int | None:
    below = np.where(position < floor)[0]
    return int(below[0]) + 1 if len(below) else None
