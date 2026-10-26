import json
from pathlib import Path

import httpx
import pytest

from finguard.backend.catalog import TaskCreate
from finguard.backend.errors import ServiceError
from finguard.backend.evaluation import ReplayRunner, ReplaySuite, invariants
from finguard.backend.planning import GatewayPlanner
from finguard.backend.policies import Policies, PolicyPlanner
from finguard.backend.security import ADMIN_SCOPES

pytestmark = pytest.mark.postgres
SUITE = Path(__file__).parents[1] / "scenarios" / "backend-replay.json"


def mini_suite():
    data = json.loads(SUITE.read_text(encoding="utf-8"))
    data["cases"] = data["cases"][:2]
    return ReplaySuite.model_validate(data)


def echo_gateway():
    def handler(request):
        data = json.loads(request.content)
        return httpx.Response(200, json={"steps": data["goals"], "planner_version": "mock-policy"})

    return GatewayPlanner("https://gateway.example/plan", transport=httpx.MockTransport(handler))


def test_full_replay_preserves_live_business_and_checks_invariants(backend, actor, database):
    before = backend.bank.list_accounts(actor.subject_ref)
    report = ReplayRunner(database).run(
        ReplaySuite.model_validate_json(SUITE.read_text(encoding="utf-8"))
    )
    assert report["passed"], report
    assert report["case_count"] == 5 and report["business_completion_rate"] == 1
    assert report["mode"] == "fixed" and report["all_invariants_hold"]
    assert backend.bank.list_accounts(actor.subject_ref) == before
    with database.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM tasks").fetchone()["n"] == 0


def test_invariant_detects_ledger_tampering(database, bank):
    with database.transaction() as c:
        c.execute(
            "UPDATE bank_accounts SET balance_minor=balance_minor+1 WHERE account_ref='account-demo-001'"
        )
    assert not invariants(database)["ledger_balanced"]


def test_fixed_replay_cannot_release_model_policy(backend, actor, database):
    admin = backend.security.authenticate(
        backend.security.issue_token("operator-demo", ADMIN_SCOPES)
    )
    policies = Policies(database, None)
    candidate = policies.create(admin, "需要通过对照评估的候选提示词。")
    report = policies.evaluate(admin, candidate["candidate_ref"], mini_suite())
    assert report["candidate"]["passed"] and not report["eligible_for_release"]
    with pytest.raises(ServiceError) as exc:
        policies.release(admin, candidate["candidate_ref"], report["evaluation_digest"], True)
    assert exc.value.code == "EVALUATION_REQUIRED"
    with pytest.raises(ServiceError):
        policies.create(actor, "用户无权修改优化策略。")


def test_failures_generate_draft_candidate_without_changing_business(backend, actor, database):
    admin = backend.security.authenticate(
        backend.security.issue_token("operator-demo", ADMIN_SCOPES)
    )
    data = mini_suite().model_dump(mode="json")
    data["cases"] = data["cases"][:1]
    data["cases"][0]["expected"]["balance_minor"] = 1
    candidate = Policies(database, None).propose_from_failures(
        admin, ReplaySuite.model_validate(data)
    )
    assert candidate["status"] == "DRAFT" and candidate["requires_evaluation"]
    assert candidate["failure_analysis"][0]["layer"] == "BUSINESS_OUTCOME"
    assert backend.bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 150000


def test_mock_model_policy_evaluation_review_release_and_rollback(backend, actor, database):
    admin = backend.security.authenticate(
        backend.security.issue_token("operator-demo", ADMIN_SCOPES)
    )
    gateway = echo_gateway()
    policies = Policies(database, gateway)
    first = policies.create(admin, "保留所有目标、参数与到账条件。")
    report = policies.evaluate(admin, first["candidate_ref"], mini_suite())
    assert report["eligible_for_release"], report
    with pytest.raises(ServiceError):
        policies.release(admin, first["candidate_ref"], report["evaluation_digest"], False)
    assert (
        policies.release(admin, first["candidate_ref"], report["evaluation_digest"], True)["status"]
        == "RELEASED"
    )
    planner = PolicyPlanner(database, gateway)
    request = TaskCreate.model_validate(
        json.loads(SUITE.read_text(encoding="utf-8"))["cases"][0]["request"]
    )
    goals = [step.model_dump(mode="json") for step in request.steps]
    assert planner.propose(goals, request.constraints)[1] == "mock-policy"
    assert policies.rollback(admin, first["candidate_ref"])["restored_ref"] is None
    with database.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM policy_events").fetchone()["n"] == 2
