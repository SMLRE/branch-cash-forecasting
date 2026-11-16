-- =====================================================================
-- STG_02  Opening vault balance = previous business day's closing (LAG)
-- =====================================================================
CREATE OR REPLACE TABLE stg_02_opening_balance AS
SELECT branch_code, currency, bal_date, gl_source, vault_closing,
       LAG(vault_closing) OVER (PARTITION BY branch_code, currency ORDER BY bal_date) AS vault_opening
FROM stg_01_vault_unified;
