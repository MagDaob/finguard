import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from finguard.backend.api import create_app
from finguard.backend.benchmark import run_live_benchmark
from finguard.backend.catalog import Constraints, Step, normalize_steps
from finguard.backend.config import Settings
from finguard.backend.deepseek import AdaptiveDeepSeekPlanner, DeepSeekClient, DeepSeekPlanner
from finguard.backend.errors import ServiceError
from finguard.backend.model_factory import configured_planner


def settings(**changes):
    return Settings(
        **{
            "database_url": "postgresql://test-local",
            "deepseek_api_key": "test-only-not-a-real-key",
            **changes,
        }
    )


def goal():
    return normalize_steps(
        (
            Step(
                step_ref="balance",
                action="accounts.balance",
                arguments={"account_ref": "account-demo-001"},
            ),
        ),
        Constraints(),
    )


def response(call=None, finish=None, content=None):
    return httpx.Response(
        200,
        json={
            "model": "deepseek-flash",
            "choices": [
                {
                    "finish_reason": finish or ("tool_calls" if call else "stop"),
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "reasoning_content": "private-model-reasoning",
                        "tool_calls": [call] if call else [],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 10,
            },
        },
    )


def call(name, args, reference):
    return {
        "id": reference,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def echo_transport():
    def handler(request):
        payload = json.loads(request.content)
        if not payload.get("tools"):
            if payload.get("response_format"):
                data = json.loads(payload["messages"][1]["content"])
                return response(content=json.dumps({"steps": data["goals"]}))
            return response(content="OK")
        outputs = [json.loads(m["content"]) for m in payload["messages"] if m["role"] == "tool"]
        if not outputs:
            summary = json.loads(payload["messages"][1]["content"])
            return response(
                call(
                    "inspect_steps",
                    {"step_refs": [s["step_ref"] for s in summary["goals"]]},
                    "inspect",
                )
            )
        steps = next(item["steps"] for item in outputs if "steps" in item)
        return response(call("submit_plan", {"steps": steps}, "submit"))

    return httpx.MockTransport(handler)


def test_deepseek_two_round_loop_preserves_reasoning_and_keeps_it_out_of_reports():
    requests = []
    echo = echo_transport()

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-only-not-a-real-key"
        return echo.handle_request(request)

    client = DeepSeekClient(settings(deepseek_thinking=True), httpx.MockTransport(handler))
    planner = DeepSeekPlanner(client)
    try:
        candidate, version = planner.propose(goal(), Constraints())
        assert candidate == goal() and "deepseek-agent" in version
        assert len(requests) == 2
        assert requests[0]["user_id"] == requests[1]["user_id"]
        assert requests[1]["messages"][2]["reasoning_content"] == "private-model-reasoning"
        assert requests[0]["tool_choice"] == "auto"
        assert "JsonValue" not in json.dumps(requests[0]["tools"])
        assert "private-model-reasoning" not in json.dumps(planner.diagnostics())
        assert planner.diagnostics()["status"] == "VALIDATED"
    finally:
        client.close()


def test_invalid_plan_gets_bounded_feedback_then_can_be_corrected():
    n = 0

    def handler(request):
        nonlocal n
        n += 1
        if n == 1:
            return response(call("inspect_steps", {"step_refs": ["balance"]}, "read"))
        if n == 2:
            bad = copy.deepcopy(goal())
            bad[0]["arguments"]["account_ref"] = "other-account"
            return response(call("submit_plan", {"steps": bad}, "bad"))
        payload = json.loads(request.content)
        assert (
            json.loads(payload["messages"][-1]["content"])["error"]["code"] == "PLAN_SCOPE_CHANGED"
        )
        return response(call("submit_plan", {"steps": goal()}, "good"))

    client = DeepSeekClient(settings(), httpx.MockTransport(handler))
    try:
        planner = DeepSeekPlanner(client)
        assert planner.propose(goal(), Constraints())[0] == goal()
        assert len(planner.diagnostics()["rounds"]) == 3
    finally:
        client.close()


@pytest.mark.parametrize(
    "variant,code",
    [
        ("repeat", "AGENT_REPEATED_CALL"),
        ("unregistered", "AGENT_ROUND_LIMIT"),
        ("truncated", "MODEL_INCOMPLETE"),
    ],
)
def test_runaway_and_incomplete_model_outputs_fail_closed(variant, code):
    n = 0

    def handler(_):
        nonlocal n
        n += 1
        if variant == "truncated":
            return response(finish="length")
        if variant == "repeat":
            return response(call("inspect_steps", {"step_refs": ["balance"]}, str(n)))
        return response(call("approve_payment", {"attempt": n}, str(n)))

    client = DeepSeekClient(settings(), httpx.MockTransport(handler))
    planner = DeepSeekPlanner(client)
    try:
        with pytest.raises(ServiceError) as exc:
            planner.propose(goal(), Constraints())
        assert exc.value.code == code and client.request_count <= 4
    finally:
        client.close()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "DEEPSEEK_AUTH_FAILED"),
        (402, "DEEPSEEK_BALANCE_REQUIRED"),
        (429, "DEEPSEEK_RATE_LIMITED"),
        (500, "DEEPSEEK_HTTP_ERROR"),
    ],
)
def test_provider_errors_are_sanitized_and_never_automatically_retried(status, code):
    client = DeepSeekClient(
        settings(),
        httpx.MockTransport(lambda _: httpx.Response(status, text="private upstream response")),
    )
    try:
        with pytest.raises(ServiceError) as exc:
            client.complete([{"role": "user", "content": "ping"}])
        assert exc.value.code == code and "private" not in str(exc.value)
        assert client.request_count == 1
    finally:
        client.close()


