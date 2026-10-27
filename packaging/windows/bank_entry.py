"""独立模拟银行入口；不加载项目本体。"""

import os
import sys
import threading
import time
from pathlib import Path

import uvicorn
from desktop_probe import DesktopIdentity
from finguard_simulator.api import create_app
from finguard_simulator.config import Settings
from finguard_simulator.database import Database
from finguard_simulator.demo import seed_demo


def main():
    settings = Settings.from_env(None)
    if sys.argv[1] == "init":
        database = Database(settings.database_url.get_secret_value(), settings.database_schema)
        database.migrate()
        seed_demo(database)
        return
    server = uvicorn.Server(
        uvicorn.Config(
            DesktopIdentity(create_app(settings), os.environ["SIMBANK_DESKTOP_PROBE_KEY"]),
            host="127.0.0.1",
            port=int(sys.argv[2]),
            log_level="warning",
            access_log=False,
        )
    )

    def watch():
        while not server.should_exit:
            if Path(os.environ["SIMBANK_DESKTOP_STOP_FILE"]).exists():
                server.should_exit = True
            time.sleep(0.25)

    threading.Thread(target=watch, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
