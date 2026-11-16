"""
End-to-end cash forecasting pipeline.

    1. Generate synthetic data (if missing)
    2. Sequential staging: unify two GL sources -> opening balance (LAG) -> customer flow -> position + implied CIT
    3. Backtest Prophet vs a seasonal-naive benchmark on May-June 2026, per branch x currency
    4. Check the position-reconstruction layer: forecast flow + actual CIT vs actual vault closing
    5. Live run from 30 Jun 2026: 10-business-day position outlook and replenishment alerts
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.forecast import days_until_floor, fit_predict, reconstruct_position, seasonal_naive
from src.pipeline import connect, run_staging

ROOT = Path(__file__).parent
REPORTS, FIG = ROOT / "reports", ROOT / "reports" / "figures"
BACKTEST_ORIGIN = pd.Timestamp("2026-04-30")
LIVE_HORIZON = 10
ACCENT = "#E8A54B"
plt.rcParams.update({"figure.dpi": 120, "axes.spines.top": False, "axes.spines.right": False})


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    if not (ROOT / "data/raw/teller_daily.parquet").exists():
        subprocess.run([sys.executable, str(ROOT / "data/generate_synthetic.py")], check=True)
    con = connect()
    pos = run_staging(con)
    pos["bal_date"] = pd.to_datetime(pos.bal_date)
    src_counts = pos.groupby("gl_source").size().to_dict()
    print(f"cash position rows: {len(pos):,}  by GL source {src_counts}")

    rows, live, examples = [], [], {}
    t0 = time.time()
    for (b, c), g in pos.groupby(["branch_code", "currency"]):
        g = g.sort_values("bal_date")
        hist = g[g.bal_date <= BACKTEST_ORIGIN].rename(columns={"bal_date": "ds", "net_outflow": "y"})[["ds", "y"]]
        test = g[g.bal_date > BACKTEST_ORIGIN]
        fc = fit_predict(hist, test.bal_date)
        naive = seasonal_naive(hist, test.bal_date)
        y = test.net_outflow.to_numpy()
        mae_p, mae_n = np.mean(np.abs(y - fc.yhat.to_numpy())), np.mean(np.abs(y - naive))
        coverage = np.mean((y >= fc.yhat_lower.to_numpy()) & (y <= fc.yhat_upper.to_numpy()))

        # position layer check: rebuild the closing balance using forecast flow + ACTUAL CIT
        opening = g[g.bal_date <= BACKTEST_ORIGIN].vault_closing.iloc[-1]
        rebuilt = reconstruct_position(opening, fc.yhat.to_numpy(), test.implied_cit.to_numpy())
        pos_err = np.mean(np.abs(rebuilt[:10] - test.vault_closing.to_numpy()[:10])) / g.vault_closing.mean()
        rows.append(dict(branch_code=b, currency=c, mae_prophet=mae_p, mae_naive=mae_n,
                         rel_mae=mae_p / mae_n, interval_coverage=coverage, position_err_10d_pct_of_vault=pos_err))
        if (b, c) in [("B100", "NGN"), ("B101", "USD")]:
            examples[(b, c)] = (g, fc, naive)

        # live outlook from the last available day
        full = g.rename(columns={"bal_date": "ds", "net_outflow": "y"})[["ds", "y"]]
        fut = pd.Series(pd.bdate_range(g.bal_date.max() + pd.DateOffset(days=1), periods=LIVE_HORIZON))
        lf = fit_predict(full, fut)
        proj = reconstruct_position(g.vault_closing.iloc[-1], lf.yhat.to_numpy())
        floor = g.vault_closing.quantile(0.10)
        d = days_until_floor(proj, floor)
        live.append(dict(branch_code=b, currency=c, closing_30jun=round(g.vault_closing.iloc[-1], 2),
                         projected_day10=round(proj[-1], 2), floor_p10=round(floor, 2),
                         breach_in_business_days=d, action="REPLENISH" if d is not None and d <= 5 else ("WATCH" if d else "OK")))
    print(f"fitted {2 * len(rows)} Prophet models in {time.time() - t0:.0f}s")

    bt = pd.DataFrame(rows)
    bt.round(4).to_csv(REPORTS / "backtest_by_series.csv", index=False)
    lv = pd.DataFrame(live).sort_values(["action", "breach_in_business_days"])
    lv.to_csv(REPORTS / "replenishment_outlook.csv", index=False)
    by_ccy = bt.groupby("currency").agg(series=("rel_mae", "size"), rel_mae_median=("rel_mae", "median"),
                                        coverage=("interval_coverage", "mean"), position_err=("position_err_10d_pct_of_vault", "median")).round(3)
    print(by_ccy)
    print(lv.action.value_counts().to_dict())

    # figures
    for (b, c), (g, fc, naive) in examples.items():
        fig, ax = plt.subplots(figsize=(8, 3.6))
        h = g[(g.bal_date > BACKTEST_ORIGIN - pd.DateOffset(days=60))]
        ax.plot(h.bal_date, h.net_outflow, color="#8A8A8A", lw=1.2, label="actual net outflow")
        ax.plot(fc.ds, fc.yhat, color=ACCENT, lw=2, label="Prophet")
        ax.fill_between(fc.ds, fc.yhat_lower, fc.yhat_upper, color=ACCENT, alpha=0.2, label="80% interval")
        ax.plot(fc.ds, naive, color="#5B8DEF", lw=1, ls="--", label="seasonal naive")
        ax.axvline(BACKTEST_ORIGIN, color="grey", lw=1, ls=":")
        ax.set_title(f"Branch {b} · {c}: backtest from {BACKTEST_ORIGIN:%d %b %Y}"); ax.legend(fontsize=8, ncol=4, loc="upper left")
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d %b")); ax.set_ylabel(f"{c}")
        fig.tight_layout(); fig.savefig(FIG / f"backtest_{b}_{c}.png"); plt.close(fig)

    g = examples[("B100", "NGN")][0]
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.bar(g.bal_date, g.implied_cit / 1e6, width=1.5, color=np.where(g.implied_cit > 0, "#3C8D5A", "#D9534F"))
    ax.set_ylabel("implied CIT (NGN m)"); ax.set_title("Recovered cash-in-transit: replenishments (+) and evacuations (−), branch B100")
    fig.tight_layout(); fig.savefig(FIG / "implied_cit_B100.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.8))
    ax.hist(bt.rel_mae, bins=15, color=ACCENT)
    ax.axvline(1, color="grey", ls="--"); ax.set_xlabel("Prophet MAE / seasonal-naive MAE  (<1 = Prophet better)")
    ax.set_ylabel("series"); ax.set_title("Backtest across all branch × currency series")
    fig.tight_layout(); fig.savefig(FIG / "relative_mae.png"); plt.close(fig)

    metrics = {
        "series": int(len(bt)), "branches": int(bt.branch_code.nunique()), "currencies": sorted(bt.currency.unique().tolist()),
        "gl_sources_rows": src_counts,
        "backtest_origin": str(BACKTEST_ORIGIN.date()),
        "share_series_beating_naive": round(float((bt.rel_mae < 1).mean()), 4),
        "median_mae_improvement_vs_naive": round(float(1 - bt.rel_mae.median()), 4),
        "interval_coverage_mean": round(float(bt.interval_coverage.mean()), 4),
        "position_error_10d_median_pct_of_vault": round(float(bt.position_err_10d_pct_of_vault.median()), 4),
        "by_currency": by_ccy.reset_index().to_dict(orient="records"),
        "live_outlook_actions": lv.action.value_counts().to_dict(),
    }
    (REPORTS / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    return metrics


if __name__ == "__main__":
    main()
