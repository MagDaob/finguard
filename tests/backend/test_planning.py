import copy
import json

import httpx
import pytest
from pydantic import ValidationError

from finguard.backend.catalog import Constraints, Step, normalize_steps
from finguard.backend.errors import ServiceError
from finguard.backend.planning import BaselinePlanner, GatewayPlanner, compile_candidate


def goals():
    return normalize_steps(
        (
            Step(
                step_ref="read",
                action="accounts.balance",
                arguments={"account_ref": "account-demo-001"},
            ),
            Step(
                step_ref="card",
                action="card.set_lock",
                arguments={"card_ref": "card-demo", "locked": True},
                depends_on=("read",),
            ),
        ),
        Constraints(),
    )


@pytest.mark.parametrize(
    "mutation", ["extra", "missing", "amount", "condition", "unknown", "cycle"]
)
def test_model_candidate_cannot_expand_or_weaken_goals(mutation):
    original = goals()
    candidate = copy.deepcopy(original)
    if mutation == "extra":
        candidate.append({**candidate[0], "step_ref": "extra"})
    elif mutation == "missing":
        candidate.pop()
    elif mutation == "amount":
        candidate[1]["arguments"]["locked"] = False
    elif mutation == "condition":
        candidate[1]["depends_on"] = []
    elif mutation == "unknown":
        candidate[0]["action"] = "admin.approve"
    elif mutation == "cycle":
        candidate[0]["depends_on"] = ["card"]
    with pytest.raises((ServiceError, ValidationError)):
        compile_candidate(original, candidate, Constraints())


def test_gateway_protocol_and_non_json_response():
    def handler(request):
        data = json.loads(request.content)
        assert "authorization" not in data and request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"steps": data["goals"], "planner_version": "mock-v1"})

    planner = GatewayPlanner(
        "https://gateway.example/plan", "test-key", transport=httpx.MockTransport(handler)
    )
    candidate, version = planner.propose(goals(), Constraints())
    assert version == "mock-v1" and compile_candidate(goals(), candidate, Constraints()) == goals()
    planner = GatewayPlanner(
        "https://gateway.example/plan",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="not json")),
    )
    with pytest.raises(ServiceError) as exc:
        planner.propose(goals(), Constraints())
    assert exc.value.code == "MODEL_UNAVAILABLE"


@pytest.mark.parametrize(
    "url",
    [
        "http://external.example/plan",
        "https://user:password@gateway.example/plan",
        "https://gateway.example/plan#fragment",
    ],
)
def test_gateway_rejects_unsafe_configuration(url):
    with pytest.raises(ServiceError):
        GatewayPlanner(url)


def test_funds_condition_requires_same_account_redemption():
    candidate = goals()
    candidate[1]["funds_available_after"] = ["read"]
    with pytest.raises(ServiceError) as exc:
        compile_candidate(candidate, candidate, Constraints())
    assert exc.value.code == "INVALID_FUNDS_DEPENDENCY"


def test_negative_or_unsupported_condition_requires_clarification():
    steps, _, questions = BaselinePlanner().infer("不要给小王转300元", {}, Constraints())
    assert not steps and questions
