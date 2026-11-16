"""
Synthetic data for branch cash demand forecasting.

    branches.parquet          branch master: region, source system (LEGACY / ACQUIRED), FX-enabled
    teller_daily.parquet      daily customer cash deposits & withdrawals per branch x currency
    gl_vault_legacy.parquet   vault closing balances for LEGACY branches   (one schema)
    gl_vault_acquired.parquet vault closing balances for ACQUIRED branches (different schema)

Realistic quirks:
  * two general-ledger sources with different column names and currency codes,
    the situation after a bank merger
  * cash-in-transit (CIT) replenishments and evacuations are NOT recorded in the
    teller data. They only show up as unexplained jumps in the vault balance,
    so they have to be recovered as a residual (implied CIT)
  * weekly cycle, month-end salary peaks, December festive season, and
    Nigerian public holidays (Easter, Eid, Independence Day, Christmas)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

START, END = "2024-01-01", "2026-06-30"
CCY = ["NGN", "USD", "GBP", "EUR"]
CCY_SCALE = {"NGN": 60_000_000, "USD": 40_000, "GBP": 9_000, "EUR": 8_000}
HOLIDAYS = pd.to_datetime([
    "2024-01-01", "2024-03-29", "2024-04-01", "2024-04-10", "2024-05-01", "2024-06-12", "2024-06-17", "2024-10-01", "2024-12-25", "2024-12-26",
    "2025-01-01", "2025-03-31", "2025-04-18", "2025-04-21", "2025-05-01", "2025-06-06", "2025-06-12", "2025-10-01", "2025-12-25", "2025-12-26",
    "2026-01-01", "2026-03-20", "2026-04-03", "2026-04-06", "2026-05-01", "2026-05-27", "2026-06-12",
])


def generate(n_branches: int = 20, n_fx: int = 8, seed: int = 4):
    rng = np.random.default_rng(seed)
    days = pd.date_range(START, END, freq="D")
    open_days = days[(days.dayofweek < 5) & ~days.isin(HOLIDAYS)]
    br = pd.DataFrame({
        "branch_code": [f"B{100 + i}" for i in range(n_branches)],
        "region": rng.choice(["LAGOS_MAINLAND", "LAGOS_ISLAND", "ABUJA", "SOUTH_WEST", "SOUTH_SOUTH", "NORTH"], n_branches),
        "source_system": np.where(np.arange(n_branches) < int(n_branches * 0.65), "LEGACY", "ACQUIRED"),
        "fx_enabled": np.arange(n_branches) < n_fx,
    })
    teller, legacy, acquired = [], [], []
    t = np.arange(len(open_days))
    dom = open_days.day.to_numpy()
    dow = open_days.dayofweek.to_numpy()
    month = open_days.month.to_numpy()
    after_hol = (open_days.shift(-1, freq="D").isin(HOLIDAYS) | open_days.shift(-3, freq="D").isin(HOLIDAYS))
    before_hol = open_days.shift(1, freq="D").isin(HOLIDAYS)

    for _, b in br.iterrows():
        size = rng.lognormal(0, 0.45)
        for ccy in (CCY if b.fx_enabled else ["NGN"]):
            sc = CCY_SCALE[ccy] * size
            trend = 1 + rng.normal(0.08, 0.05) * t / 365
            wk = np.array([1.25, 0.95, 0.9, 0.95, 1.15])[dow]
            mend = 1 + 0.6 * ((dom >= 25) | (dom <= 2)) * (ccy == "NGN")
            dec = 1 + 0.5 * (month == 12) * (dom >= 10)
            hol = 1 + 0.45 * before_hol + 0.3 * after_hol
            base = sc * trend * wk * hol
            withdrawals = base * mend * dec * rng.lognormal(0, 0.18, len(t))
            deposits = base * (1 + 0.25 * (dom >= 28)) * rng.lognormal(0, 0.2, len(t)) * (0.97 if ccy == "NGN" else 1.0)
            teller.append(pd.DataFrame({"branch_code": b.branch_code, "txn_date": open_days, "currency": ccy,
                                        "cash_deposits": deposits.round(2), "cash_withdrawals": withdrawals.round(2)}))
            # vault: replenish (CIT in) when balance falls below a floor, evacuate above a ceiling
            floor, ceil = sc * 2.5, sc * 9
            bal = np.empty(len(t)); cur = sc * 5
            for i in range(len(t)):
                cur += deposits[i] - withdrawals[i]
                if cur < floor:
                    cur += sc * rng.uniform(4, 6)
                elif cur > ceil:
                    cur -= sc * rng.uniform(3, 5)
                bal[i] = cur
            if b.source_system == "LEGACY":
                legacy.append(pd.DataFrame({"BRANCH_CODE": b.branch_code, "GL_DATE": open_days, "CCY_CODE": ccy,
                                            "GL_ACCT": "VAULT_CASH", "CLOSING_BAL": bal.round(2)}))
            else:
                code = {"NGN": "566", "USD": "840", "GBP": "826", "EUR": "978"}[ccy]  # ISO numeric codes
                acquired.append(pd.DataFrame({"sol_id": b.branch_code.replace("B", "S"), "value_date": open_days,
                                              "crncy_num": code, "gl_sub_head": "10101", "eod_balance": bal.round(2)}))
    return br, pd.concat(teller), pd.concat(legacy), pd.concat(acquired)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--branches", type=int, default=20)
    ap.add_argument("--seed", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "raw")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    br, teller, leg, acq = generate(a.branches, seed=a.seed)
    for name, df in dict(branches=br, teller_daily=teller, gl_vault_legacy=leg, gl_vault_acquired=acq).items():
        df.to_parquet(a.out / f"{name}.parquet", index=False)
        print(f"{name:18s} {len(df):>9,} rows")


if __name__ == "__main__":
    main()
