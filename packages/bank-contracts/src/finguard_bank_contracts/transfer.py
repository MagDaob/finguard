from typing import Self

from pydantic import model_validator

from .values import Currency, DomainModel, Minor, PositiveMinor, Reference


class TransferParameters(DomainModel):
    source_account_ref: Reference
    recipient_ref: Reference
    amount_minor: PositiveMinor
    currency: Currency
    fee_minor: Minor
    minimum_remaining_balance_minor: Minor

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.amount_minor + self.fee_minor > 2**63 - 1:
            raise ValueError("转账金额与费用合计超出存储范围。")
        return self