def test_shared_client_limits_concurrency_and_request_budget():
    active, maximum = 0, 0
    lock = threading.Lock()

    def handler(_):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(active, maximum)
        time.sleep(0.02)
        with lock:
            active -= 1
        return response(content="OK")

    client = DeepSeekClient(
        settings(deepseek_concurrency=2), httpx.MockTransport(handler), request_budget=6
    )
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(
                pool.map(lambda _: client.complete([{"role": "user", "content": "ping"}]), range(6))
            )
        assert maximum == 2
        with pytest.raises(ServiceError) as exc:
            client.complete([{"role": "user", "content": "ping"}])
        assert exc.value.code == "MODEL_REQUEST_BUDGET" and client.request_count == 6
    finally:
        client.close()


def test_missing_key_fails_closed_and_env_alias_is_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("FINGUARD_DEEPSEEK_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "FINGUARD_DATABASE_URL=postgresql://test-local\nDEEPSEEK_API_KEY=test-only-key\nFINGUARD_MODEL_PROVIDER=deepseek\n"
    )
    configured = Settings.from_env(path)
    assert "test-only-key" not in repr(configured)
    with pytest.raises(ServiceError):
        configured_planner(
            Settings(database_url="postgresql://test-local", model_provider="deepseek")
        )


def test_adaptive_simple_plan_uses_one_request_and_only_relevant_schemas():
    seen = []
    transport = echo_transport()

    def handler(request):
        data = json.loads(request.content)
        seen.append(data)
        assert set(json.loads(data["messages"][1]["content"])["tools"]) == {"accounts.balance"}
        return transport.handle_request(request)

    client = DeepSeekClient(settings(), httpx.MockTransport(handler))
    try:
        planner = AdaptiveDeepSeekPlanner(client)
        assert planner.propose(goal(), Constraints())[0] == goal()
        assert client.request_count == 1 and planner.diagnostics()["strategy"] == "direct-small"
        assert isinstance(planner.with_prompt("review"), AdaptiveDeepSeekPlanner)
    finally:
        client.close()


def test_adaptive_forced_loop_preserves_two_round_protocol():
    client = DeepSeekClient(settings(deepseek_planning_mode="loop"), echo_transport())
    try:
        assert AdaptiveDeepSeekPlanner(client).propose(goal(), Constraints())[0] == goal()
        assert client.request_count == 2
    finally:
        client.close()


def test_adaptive_funds_condition_automatically_keeps_tool_loop():
    steps = normalize_steps(
        (
            Step(
                step_ref="redeem",
                action="product.redeem",
                arguments={
                    "account_ref": "account-demo-001",
                    "product_ref": "fund-demo",
                    "amount_minor": 100,
                    "fee_minor": 0,
                    "minimum_remaining_balance_minor": 0,
                },
            ),
            Step(
                step_ref="balance",
                action="accounts.balance",
                arguments={"account_ref": "account-demo-001"},
                depends_on=("redeem",),
                funds_available_after=("redeem",),
            ),
        ),
        Constraints(),
    )
    client = DeepSeekClient(settings(), echo_transport())
    try:
        assert AdaptiveDeepSeekPlanner(client).propose(steps, Constraints())[0] == steps
        assert client.request_count == 2
    finally:
        client.close()


def test_adaptive_candidate_still_cannot_change_business_parameters():
    altered = copy.deepcopy(goal())
    altered[0]["arguments"]["account_ref"] = "other-account"
    client = DeepSeekClient(
        settings(), httpx.MockTransport(lambda _: response(content=json.dumps({"steps": altered})))
    )
    try:
        with pytest.raises(ServiceError) as error:
            AdaptiveDeepSeekPlanner(client).propose(goal(), Constraints())
        assert error.value.code == "PLAN_SCOPE_CHANGED" and client.request_count == 1
    finally:
        client.close()


@pytest.mark.postgres
def test_same_live_harness_runs_with_mock_provider_and_isolated_business(database, bank):
    client = DeepSeekClient(settings(), echo_transport(), request_budget=32)
    try:
        report = run_live_benchmark(database, client, Path("tests/scenarios/backend-replay.json"))
        assert report["status"] == "PASSED", report
        assert report["requests_sent"] == 29
        assert report["eager_baseline"]["passed"]
        assert report["adaptive_small"]["passed"]
        assert report["agent_loop"]["case_count"] == 3
        assert all(len(c["planning"]["rounds"]) == 2 for c in report["agent_loop"]["cases"])
        assert bank.list_accounts("user-demo-001")[0]["balance_minor"] == 150000
        assert "private-model-reasoning" not in json.dumps(report)
    finally:
        client.close()


@pytest.mark.postgres
def test_http_uses_deepseek_loop_but_still_waits_for_business_approval(
    database, bank, backend, actor
):
    config = settings(
        database_url=database.dsn, database_schema=database.schema, worker_enabled=False
    )
    provider = DeepSeekClient(config, echo_transport())
    with TestClient(create_app(config, DeepSeekPlanner(provider))) as http:
        http.headers["Authorization"] = "Bearer " + backend.security.issue_token(actor.subject_ref)
        result = http.post(
            "/v1/tasks",
            json={
                "client_request_ref": "deepseek-http",
                "user_request": "给小王转300元，至少保留1000元",
            },
        )
        assert result.status_code == 201, result.text
        task = result.json()
        assert http.post(f"/v1/tasks/{task['task_ref']}/run").json()["status"] == "WAITING_APPROVAL"
        trace = http.get(f"/v1/tasks/{task['task_ref']}/trace").json()
        assert any(
            event["kind"] == "model_planning" and len(event["data"]["rounds"]) == 2
            for event in trace
        )
        assert "private-model-reasoning" not in json.dumps(trace)
        assert bank.list_accounts(actor.subject_ref)[0]["balance_minor"] == 150000
