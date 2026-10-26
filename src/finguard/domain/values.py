"""不可变的基础值；首批仅支持人民币整数分。"""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from finguard.domain.errors import AccessDenied

Reference = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9_.:-]{0,127}$")]
RequestText = Annotated[str, StringConstraints(min_length=1, max_length=10000, pattern=r"\S")]
Minor = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
PositiveMinor = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
Currency = Literal["CNY"]
_REFERENCE = TypeAdapter(Reference)


class DomainModel(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always"
    )


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
