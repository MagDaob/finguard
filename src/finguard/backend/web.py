"""可选的本机构建页面，同源访问 API；无构建产物时不改变纯 API 部署。"""

from pathlib import Path

from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class WorkbenchFiles(StaticFiles):
    async def get_response(self, path, scope):
        route = path.replace("\\", "/").strip("/")
        if route == "" or route.split("/", 1)[0] in {
            "tasks",
            "accounts",
            "authorizations",
            "products",
            "services",
            "settings",
            "lab",
        }:
            return FileResponse(
                Path(self.directory) / "index.html",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
        if not route.startswith("assets/"):
            raise HTTPException(404)
        return await super().get_response(path, scope)


def register_workbench(app, directory):
    folder = Path(directory).resolve()
    if (folder / "index.html").is_file():
        app.mount("/", WorkbenchFiles(directory=folder), name="workbench")
