"""显式启动的小规模真实模型测试；业务回放始终使用隔离虚构数据。"""

import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from finguard.backend.catalog import ACTIONS, canonical
from finguard.backend.database import Database
from finguard.backend.deepseek import DeepSeekClient, DeepSeekPlanner, SubmitPlan
from finguard.backend.errors import ServiceError
from finguard.backend.evaluation import ReplayRunner, ReplaySuite
from finguard.backend.planning import BaselinePlanner, compile_candidate


class EagerBaseline(BaselinePlanner):
    """对照实现一次发送全部模式，只返回候选，仍接受相同控制校验。"""

    def __init__(self, client):
        self.client, self.report = client, None

    def diagnostics(self):
        return self.report

    def propose(self, goals, constraints):
        self.report = None
        result = self.client.complete(
            [
                {
                    "role": "system",
                    "content": "Return JSON {steps:[...]} only. Preserve every goal, exact arguments, dependencies and funds conditions. Never execute or authorize anything.",
                },
                {
                    "role": "user",
                    "content": canonical(
                        {
                            "goals": goals,
                            "constraints": constraints.model_dump(mode="json"),
                            "tools": {
                                name: definition.arguments.model_json_schema()
                                for name, definition in ACTIONS.items()
                            },
                        }
                    ),
                },
            ],
            json_output=True,
        )
        self.report = {
            "status": "RECEIVED",
            "rounds": [
                {
                    "round": 1,
                    "model": result.model,
                    "latency_ms": result.latency_ms,
                    "usage": result.usage,
                }
            ],
            "tool_calls": [],
        }
        try:
            plan = SubmitPlan.model_validate_json(result.message.get("content") or "")
        except (ValueError, ValidationError) as exc:
            raise ServiceError("MODEL_INVALID_RESPONSE", "对照计划不是有效 JSON。", 502) from exc
        candidate = compile_candidate(
            goals, [s.model_dump(mode="json") for s in plan.steps], constraints
        )
        self.report["status"] = "VALIDATED"
        return candidate, "deepseek-eager-baseline:" + self.client.settings.deepseek_model


def percentile(values, ratio):
    return sorted(values)[max(0, math.ceil(len(values) * ratio) - 1)] if values else None


def throughput(client: DeepSeekClient, progress=None) -> list[dict]:
    stages = []
    for concurrency, count in ((1, 4), (2, 4), (4, 8)):
        started = time.perf_counter()

        def invoke(_):
            began = time.perf_counter()
            try:
                result = client.complete(
                    [
                        {
                            "role": "system",
                            "content": "Synthetic connectivity test. Reply with the single word OK.",
                        },
                        {"role": "user", "content": "Ping. Reply OK."},
                    ],
                    max_tokens=16,
                )
                return {
                    "ok": True,
                    "latency_ms": result.latency_ms,
                    "usage": result.usage,
                    "model": result.model,
                }
            except ServiceError as exc:
                return {
                    "ok": False,
                    "latency_ms": round((time.perf_counter() - began) * 1000, 2),
                    "error_code": exc.code,
                }

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(invoke, range(count)))
        elapsed = time.perf_counter() - started
        successful = [r for r in results if r["ok"]]
        latencies = [r["latency_ms"] for r in successful]
        tokens = [
            r["usage"]["completion_tokens"]
            for r in successful
            if r["usage"]["completion_tokens"] is not None
        ]
        stages.append(
            {
                "concurrency": concurrency,
                "request_count": count,
                "success_count": len(successful),
                "error_count": count - len(successful),
                "elapsed_seconds": round(elapsed, 3),
                "successful_requests_per_second": round(len(successful) / elapsed, 4),
                "completion_tokens_per_second": round(sum(tokens) / elapsed, 3) if tokens else None,
                "latency_p50_ms": percentile(latencies, 0.5),
                "latency_p95_ms": percentile(latencies, 0.95),
                "requests": results,
            }
        )
        if progress:
            progress(
                {
                    "phase": "throughput",
                    "concurrency": concurrency,
                    "success_count": len(successful),
                    "request_count": count,
                }
            )
    return stages


def run_live_benchmark(
    database: Database, client: DeepSeekClient, suite_path: Path, progress=None
) -> dict:
    report = {
        "format_version": "1",
        "started_at": datetime.now(UTC).isoformat(),
        "provider": "deepseek",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "requested_model": client.settings.deepseek_model,
        "thinking": client.settings.deepseek_thinking,
        "request_budget": client.request_budget,
        "automatic_retries": 0,
        "request_count_semantics": "client_attempts_not_billing",
        "business_mode": "isolated_simulation",
        "contains_credentials": False,
        "scope": "small_sample_not_provider_capacity",
    }
    try:
        probe = client.complete(
            [{"role": "user", "content": "Synthetic connectivity test. Reply OK."}], max_tokens=16
        )
        report["probe"] = {
            "status": "PASSED",
            "model": probe.model,
            "usage": probe.usage,
            "latency_ms": probe.latency_ms,
        }
    except ServiceError as exc:
        report.update(
            status="FAILED",
            probe={"status": "FAILED", "error_code": exc.code},
            throughput=[],
            agent_loop={"status": "NOT_RUN", "reason": "PROVIDER_PREFLIGHT_FAILED"},
            requests_sent=client.request_count,
        )
        return report
    report["throughput"] = throughput(client, progress)
    if not any(stage["success_count"] for stage in report["throughput"]):
        report.update(
            status="FAILED",
            agent_loop={"status": "NOT_RUN", "reason": "NO_SUCCESSFUL_PROVIDER_REQUEST"},
            requests_sent=client.request_count,
        )
        return report
    suite = ReplaySuite.model_validate_json(suite_path.read_text(encoding="utf-8"))
    suite = ReplaySuite(format_version=suite.format_version, cases=suite.cases[:3])
    planner = DeepSeekPlanner(client)
    report["eager_baseline"] = ReplayRunner(database).run(suite, EagerBaseline(client))
    if progress:
        progress({"phase": "eager_baseline", "passed": report["eager_baseline"]["passed"]})
    report["agent_loop"] = ReplayRunner(database).run(suite, planner)
    report["agent_diagnostics"] = planner.diagnostics()
    report["requests_sent"] = client.request_count
    report["status"] = (
        "PASSED"
        if all(stage["error_count"] == 0 for stage in report["throughput"])
        and report["agent_loop"]["passed"]
        and report["eager_baseline"]["passed"]
        else "FAILED"
    )
    report["finished_at"] = datetime.now(UTC).isoformat()
    return report
