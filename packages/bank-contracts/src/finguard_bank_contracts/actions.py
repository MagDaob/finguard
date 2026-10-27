import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field

from .transfer import TransferParameters
from .values import Currency, DomainModel, Minor, PositiveMinor, Reference


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class AccountArguments(DomainModel):
    account_ref: Reference


class BillArguments(AccountArguments):
    days: Annotated[int, Field(strict=True, ge=1, le=366)] = 30


class CardArguments(DomainModel):
    card_ref: Reference
    locked: Annotated[bool, Field(strict=True)]


class SubscriptionArguments(DomainModel):
    subscription_ref: Reference


class ProductArguments(AccountArguments):
    product_ref: Reference
    currency: Currency = "CNY"
    amount_minor: PositiveMinor
    fee_minor: Minor
    minimum_remaining_balance_minor: Minor


@dataclass(frozen=True)
class Action:
    arguments: type[DomainModel]
    scope: str
    write: bool
    monetary: bool = False


ACTIONS = {
    "accounts.balance": Action(AccountArguments, "accounts.read", False),
    "bills.analyze": Action(BillArguments, "transactions.read", False),
    "transfer.create": Action(TransferParameters, "transfers.write", True, True),
    "card.set_lock": Action(CardArguments, "services.write", True),
    "subscription.cancel": Action(SubscriptionArguments, "services.write", True),
    "product.subscribe": Action(ProductArguments, "investments.write", True, True),
    "product.redeem": Action(ProductArguments, "investments.write", True, True),
}


def operation_digest(subject: str, action: str, arguments: dict) -> str:
    return digest({"subject_ref": subject, "action": action, "arguments": arguments})
