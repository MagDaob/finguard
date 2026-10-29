CREATE TABLE wallet_connections (
 connection_ref text PRIMARY KEY,
 subject_ref text NOT NULL REFERENCES principals,
 request_ref text NOT NULL,
 bank_ref text NOT NULL,
 state text NOT NULL DEFAULT 'CONNECTED',
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(subject_ref,request_ref)
);
CREATE TABLE wallet_assets (
 asset_ref text PRIMARY KEY,
 connection_ref text NOT NULL REFERENCES wallet_connections,
 kind text NOT NULL CHECK(kind IN ('account','card')),
 remote_ref text NOT NULL,
 display_name text NOT NULL,
 state text NOT NULL,
 synced_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(connection_ref,kind,remote_ref)
);
CREATE UNIQUE INDEX wallet_one_connected_bank ON wallet_connections(subject_ref,bank_ref) WHERE state='CONNECTED';
