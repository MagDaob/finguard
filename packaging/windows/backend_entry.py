"""桌面包的独立后端入口；服务端会话只通过父进程的私有管道返回。"""

import json
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn
from desktop_probe import DesktopIdentity

from finguard.backend.api import create_app
from finguard.backend.config import Settings
from finguard.backend.database import Database
from finguard.backend.demo import seed_demo
from finguard.backend.security import Security


def main():
    settings = Settings.from_env(None)
    database = Database(settings.database_url.get_secret_value(), settings.database_schema)
    if sys.argv[1] in {"init", "session"}:
        if sys.argv[1] == "init":
            database.migrate()
            seed_demo(database)
        else:
            print(json.dumps({"token": Security(database).issue_token("user-demo-001")}))
        return
    server = uvicorn.Server(
        uvicorn.Config(
            DesktopIdentity(create_app(settings), os.environ["FINGUARD_DESKTOP_PROBE_KEY"]),
            host="127.0.0.1",
            port=int(sys.argv[2]),
            log_level="warning",
            access_log=False,
        )
    )

    def watch():
        while not server.should_exit:
            if Path(os.environ["FINGUARD_DESKTOP_STOP_FILE"]).exists():
                server.should_exit = True
            time.sleep(0.25)

    threading.Thread(target=watch, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
