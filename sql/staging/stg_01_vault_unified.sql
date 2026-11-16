-- =====================================================================
-- STG_01  One vault-balance table from two general-ledger sources
-- ---------------------------------------------------------------------
-- After a merger, legacy and acquired branches post vault cash to
-- different GL tables with different schemas:
--   legacy   : BRANCH_CODE, GL_DATE, CCY_CODE ('NGN'), CLOSING_BAL
--   acquired : sol_id ('S1xx'), value_date, crncy_num ('566'), eod_balance
-- Harmonise branch codes and currency codes, then UNION.
-- =====================================================================
CREATE OR REPLACE TABLE stg_01_vault_unified AS
SELECT BRANCH_CODE AS branch_code, CAST(GL_DATE AS DATE) AS bal_date, CCY_CODE AS currency,
       CLOSING_BAL AS vault_closing, 'LEGACY' AS gl_source
FROM gl_vault_legacy
WHERE GL_ACCT = 'VAULT_CASH'
UNION ALL
SELECT REPLACE(sol_id, 'S', 'B'), CAST(value_date AS DATE),
       CASE crncy_num WHEN '566' THEN 'NGN' WHEN '840' THEN 'USD' WHEN '826' THEN 'GBP' WHEN '978' THEN 'EUR' END,
       eod_balance, 'ACQUIRED'
FROM gl_vault_acquired
WHERE gl_sub_head = '10101';
