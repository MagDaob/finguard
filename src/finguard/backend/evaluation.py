"""回放请求契约和可选离线执行入口；服务运行不加载模拟业务。"""

from typing import Annotated, Literal

from pydantic import Field

from finguard.backend.catalog import RequestModel, TaskCreate
from finguard.backend.database import Database
from finguard.backend.errors import ServiceError
from finguard.domain.values import Minor, Reference


class ReplayEvent(RequestModel):
    kind: Literal[
        "approve", "run", "recover", "stop", "expire", "settle", "fault", "concurrent_run"
    ]
    step_ref: Reference | None = None
    fault: (
        Literal[
            "before_send",
            "response_lost",
            "crash_before_bank",
            "crash_after_commit",
            "malformed_response",
            "lookup_unknown",
        ]
        | None
    ) = None


class ReplayExpected(RequestModel):
    task_status: Literal[
        "COMPLETED",
        "WAITING_APPROVAL",
        "PARTIALLY_COMPLETED",
        "TERMINATED",
        "EXECUTING",
        "WAITING_CLARIFICATION",
    ]
    balance_minor: Minor
    verified_success: Annotated[bool, Field(strict=True)]


class ReplayCase(RequestModel):
    case_ref: Reference
    initial_balance_minor: Minor = 150000
    request: TaskCreate
    events: Annotated[tuple[ReplayEvent, ...], Field(min_length=1, max_length=20)]
    expected: ReplayExpected


class ReplaySuite(RequestModel):
    format_version: Literal["1"]
    cases: Annotated[tuple[ReplayCase, ...], Field(min_length=1, max_length=20)]


def _implementation():
    try:
        from finguard_simulator import evaluation
    except ImportError as exc:
        raise ServiceError(
            "REPLAY_DEPENDENCY_MISSING", "离线回放需安装开发依赖中的模拟器。", 503
        ) from exc
    return evaluation


def invariants(database: Database) -> dict[str, bool]:
    return _implementation().invariants(database)


class ReplayRunner:
    def __init__(self, database: Database):
        self.db = database

    def run(self, suite: ReplaySuite, planner=None) -> dict:
        return _implementation().SimulatorReplayRunner(self.db).run(suite, planner)
