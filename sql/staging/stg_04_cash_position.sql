-- =====================================================================
-- STG_04  True cash position + implied cash-in-transit
-- ---------------------------------------------------------------------
--   closing = opening - net_customer_outflow + CIT
--   => implied_cit = closing - opening + net_outflow
-- CIT (replenishments / evacuations) is not in the teller data, so it is
-- recovered as the residual. Large positive = replenishment, large
-- negative = evacuation, small = noise / reconciliation items.
-- =====================================================================
CREATE OR REPLACE TABLE stg_04_cash_position AS
SELECT o.branch_code, o.currency, o.bal_date, o.gl_source,
       o.vault_opening, f.deposits, f.withdrawals, f.net_outflow, o.vault_closing,
       o.vault_closing - o.vault_opening + f.net_outflow AS implied_cit
FROM stg_02_opening_balance o
JOIN stg_03_customer_flow f USING (branch_code, currency, bal_date)
WHERE o.vault_opening IS NOT NULL;
