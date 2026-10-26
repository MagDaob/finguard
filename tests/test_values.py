import pytest
from pydantic import ValidationError

from finguard.domain.errors import AccessDenied
from finguard.domain.values import Money, OwnedResource, new_ref, require_owner


@pytest.mark.parametrize("amount", [True, False, 30000.0, "30000", -1, 2**63, None])
def test_money_rejects_lossy_or_invalid_amount(amount):
    with pytest.raises(ValidationError):
        Money(amount_minor=amount)


def test_money_preserves_integer_minor_units_and_rejects_underflow_and_overflow():
    balance = Money(amount_minor=150000)
    remaining = balance.subtract(Money(amount_minor=30000))
    assert remaining.amount_minor == 120000
    assert balance.amount_minor == 150000
    with pytest.raises(ValidationError):
        remaining.subtract(Money(amount_minor=120001))
    with pytest.raises(ValidationError):
        Money(amount_minor=2**63 - 1).add(Money(amount_minor=1))


@pytest.mark.parametrize("currency", ["USD", "cny", "", 156])
def test_unimplemented_currencies_are_rejected(currency):
    with pytest.raises(ValidationError):
        Money(amount_minor=30000, currency=currency)


def test_resource_reference_does_not_confer_ownership():
    resource = OwnedResource(resource_ref="account-1", subject_ref="user-1")
    require_owner("user-1", resource)
    with pytest.raises(AccessDenied):
        require_owner("user-2", resource)


@pytest.mark.parametrize("prefix", ["", "contains space", "../account", None, 1])
def test_reference_generation_validates_input(prefix):
    with pytest.raises(ValidationError):
        new_ref(prefix)


def test_generated_identifiers_are_distinct_and_resources_are_immutable():
    assert new_ref("operation") != new_ref("operation")
    resource = OwnedResource(resource_ref="account-1", subject_ref="user-1")
    with pytest.raises(ValidationError):
        resource.subject_ref = "user-2"
