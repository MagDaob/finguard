import pytest

from finguard.domain.errors import InvalidTransition
from finguard.domain.states import (
    OperationStatus as O,
)
from finguard.domain.states import (
    TaskStatus as T,
)
from finguard.domain.states import (
    require_operation_transition,
    require_task_transition,
)


def test_normal_operation_path_and_reconciliation_path():
    for current, target in [
        (O.PREPARING, O.WAITING_APPROVAL),
        (O.WAITING_APPROVAL, O.AUTHORIZED),
        (O.AUTHORIZED, O.SUBMITTING),
        (O.SUBMITTING, O.UNKNOWN),
        (O.UNKNOWN, O.SUCCEEDED),
    ]:
        require_operation_transition(current, target)


@pytest.mark.parametrize("target", list(O))
@pytest.mark.parametrize("terminal", [O.SUCCEEDED, O.FAILED, O.CANCELLED_PRE_SUBMIT])
def test_terminal_operations_cannot_be_replayed(terminal, target):
    with pytest.raises(InvalidTransition):
        require_operation_transition(terminal, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (O.PREPARING, O.SUBMITTING),
        (O.WAITING_APPROVAL, O.SUBMITTING),
        (O.UNKNOWN, O.SUBMITTING),
        (O.UNKNOWN, O.AUTHORIZED),
        (O.UNKNOWN, O.CANCELLED_PRE_SUBMIT),
        (O.SUBMITTING, O.CANCELLED_PRE_SUBMIT),
    ],
)
def test_authorization_cannot_be_skipped_and_unknown_results_cannot_be_resubmitted(current, target):
    with pytest.raises(InvalidTransition):
        require_operation_transition(current, target)


def test_expired_authorization_can_return_to_waiting():
    require_operation_transition(O.AUTHORIZED, O.WAITING_APPROVAL)


@pytest.mark.parametrize("terminal", [T.COMPLETED, T.TERMINATED])
@pytest.mark.parametrize("target", list(T))
def test_task_terminal_states_are_closed(terminal, target):
    with pytest.raises(InvalidTransition):
        require_task_transition(terminal, target)


def test_partial_completion_can_resume_without_resetting_operation_state():
    require_task_transition(T.EXECUTING, T.PARTIALLY_COMPLETED)
    require_task_transition(T.PARTIALLY_COMPLETED, T.EXECUTING)


def test_state_guards_reject_strings_and_wrong_enum_types():
    with pytest.raises(InvalidTransition):
        require_operation_transition("AUTHORIZED", O.SUBMITTING)
    with pytest.raises(InvalidTransition):
        require_task_transition(O.PREPARING, T.PLANNING)
