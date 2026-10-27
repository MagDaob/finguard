import os
from uuid import uuid4

import psycopg
import pytest
from finguard_simulator.bank import SimulatedBank
from finguard_simulator.testing import seed_combined_demo as seed_demo
from psycopg import sql

from finguard.backend.config import Settings
from finguard.backend.database import Database
from finguard.backend.security import Security
from finguard.backend.service import Backend


@pytest.fixture
def database():
    try:
        settings = Settings.from_env()
    except ValueError:
        if os.getenv("FINGUARD_REQUIRE_POSTGRES") == "1":
            pytest.fail("PostgreSQL is required but not configured")
        pytest.skip("No PostgreSQL configured")
    schema = "fg_test_" + uuid4().hex[:20]
    db = Database(settings.database_url.get_secret_value(), schema)
    db.migrate()
    try:
        yield db
    finally:
        assert schema.startswith("fg_test_")
        with psycopg.connect(db.dsn) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def bank(database):
    seed_demo(database)
    return SimulatedBank(database)


@pytest.fixture
def actor(database, bank):
    security = Security(database)
    return security.authenticate(security.issue_token("user-demo-001"))


@pytest.fixture
def transfer_args():
    return {
        "source_account_ref": "account-demo-001",
        "recipient_ref": "recipient-demo-wang",
        "amount_minor": 30000,
        "currency": "CNY",
        "fee_minor": 0,
        "minimum_remaining_balance_minor": 100000,
    }


@pytest.fixture
def backend(database, bank):
    return Backend(database, bank=bank)
