"""本地可解释规划基线与可替换模型网关；候选计划不能扩张业务目标。"""

import json
from urllib.parse import urlparse

import httpx

from finguard.backend.catalog import ACTIONS, Constraints, Step, canonical, normalize_steps
from finguard.backend.errors import ServiceError, require


class BaselinePlanner:
    version = "deterministic-v2"

    def infer(self, message: str, context: dict, constraints: Constraints):
        from finguard.backend.understanding import infer

        return infer(message, context, constraints)

    def propose(self, goals: list[dict], constraints: Constraints) -> tuple[list[dict], str]:
        return goals, self.version


class GatewayPlanner(BaselinePlanner):
    """网关协议为 POST JSON -> {steps, planner_version}，不绑定模型供应商。"""

    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        allow_local_http: bool = False,
        prompt: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        parsed = urlparse(url)
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        require(
            parsed.scheme == "https" or (allow_local_http and local and parsed.scheme == "http"),
            "INVALID_MODEL_URL",
            "模型网关必须使用 HTTPS。",
        )
        require(
            not parsed.username and not parsed.password and not parsed.fragment,
            "INVALID_MODEL_URL",
            "模型地址不能嵌入凭据或片段。",
        )
        self.url, self.api_key, self.prompt = url, api_key, prompt
        self.allow_local_http, self.transport = allow_local_http, transport

    def with_prompt(self, prompt):
        return GatewayPlanner(self.url, self.api_key, self.allow_local_http, prompt, self.transport)

    def propose(self, goals: list[dict], constraints: Constraints) -> tuple[list[dict], str]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {
            "goals": goals,
            "constraints": constraints.model_dump(mode="json"),
            "tools": {
                name: definition.arguments.model_json_schema()
                for name, definition in ACTIONS.items()
            },
            "policy": "Preserve every goal, action, arguments and required dependencies. Return a candidate plan only.",
            "prompt_candidate": self.prompt,
        }
        try:
            with httpx.Client(
                timeout=20, follow_redirects=False, transport=self.transport
            ) as client:
                with client.stream("POST", self.url, json=payload, headers=headers) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        require(
                            size <= 1048576, "MODEL_RESPONSE_TOO_LARGE", "模型响应超出限制。", 502
                        )
                        chunks.append(chunk)
                    data = json.loads(b"".join(chunks))
            return [
                Step.model_validate(item).model_dump(mode="json") for item in data["steps"]
            ], str(data.get("planner_version", "gateway"))[:128]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ServiceError("MODEL_UNAVAILABLE", "模型网关不可用或响应无效。", 503) from exc


def compile_candidate(
    goals: list[dict], candidate: list[dict], constraints: Constraints
) -> list[dict]:
    normalized = normalize_steps(
        tuple(Step.model_validate(step) for step in candidate), constraints
    )
    expected = {step["step_ref"]: step for step in goals}
    require(
        {step["step_ref"] for step in normalized} == set(expected),
        "GOAL_COVERAGE",
        "候选计划遗漏或新增了业务目标。",
        422,
    )
    for step in normalized:
        goal = expected[step["step_ref"]]
        require(
            step["action"] == goal["action"]
            and canonical(step["arguments"]) == canonical(goal["arguments"]),
            "PLAN_SCOPE_CHANGED",
            "候选计划改变了业务操作。",
            422,
        )
        require(
            set(goal["depends_on"]) <= set(step["depends_on"])
            and set(goal["funds_available_after"]) <= set(step["funds_available_after"]),
            "PLAN_CONDITION_REMOVED",
            "候选计划移除了业务条件。",
            422,
        )
    lookup = {step["step_ref"]: step for step in normalized}
    for step in normalized:
        for dependency in step["funds_available_after"]:
            source = lookup[dependency]
            target_account = step["arguments"].get(
                "source_account_ref", step["arguments"].get("account_ref")
            )
            require(
                source["action"] == "product.redeem"
                and source["arguments"]["account_ref"] == target_account,
                "INVALID_FUNDS_DEPENDENCY",
                "资金到账条件必须引用同一账户的赎回操作。",
                422,
            )
    return normalized
