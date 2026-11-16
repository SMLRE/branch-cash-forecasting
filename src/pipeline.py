"""Run the sequential staging tables in DuckDB."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW, SQL = ROOT / "data" / "raw", ROOT / "sql" / "staging"
STAGES = ["stg_01_vault_unified", "stg_02_opening_balance", "stg_03_customer_flow", "stg_04_cash_position"]


def connect():
    con = duckdb.connect()
    for t in ["branches", "teller_daily", "gl_vault_legacy", "gl_vault_acquired"]:
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{RAW / (t + '.parquet')}')")
    return con


def run_staging(con) -> pd.DataFrame:
    """Each stage materialises a table before the next one reads it.

    In the production Oracle version, one long query over the GL history
    regularly failed with ORA-01555 ("snapshot too old"): the undo data it
    needed for read consistency was overwritten before the query finished.
    Splitting the work into short, sequential, materialised stages removed
    the failure. The same structure is kept here.
    """
    for s in STAGES:
        con.execute((SQL / f"{s}.sql").read_text())
    return con.execute("SELECT * FROM stg_04_cash_position ORDER BY branch_code, currency, bal_date").df()
