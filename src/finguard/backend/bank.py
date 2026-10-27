"""银行接口客户端；不加载模拟器代码或访问银行数据库。"""

from typing import Protocol

import httpx
from finguard_bank_contracts.connections import BankCapabilities
from finguard_bank_contracts.errors import (
    NotSubmitted as NotSubmitted,
)
from finguard_bank_contracts.errors import (
    ResultUnknown as ResultUnknown,
)
from finguard_bank_contracts.errors import (
    WorkerCrash as WorkerCrash,
)

from finguard.backend.errors import ServiceError


class Bank(Protocol):
    def get_capabilities(self) -> BankCapabilities: ...
    def list_accounts(self, subject: str) -> list[dict]: ...
    def list_recipients(self, subject: str) -> list[dict]: ...
    def list_services(self, subject: str, kind: str) -> list[dict]: ...
    def list_products(self) -> list[dict]: ...
    def list_positions(self, subject: str) -> list[dict]: ...
    def list_ledger(self, subject: str, account_ref: str, limit=50, before=None) -> list[dict]: ...
    def preview(self, subject: str, action: str, arguments: dict) -> dict: ...
    def execute(self, subject: str, operation_ref: str, action: str, arguments: dict) -> dict: ...
    def lookup(
        self,
        subject: str,
        operation: str,
        fingerprint: str,
        fence_if_absent=False,
        action=None,
        arguments=None,
    ) -> dict | None: ...
    def verify(
        self, subject: str, operation: str, fingerprint: str
    ) -> tuple[dict | None, list[dict]]: ...


class HttpBank:
    def __init__(self, url: str, token: str, timeout: float = 10, *, client=None):
        self.client = client or httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    @classmethod
    def configured(cls, settings):
        from finguard.integrations.banks import configured_bank

        return configured_bank(settings)

    def get_capabilities(self) -> BankCapabilities:
        try:
            response = self.client.get("/v1/capabilities")
            response.raise_for_status()
            return BankCapabilities.model_validate_json(response.content)
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceError(
                "BANK_CAPABILITIES_UNAVAILABLE", "银行能力声明不可用。", 503
            ) from exc

    def close(self):
        self.client.close()

    def ready(self):
        try:
            response = self.client.get("/health/ready")
            response.raise_for_status()
            if response.json().get("status") != "ready":
                raise ValueError()
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise ServiceError("BANK_UNAVAILABLE", "银行服务暂时不可用。", 503) from exc

    def _call(self, method, **payload):
        try:
            response = self.client.post(f"/v1/bank/{method}", json=payload)
        except httpx.HTTPError as exc:
            if method == "execute":
                raise ResultUnknown() from exc
            raise ServiceError("BANK_UNAVAILABLE", "银行服务暂时不可用。", 503) from exc
        try:
            data = response.json()
            if response.is_success:
                if not isinstance(data, dict) or "result" not in data:
                    raise ValueError()
                result = data["result"]
                if method.startswith("list_") and not isinstance(result, list):
                    raise ValueError()
                if method in {"execute", "preview"} and not isinstance(result, dict):
                    raise ValueError()
                if method == "lookup" and result is not None and not isinstance(result, dict):
                    raise ValueError()
                return result
            error = data.get("error", {})
            code = error.get("code")
            if code == "NOT_SUBMITTED":
                raise NotSubmitted()
            if code in {"RESULT_UNKNOWN", "WORKER_CRASH"}:
                raise ResultUnknown()
            if not isinstance(code, str) or response.status_code >= 500:
                raise ValueError()
            raise ServiceError(code, "银行服务拒绝该请求。", response.status_code)
        except (ValueError, TypeError, AttributeError) as exc:
            if method == "execute":
                raise ResultUnknown() from exc
            raise ServiceError("BANK_INVALID_RESPONSE", "银行服务返回无效结果。", 503) from exc

    def list_accounts(self, subject):
        return self._call("list_accounts", subject=subject)

    def list_recipients(self, subject):
        return self._call("list_recipients", subject=subject)

    def list_services(self, subject, kind):
        return self._call("list_services", subject=subject, kind=kind)

    def list_products(self):
        return self._call("list_products")

    def list_positions(self, subject):
        return self._call("list_positions", subject=subject)

    def list_ledger(self, subject, account_ref, limit=50, before=None):
        return self._call(
            "list_ledger", subject=subject, account_ref=account_ref, limit=limit, before=before
        )

    def preview(self, subject, action, arguments):
        return self._call("preview", subject=subject, action=action, arguments=arguments)

    def execute(self, subject, operation_ref, action, arguments):
        return self._call(
            "execute",
            subject=subject,
            operation_ref=operation_ref,
            action=action,
            arguments=arguments,
        )

    def lookup(
        self, subject, operation, fingerprint, fence_if_absent=False, action=None, arguments=None
    ):
        return self._call(
            "lookup",
            subject=subject,
            operation=operation,
            fingerprint=fingerprint,
            fence_if_absent=fence_if_absent,
            action=action,
            arguments=arguments,
        )

    def verify(self, subject, operation, fingerprint):
        result = self._call("verify", subject=subject, operation=operation, fingerprint=fingerprint)
        if not isinstance(result, list) or len(result) != 2 or not isinstance(result[1], list):
            raise ServiceError("BANK_INVALID_RESPONSE", "银行证据结构无效。", 503)
        return result[0], result[1]
