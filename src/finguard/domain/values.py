"""不可变的基础值；首批仅支持人民币整数分。"""

from uuid import uuid4

from finguard_bank_contracts.values import (
    Currency as Currency,
)
from finguard_bank_contracts.values import (
    DomainModel as DomainModel,
)
from finguard_bank_contracts.values import (
    Minor as Minor,
)
from finguard_bank_contracts.values import (
    PositiveMinor as PositiveMinor,
)
from finguard_bank_contracts.values import (
    Reference as Reference,
)
from finguard_bank_contracts.values import (
    RequestText as RequestText,
)
from pydantic import TypeAdapter

from finguard.domain.errors import AccessDenied

_REFERENCE = TypeAdapter(Reference)


class Money(DomainModel):
    amount_minor: Minor
    currency: Currency = "CNY"

    def add(self, other: "Money") -> "Money":
        return Money(amount_minor=self.amount_minor + other.amount_minor, currency=self.currency)

    def subtract(self, other: "Money") -> "Money":
        return Money(amount_minor=self.amount_minor - other.amount_minor, currency=self.currency)


class OwnedResource(DomainModel):
    resource_ref: Reference
    subject_ref: Reference


def require_owner(subject_ref: str, resource: OwnedResource) -> None:
    """检查可信资源记录的归属；不负责认证或从数据库加载资源。"""
    _REFERENCE.validate_python(subject_ref, strict=True)
    if subject_ref != resource.subject_ref:
        raise AccessDenied("主体不具有该资源的访问权限。")


def new_ref(prefix: str) -> str:
    """仅在创建业务对象时生成编号；重试时复用已保存编号。"""
    _REFERENCE.validate_python(prefix, strict=True)
    return _REFERENCE.validate_python(f"{prefix}-{uuid4().hex}", strict=True)
