import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

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
    days: Annotated[int, Field(strict=True, ge=1, le=366)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    start_date: date | None = Field(default=None, exclude_if=lambda v: v is None)
    end_date: date | None = Field(default=None, exclude_if=lambda v: v is None)
    timezone: Literal["Asia/Shanghai"] | None = Field(default=None, exclude_if=lambda v: v is None)
    category: Literal["food", "transport", "shopping"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def period(self) -> Self:
        if self.start_date is not None or self.end_date is not None:
            if self.start_date is None or self.end_date is None or self.days is not None:
                raise ValueError("日期区间须成对提供，且不能与最近天数混用。")
            if (
                self.timezone != "Asia/Shanghai"
                or not 0 < (self.end_date - self.start_date).days <= 366
            ):
                raise ValueError("日期区间须为上海时区，长度为 1 至 366 天。")
        elif self.timezone is not None:
            raise ValueError("时区须与明确日期区间一起提供。")
        elif self.days is None:
            object.__setattr__(self, "days", 30)
        return self


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
