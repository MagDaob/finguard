"""本地场景结构校验入口。"""

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Annotated

from pydantic import Field, ValidationError

from finguard.domain.values import DomainModel
from finguard.scenarios import load_transfer_scenario


class ScenarioConfig(DomainModel):
    path: Annotated[str, Field(min_length=1)]


class DevelopmentConfig(DomainModel):
    scenario: ScenarioConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finguard")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-scenario", help="校验虚构转账场景，不执行交易")
    inputs = validate.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--scenario", type=Path, help="场景 JSON 文件")
    inputs.add_argument("--config", type=Path, help="开发配置 TOML 文件")
    args = parser.parse_args(argv)
    try:
        scenario_path = args.scenario
        if args.config:
            with args.config.open("rb") as file:
                config = DevelopmentConfig.model_validate(tomllib.load(file))
            scenario_path = args.config.resolve().parent / config.scenario.path
        scenario = load_transfer_scenario(scenario_path)
        task, plan, operation = scenario.to_domain()
    except ValidationError as exc:
        print(
            json.dumps(
                {
                    "status": "INVALID",
                    "errors": exc.errors(
                        include_input=False, include_context=False, include_url=False
                    ),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        print(
            json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": "VALIDATED",
                "scenario_id": scenario.scenario_id,
                "task_ref": task.task_ref,
                "plan_ref": plan.plan_ref,
                "operation_ref": operation.operation_ref,
                "operation_status": operation.status,
                "projected_balance_minor": scenario.projected_balance_minor,
                "currency": operation.parameters.currency,
                "execution_performed": False,
                "authorization_created": False,
            },
            ensure_ascii=False,
        )
    )
    return 0
