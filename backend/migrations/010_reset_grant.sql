-- A season reset gives every existing account a fresh starting balance,
-- recorded as its own mint reason so the ledger still explains every balance.

BEGIN;

ALTER TABLE currency_ledger DROP CONSTRAINT currency_ledger_reason_check;
ALTER TABLE currency_ledger ADD CONSTRAINT currency_ledger_reason_check CHECK (reason IN (
    'MINT_SIGNUP',
    'MINT_DEV_GRANT',
    'MINT_RESET_GRANT',
    'TRANSFER_PURCHASE_DEBIT',
    'TRANSFER_SALE_CREDIT',
    'BURN_MARKET_FEE',
    'BURN_PACK_PURCHASE'
));

COMMIT;
