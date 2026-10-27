"""本机管理命令；令牌只写入用户指定的本地文件。"""

import json
import time
from pathlib import Path

import psycopg
from pydantic import ValidationError

from finguard.backend.config import Settings
from finguard.backend.database import Database
from finguard.backend.demo import seed_demo
from finguard.backend.errors import ServiceError, require
from finguard.backend.security import ADMIN_SCOPES, USER_SCOPES, Security
from finguard.backend.service import Backend


def register_commands(commands) -> None:
    commands.add_parser("migrate", help="创建或升级本地数据库结构")
    seed = commands.add_parser("seed-demo", help="导入虚构业务数据并写入演示令牌文件")
    seed.add_argument("--output", type=Path, default=Path("artifacts/demo-tokens.local.json"))
    seed.add_argument("--rotate", action="store_true")
    user = commands.add_parser("create-user", help="在本机可信管理环境中建立主体和访问令牌")
    user.add_argument("--subject", required=True)
    user.add_argument("--name", required=True)
    user.add_argument("--risk-level", type=int, choices=range(6), default=1)
    user.add_argument("--admin", action="store_true")
    user.add_argument("--output", type=Path, required=True)
    serve = commands.add_parser("serve", help="运行模拟业务后端")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    worker = commands.add_parser("worker", help="核实未完成提交并恢复已请求的任务")
    worker.add_argument("--once", action="store_true")
    replay = commands.add_parser("replay", help="在独立命名空间回放虚构业务套件")
    replay.add_argument("--suite", type=Path, required=True)
    replay.add_argument("--output", type=Path, default=Path("artifacts/replay-report.json"))
    replay.add_argument("--model", action="store_true", help="使用已配置模型网关重新规划")
    bench = commands.add_parser(
        "model-benchmark", help="测试 DeepSeek 官方 API 吞吐和隔离 Agent 循环"
    )
    bench.add_argument(
        "--live", action="store_true", help="显式启用付费网络请求；最多 32 次，并发最高 4"
    )
    bench.add_argument("--suite", type=Path, default=Path("tests/scenarios/backend-replay.json"))
    bench.add_argument("--output", type=Path, default=Path("artifacts/deepseek-live-report.json"))


def _write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def run_command(args) -> int:
    try:
        settings = Settings.from_env()
        db = Database(settings.database_url.get_secret_value(), settings.database_schema)
        db.migrate()
        security = Security(db)
        if args.command == "model-benchmark":
            from finguard.backend.benchmark import run_live_benchmark
            from finguard.backend.deepseek import DeepSeekClient

            require(args.live, "LIVE_FLAG_REQUIRED", "真实测试需要显式提供 --live。", 422)
            settings = settings.model_copy(
                update={
                    "deepseek_concurrency": 4,
                    "deepseek_max_rounds": 4,
                    "deepseek_thinking": False,
                    "deepseek_planning_mode": "auto",
                    "deepseek_max_tokens": min(settings.deepseek_max_tokens, 2048),
                }
            )
            client = DeepSeekClient(settings, request_budget=32)
            try:
                report = run_live_benchmark(
                    db,
                    client,
                    args.suite,
                    progress=lambda data: print(json.dumps({"progress": data}), flush=True),
                )
                _write_private(args.output, report)
                print(
                    json.dumps(
                        {
                            "status": report["status"],
                            "requests_sent": report["requests_sent"],
                            "report_file": str(args.output),
                        }
                    )
                )
                return 0 if report["status"] == "PASSED" else 1
            finally:
                client.close()
        elif args.command == "migrate":
            print(json.dumps({"status": "MIGRATED", "schema": db.schema}))
        elif args.command == "seed-demo":
            seed_demo(db)
            if args.output.exists() and not args.rotate:
                data = json.loads(args.output.read_text(encoding="utf-8"))
                for key in ("user", "recipient", "operator"):
                    security.authenticate(data[key]["token"])
            else:
                data = {}
                for key, subject, scopes in (
                    ("user", "user-demo-001", USER_SCOPES),
                    ("recipient", "user-demo-002", USER_SCOPES),
                    ("operator", "operator-demo", ADMIN_SCOPES),
                ):
                    data[key] = {
                        "subject_ref": subject,
                        "token": security.issue_token(subject, scopes),
                    }
                _write_private(args.output, data)
            print(
                json.dumps(
                    {"status": "SEEDED", "token_file": str(args.output), "mode": "simulation"}
                )
            )
        elif args.command == "create-user":
            from pydantic import TypeAdapter

            from finguard.domain.values import Reference

            TypeAdapter(Reference).validate_python(args.subject, strict=True)
            require(
                not args.output.exists(), "OUTPUT_EXISTS", "令牌输出文件已存在，请选择新文件。", 409
            )
            security.provision(args.subject, args.name, args.risk_level)
            _write_private(
                args.output,
                {
                    "subject_ref": args.subject,
                    "token": security.issue_token(
                        args.subject, ADMIN_SCOPES if args.admin else USER_SCOPES
                    ),
                },
            )
            print(json.dumps({"status": "CREATED", "token_file": str(args.output)}))
        elif args.command == "serve":
            import uvicorn

            from finguard.backend.api import create_app

            uvicorn.run(create_app(settings), host=args.host, port=args.port)
        elif args.command == "worker":
            backend = Backend(db)
            while True:
                print(json.dumps(backend.recover()))
                if args.once:
                    break
                time.sleep(settings.worker_interval_seconds)
        elif args.command == "replay":
            from finguard.backend.evaluation import ReplayRunner, ReplaySuite
            from finguard.backend.model_factory import configured_planner
            from finguard.backend.policies import PolicyPlanner

            planner = None
            if args.model:
                gateway = configured_planner(settings)
                require(gateway is not None, "MODEL_NOT_CONFIGURED", "尚未配置模型。", 503)
                planner = PolicyPlanner(db, gateway)
            suite = ReplaySuite.model_validate_json(args.suite.read_text(encoding="utf-8"))
            report = ReplayRunner(db).run(suite, planner)
            _write_private(args.output, report)
            print(
                json.dumps(
                    {
                        "status": "PASSED" if report["passed"] else "FAILED",
                        "report_file": str(args.output),
                        "mode": report["mode"],
                    }
                )
            )
            return 0 if report["passed"] else 1
        else:
            raise ServiceError("UNKNOWN_COMMAND", "命令不存在。")
    except KeyboardInterrupt:
        return 0
    except (ServiceError, ValidationError, psycopg.Error, OSError, ValueError, KeyError) as exc:
        code = exc.code if isinstance(exc, ServiceError) else "COMMAND_FAILED"
        print(json.dumps({"status": "FAILED", "code": code}))
        return 1
    return 0
