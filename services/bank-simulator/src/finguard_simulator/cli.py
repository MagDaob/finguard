"""模拟服务的独立迁移、数据和启动入口。"""

import argparse
import json

from finguard_simulator.config import Settings
from finguard_simulator.database import Database
from finguard_simulator.demo import seed_demo


def main():
    parser = argparse.ArgumentParser(prog="finguard-bank-simulator")
    parser.add_argument("command", choices=("migrate", "seed-demo", "serve"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    settings = Settings.from_env()
    database = Database(settings.database_url.get_secret_value(), settings.database_schema)
    database.migrate()
    if args.command == "seed-demo":
        seed_demo(database)
    elif args.command == "serve":
        import uvicorn

        from finguard_simulator.api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return
    print(json.dumps({"status": "OK", "service": "bank-simulator", "command": args.command}))
