CREATE TABLE principals (
    subject_ref text PRIMARY KEY,
    display_name text NOT NULL,
    risk_level integer NOT NULL CHECK (risk_level BETWEEN 0 AND 5),
    active boolean NOT NULL DEFAULT true
);
CREATE TABLE access_tokens (
    digest text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES principals,
    scopes jsonb NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE tasks (
    task_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES principals,
    client_request_ref text NOT NULL,
    request_digest text NOT NULL,
    user_request text NOT NULL,
    goals jsonb NOT NULL,
    constraints jsonb NOT NULL,
    status text NOT NULL,
    questions jsonb NOT NULL DEFAULT '[]',
    current_version integer NOT NULL DEFAULT 0,
    stop_requested boolean NOT NULL DEFAULT false,
    run_requested boolean NOT NULL DEFAULT false,
    runner_token text REFERENCES access_tokens,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(subject_ref, client_request_ref)
);
CREATE TABLE plans (
    task_ref text NOT NULL REFERENCES tasks,
    version integer NOT NULL,
    plan_ref text NOT NULL UNIQUE,
    steps jsonb NOT NULL,
    planner_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(task_ref, version)
);
CREATE TABLE operations (
    operation_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES principals,
    task_ref text NOT NULL REFERENCES tasks,
    step_ref text NOT NULL,
    action text NOT NULL,
    arguments jsonb NOT NULL,
    digest text NOT NULL,
    status text NOT NULL,
    result jsonb,
    error_code text,
    delegation_ref text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX operation_task_index ON operations(task_ref);
CREATE TABLE approvals (
    approval_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES principals,
    operation_ref text NOT NULL REFERENCES operations,
    digest text NOT NULL,
    issued_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    status text NOT NULL CHECK(status IN ('ACTIVE','REVOKED','CONSUMED'))
);
CREATE UNIQUE INDEX active_single_approval ON approvals(operation_ref) WHERE status='ACTIVE';
CREATE TABLE delegations (
    delegation_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES principals,
    specification jsonb NOT NULL,
    used_minor bigint NOT NULL DEFAULT 0 CHECK(used_minor >= 0),
    revoked boolean NOT NULL DEFAULT false,
    issued_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE TABLE authorization_uses (
    operation_ref text PRIMARY KEY REFERENCES operations,
    approval_ref text REFERENCES approvals,
    delegation_ref text REFERENCES delegations,
    charged_minor bigint NOT NULL CHECK(charged_minor >= 0),
    released boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((approval_ref IS NULL) <> (delegation_ref IS NULL))
);
ALTER TABLE operations ADD FOREIGN KEY(delegation_ref) REFERENCES delegations;
CREATE TABLE evidence (
    evidence_ref text PRIMARY KEY,
    operation_ref text NOT NULL REFERENCES operations,
    subject_ref text NOT NULL REFERENCES principals,
    kind text NOT NULL,
    record_ref text NOT NULL,
    source text NOT NULL,
    data jsonb NOT NULL,
    verified_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(operation_ref, kind)
);
CREATE TABLE task_events (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_ref text NOT NULL REFERENCES tasks,
    kind text NOT NULL,
    data jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX events_task_index ON task_events(task_ref, sequence);
CREATE TABLE callback_events (
    event_ref text PRIMARY KEY,
    operation_ref text NOT NULL REFERENCES operations,
    digest text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE bank_accounts (
    account_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES principals,
    balance_minor bigint NOT NULL CHECK(balance_minor >= 0),
    opening_balance_minor bigint NOT NULL CHECK(opening_balance_minor >= 0),
    currency text NOT NULL CHECK(currency='CNY'),
    active boolean NOT NULL DEFAULT true
);
CREATE TABLE bank_recipients (
    recipient_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES principals,
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
    subject_ref text NOT NULL REFERENCES principals,
    kind text NOT NULL CHECK(kind IN ('card','subscription')),
    state text NOT NULL,
    data jsonb NOT NULL
);
CREATE TABLE bank_commands (
    operation_ref text PRIMARY KEY,
    subject_ref text NOT NULL REFERENCES principals,
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
CREATE TABLE optimization_candidates (
    candidate_ref text PRIMARY KEY,
    created_by text NOT NULL REFERENCES principals,
    prompt text NOT NULL,
    status text NOT NULL CHECK(status IN ('DRAFT','EVALUATED','RELEASED','ROLLED_BACK')),
    evaluation jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_released_candidate ON optimization_candidates(status) WHERE status='RELEASED';
CREATE TABLE policy_events (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_ref text NOT NULL REFERENCES optimization_candidates,
    previous_ref text REFERENCES optimization_candidates,
    event text NOT NULL CHECK(event IN ('RELEASE','ROLLBACK')),
    actor_ref text NOT NULL REFERENCES principals,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
