-- =====================================================================
-- STG_03  Net customer cash flow per branch x currency x day
--         (positive = net cash leaving the vault to customers)
-- =====================================================================
CREATE OR REPLACE TABLE stg_03_customer_flow AS
SELECT branch_code, currency, CAST(txn_date AS DATE) AS bal_date,
       SUM(cash_deposits)                        AS deposits,
       SUM(cash_withdrawals)                     AS withdrawals,
       SUM(cash_withdrawals - cash_deposits)     AS net_outflow
FROM teller_daily
GROUP BY branch_code, currency, CAST(txn_date AS DATE);
