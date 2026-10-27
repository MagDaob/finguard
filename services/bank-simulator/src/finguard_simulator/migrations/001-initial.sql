CREATE TABLE bank_customers (subject_ref text PRIMARY KEY, display_name text NOT NULL, risk_level integer NOT NULL CHECK (risk_level BETWEEN 0 AND 5));
CREATE TABLE bank_accounts (
    account_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES bank_customers,
    balance_minor bigint NOT NULL CHECK(balance_minor >= 0),
    opening_balance_minor bigint NOT NULL CHECK(opening_balance_minor >= 0),
    currency text NOT NULL CHECK(currency='CNY'),
    active boolean NOT NULL DEFAULT true
);
CREATE TABLE bank_recipients (
    recipient_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES bank_customers,
    display_name text NOT NULL,
    destination_account_ref text NOT NULL REFERENCES bank_accounts,
    active boolean NOT NULL DEFAULT true
);
CREATE TABLE bank_products (
    product_ref text PRIMARY KEY,
    name text NOT NULL,
    risk_level integer NOT NULL CHECK(risk_level BETWEEN 0 AND 5),
    trading_open boolean NOT NULL DEFAULT true,
    settlement_delay_seconds integer NOT NULL CHECK(settlement_delay_seconds BETWEEN 0 AND 86400)
);
CREATE TABLE bank_positions (
    account_ref text NOT NULL REFERENCES bank_accounts,
    product_ref text NOT NULL REFERENCES bank_products,
    value_minor bigint NOT NULL CHECK(value_minor >= 0),
    PRIMARY KEY(account_ref,product_ref)
);
CREATE TABLE bank_services (
    resource_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES bank_customers,
    kind text NOT NULL CHECK(kind IN ('card','subscription')),
    state text NOT NULL,
    data jsonb NOT NULL
);
CREATE TABLE bank_commands (
    operation_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES bank_customers,
    action text NOT NULL,
    arguments jsonb NOT NULL,
    digest text NOT NULL,
    status text NOT NULL CHECK(status IN ('PENDING','SUCCEEDED','FAILED')),
    result jsonb NOT NULL,
    settle_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE bank_ledger (
    entry_ref text PRIMARY KEY,
    operation_ref text NOT NULL REFERENCES bank_commands,
    account_ref text NOT NULL REFERENCES bank_accounts,
    amount_minor bigint NOT NULL CHECK(amount_minor <> 0),
    category text NOT NULL,
    refund_of text REFERENCES bank_ledger(entry_ref),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(operation_ref, account_ref, category)
);
CREATE INDEX ledger_account_index ON bank_ledger(account_ref, occurred_at);
CREATE TABLE bank_faults (
    operation_ref text PRIMARY KEY,
    mode text NOT NULL,
    remaining integer NOT NULL CHECK(remaining >= 0)
);
