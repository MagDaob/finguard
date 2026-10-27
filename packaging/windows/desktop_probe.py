"""本机组件挑战应答；启动器在显示页面前确认随机端口上的进程身份。"""

import hashlib
import hmac
import json
from urllib.parse import parse_qs


class DesktopIdentity:
    def __init__(self, app, key):
        self.app = app
        self.key = key.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/health/desktop":
            nonce = parse_qs(scope["query_string"].decode("ascii", errors="replace")).get(
                "nonce", [""]
            )[0]
            valid = (
                scope["method"] == "GET"
                and len(nonce) == 32
                and all(c in "0123456789abcdef" for c in nonce)
            )
            body = json.dumps(
                {
                    "signature": hmac.new(
                        self.key, ("finguard-desktop-v1|" + nonce).encode(), hashlib.sha256
                    ).hexdigest()
                }
                if valid
                else {"error": "invalid_probe"}
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200 if valid else 400,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
