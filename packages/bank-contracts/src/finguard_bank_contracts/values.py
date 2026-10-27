from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Reference = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9_.:-]{0,127}$")]
RequestText = Annotated[str, StringConstraints(min_length=1, max_length=10000, pattern=r"\S")]
Minor = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
PositiveMinor = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
Currency = Literal["CNY"]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always"
    )
