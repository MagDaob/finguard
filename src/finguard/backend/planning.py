"""本地可解释规划基线与可替换模型网关；候选计划不能扩张业务目标。"""

import json
import re
from decimal import Decimal
from urllib.parse import urlparse

import httpx

from finguard.backend.catalog import ACTIONS, Constraints, Step, canonical, normalize_steps
from finguard.backend.errors import ServiceError, require


def money_minor(text: str) -> int:
    value = Decimal(text)
    require(
        value > 0 and value.as_tuple().exponent >= -2,
        "INVALID_AMOUNT",
        "金额必须为正数，且最多包含两位小数。",
        422,
    )
    return int(value * 100)


class BaselinePlanner:
    version = "deterministic-v1"

    def infer(
        self, message: str, context: dict, constraints: Constraints
    ) -> tuple[tuple[Step, ...], Constraints, list[dict]]:
        if re.search(r"不要|不转|别|禁止|撤销|如果|除非|(?<!到)超过", message):
            return (
                (),
                constraints,
                [
                    {
                        "field": "goals",
                        "message": "请求含否定或尚未支持的条件，请确认结构化目标与约束。",
                        "options": [],
                    }
                ],
            )
        questions = []
        steps = []
        accounts = [account for account in context["accounts"] if account["active"]]
        reserve = re.search(r"(?:至少保留|至少留|余额不少于)\s*(\d+(?:\.\d+)?)\s*元", message)
        if reserve:
            constraints = Constraints(
                minimum_remaining_balance_minor=max(
                    constraints.minimum_remaining_balance_minor, money_minor(reserve[1])
                ),
                maximum_fee_minor=constraints.maximum_fee_minor,
            )
        source = accounts[0]["account_ref"] if len(accounts) == 1 else None
        if source is None:
            questions.append(
                {
                    "field": "account_ref",
                    "message": "请选择付款或分析账户。",
                    "options": [account["account_ref"] for account in accounts],
                }
            )
        transfer = re.search(r"转(?:账)?\s*(\d+(?:\.\d+)?)\s*元", message)
        if transfer:
            recipients = [
                recipient
                for recipient in context["recipients"]
                if recipient["active"]
                and re.search(r"(?:给|向)\s*" + re.escape(recipient["display_name"]), message)
            ]
            if len(recipients) != 1:
                questions.append(
                    {
                        "field": "recipient_ref",
                        "message": "请确认具体收款人。",
                        "options": [
                            recipient["recipient_ref"]
                            for recipient in context["recipients"]
                            if recipient["active"]
                        ],
                    }
                )
            elif source:
                steps.append(
                    Step(
                        step_ref="transfer",
                        action="transfer.create",
                        arguments={
                            "source_account_ref": source,
                            "recipient_ref": recipients[0]["recipient_ref"],
                            "amount_minor": money_minor(transfer[1]),
                            "currency": "CNY",
                            "fee_minor": 0,
                            "minimum_remaining_balance_minor": constraints.minimum_remaining_balance_minor,
                        },
                    )
                )
        if "账单" in message and source:
            steps.append(
                Step(
                    step_ref="bills",
                    action="bills.analyze",
                    arguments={"account_ref": source, "days": 30},
                )
            )
        for word, action in (("赎回", "product.redeem"), ("申购", "product.subscribe")):
            if word not in message:
                continue
            products = [product for product in context["products"] if product["name"] in message]
            if not products and action == "product.redeem":
                owned = {
                    position["product_ref"]
                    for position in context["positions"]
                    if position["value_minor"] > 0 and position["account_ref"] == source
                }
                products = [
                    product for product in context["products"] if product["product_ref"] in owned
                ]
            clause = message.split(word, 1)[1]
            amount = re.search(r"(\d+(?:\.\d+)?)\s*元", clause)
            if len(products) != 1 or not amount:
                questions.append(
                    {
                        "field": "product_order",
                        "message": "请指定产品与申赎金额。",
                        "options": [product["product_ref"] for product in context["products"]],
                    }
                )
            elif source:
                steps.insert(
                    0,
                    Step(
                        step_ref="redeem" if action.endswith("redeem") else "subscribe",
                        action=action,
                        arguments={
                            "account_ref": source,
                            "product_ref": products[0]["product_ref"],
                            "amount_minor": money_minor(amount[1]),
                            "fee_minor": 0,
                            "minimum_remaining_balance_minor": 0,
                        },
                    ),
                )
        if "取消" in message and "订阅" in message:
            resources = [item for item in context["subscriptions"] if item["state"] == "ACTIVE"]
            if len(resources) == 1:
                steps.append(
                    Step(
                        step_ref="cancel-subscription",
                        action="subscription.cancel",
                        arguments={"subscription_ref": resources[0]["resource_ref"]},
                    )
                )
            else:
                questions.append(
                    {
                        "field": "subscription_ref",
                        "message": "请指定要取消的订阅。",
                        "options": [item["resource_ref"] for item in resources],
                    }
                )
        if "锁卡" in message or "锁定卡" in message or "解锁卡" in message:
            if len(context["cards"]) == 1:
                steps.append(
                    Step(
                        step_ref="card",
                        action="card.set_lock",
                        arguments={
                            "card_ref": context["cards"][0]["resource_ref"],
                            "locked": "解锁" not in message,
                        },
                    )
                )
            else:
                questions.append(
                    {
                        "field": "card_ref",
                        "message": "请选择卡片。",
                        "options": [item["resource_ref"] for item in context["cards"]],
                    }
                )
        if "到账后" in message and any(step.step_ref == "redeem" for step in steps):
            steps = [
                Step.model_validate(
                    {
                        **step.model_dump(),
                        "depends_on": ("redeem",),
                        "funds_available_after": ("redeem",),
                    }
                )
                if step.step_ref == "transfer"
                else step
                for step in steps
            ]
        if not steps and not questions:
            questions.append(
                {
                    "field": "goals",
                    "message": "请提供明确的业务目标，或提交结构化步骤。",
                    "options": [],
                }
            )
        return tuple(steps), constraints, questions

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
