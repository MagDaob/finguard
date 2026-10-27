from concurrent.futures import ThreadPoolExecutor

import pytest
from finguard_simulator.bank import SimulatedBank
from finguard_simulator.testing import seed_combined_demo as seed_demo

from finguard.backend.bank import ResultUnknown
from finguard.backend.catalog import operation_digest
from finguard.backend.errors import ServiceError
from finguard.backend.security import Security

pytestmark = pytest.mark.postgres
SUBJECT = "user-demo-001"


def test_migration_and_demo_are_idempotent(database, bank):
    database.migrate()
    seed_demo(database)
    assert bank.list_accounts(SUBJECT)[0]["balance_minor"] == 150000


def test_transfer_is_atomic_and_repeated_calls_have_one_debit(bank, database, transfer_args):
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _: bank.execute(SUBJECT, "operation-1", "transfer.create", transfer_args),
                range(16),
            )
        )
    assert all(result["status"] == "SUCCEEDED" for result in results)
    assert bank.list_accounts(SUBJECT)[0]["balance_minor"] == 120000
    with database.transaction() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM bank_ledger WHERE operation_ref='operation-1' AND amount_minor<0"
            ).fetchone()["n"]
            == 1
        )


def test_distinct_concurrent_transfers_cannot_bypass_minimum(bank, transfer_args):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda op: bank.execute(SUBJECT, op, "transfer.create", transfer_args),
                ("operation-a", "operation-b"),
            )
        )
    assert sorted(result["status"] for result in results) == ["FAILED", "SUCCEEDED"]
    assert bank.list_accounts(SUBJECT)[0]["balance_minor"] == 120000


def test_lost_response_can_be_verified_after_new_adapter_instance(database, bank, transfer_args):
    bank.inject_fault("operation-1", "response_lost")
    with pytest.raises(ResultUnknown):
        bank.execute(SUBJECT, "operation-1", "transfer.create", transfer_args)
    result, evidence = SimulatedBank(database).verify(
        SUBJECT, "operation-1", operation_digest(SUBJECT, "transfer.create", transfer_args)
    )
    assert result["status"] == "SUCCEEDED" and len(evidence) == 3


def test_fence_prevents_a_late_request_from_ever_charging(bank, transfer_args):
    fingerprint = operation_digest(SUBJECT, "transfer.create", transfer_args)
    result = bank.lookup(
        SUBJECT, "operation-1", fingerprint, True, "transfer.create", transfer_args
    )
    assert result["status"] == "FAILED"
    assert (
        bank.execute(SUBJECT, "operation-1", "transfer.create", transfer_args)["status"] == "FAILED"
    )
    assert bank.list_accounts(SUBJECT)[0]["balance_minor"] == 150000


def test_reusing_operation_id_with_different_parameters_is_rejected(bank, transfer_args):
    bank.execute(SUBJECT, "operation-1", "transfer.create", transfer_args)
    with pytest.raises(ServiceError, match="其他业务参数"):
        bank.execute(
            SUBJECT, "operation-1", "transfer.create", {**transfer_args, "amount_minor": 1}
        )


def test_cross_user_access_is_rejected(bank, transfer_args):
    with pytest.raises(ServiceError):
        bank.preview("user-demo-002", "transfer.create", transfer_args)


def test_bill_analysis_excludes_transfers_and_accounts_for_refunds(bank, transfer_args):
    bank.execute(SUBJECT, "operation-1", "transfer.create", transfer_args)
    report = bank.execute(
        SUBJECT, "analysis-1", "bills.analyze", {"account_ref": "account-demo-001", "days": 30}
    )
    assert report["spending_minor"] == 5500
    assert report["refunds_minor"] == 500
    assert report["net_spending_minor"] == 5000


def test_product_acceptance_is_distinct_from_cash_availability(bank, database):
    args = {
        "account_ref": "account-demo-001",
        "product_ref": "fund-demo",
        "amount_minor": 10000,
        "fee_minor": 0,
        "minimum_remaining_balance_minor": 0,
    }
    receipt = bank.execute(SUBJECT, "redeem-1", "product.redeem", args)
    assert receipt["status"] == "PENDING" and not receipt["funds_available"]
    assert bank.list_accounts(SUBJECT)[0]["balance_minor"] == 150000
    with database.transaction() as c:
        c.execute(
            "UPDATE bank_commands SET settle_at=now()-interval '1 second' WHERE operation_ref='redeem-1'"
        )
    receipt, evidence = bank.verify(
        SUBJECT, "redeem-1", operation_digest(SUBJECT, "product.redeem", args)
    )
    assert receipt["status"] == "SUCCEEDED" and receipt["funds_available"]
    assert bank.list_accounts(SUBJECT)[0]["balance_minor"] == 160000 and len(evidence) == 3


def test_product_risk_and_trading_window_are_rechecked(bank, database):
    args = {
        "account_ref": "account-demo-001",
        "product_ref": "fund-high-risk",
        "amount_minor": 1,
        "fee_minor": 0,
        "minimum_remaining_balance_minor": 0,
    }
    assert (
        bank.execute(SUBJECT, "buy-1", "product.subscribe", args)["error_code"] == "RISK_MISMATCH"
    )
    with database.transaction() as c:
        c.execute("UPDATE bank_products SET trading_open=false WHERE product_ref='fund-demo'")
    args["product_ref"] = "fund-demo"
    assert (
        bank.execute(SUBJECT, "buy-2", "product.subscribe", args)["error_code"]
        == "PRODUCT_UNAVAILABLE"
    )


def test_card_and_subscription_keep_distinct_business_semantics(bank):
    assert (
        bank.execute(SUBJECT, "lock-1", "card.set_lock", {"card_ref": "card-demo", "locked": True})[
            "effect"
        ]
        == "TEMPORARY_LOCK"
    )
    result = bank.execute(
        SUBJECT, "cancel-1", "subscription.cancel", {"subscription_ref": "subscription-demo"}
    )
    assert (
        result["contract_status"] == "CANCELLED"
        and not result["auto_debit_mandate_changed"]
        and result["refund_minor"] == 0
    )


def test_tokens_revoke_and_cannot_forge_scopes(database, bank):
    security = Security(database)
    token = security.issue_token(SUBJECT, scopes=("accounts.read",))
    actor = security.authenticate(token)
    security.require_scope(actor, "accounts.read")
    with pytest.raises(ServiceError):
        security.require_scope(actor, "transfers.write")
    security.revoke(actor)
    with pytest.raises(ServiceError):
        security.authenticate(token)


def test_redemption_rechecks_balance_at_settlement_and_restores_position(bank, database):
    args = {
        "account_ref": "account-demo-001",
        "product_ref": "fund-demo",
        "amount_minor": 10000,
        "fee_minor": 0,
        "minimum_remaining_balance_minor": 150000,
    }
    assert bank.execute(SUBJECT, "redeem-floor", "product.redeem", args)["status"] == "PENDING"
    with database.transaction() as c:
        c.execute(
            "UPDATE bank_accounts SET balance_minor=100000 WHERE account_ref='account-demo-001'"
        )
        c.execute(
            "UPDATE bank_commands SET settle_at=now()-interval '1 second' WHERE operation_ref='redeem-floor'"
        )
    result = bank.lookup(SUBJECT, "redeem-floor", operation_digest(SUBJECT, "product.redeem", args))
    assert result["status"] == "FAILED" and result["position_restored"]
    assert bank.list_positions(SUBJECT)[0]["value_minor"] == 30000
    assert bank.list_accounts(SUBJECT)[0]["balance_minor"] == 100000
