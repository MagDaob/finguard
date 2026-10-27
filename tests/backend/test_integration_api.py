import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finguard.backend.api import create_app
from finguard.backend.config import Settings

pytestmark = pytest.mark.postgres


@pytest.fixture
def integration_client(database, bank, actor, backend):
    settings = Settings(
        database_url=database.dsn, database_schema=database.schema, worker_enabled=False
    )
    with TestClient(create_app(settings, bank=bank)) as client:
        client.headers["Authorization"] = "Bearer " + backend.security.issue_token(
            actor.subject_ref
        )
        yield client


def test_contract_catalog_has_schemas_but_no_storage_or_runtime(integration_client):
    result = integration_client.get("/v1/integrations/contracts")
    assert result.status_code == 200
    data = result.json()
    assert {"BankConnection", "ModelRequest", "PluginManifest"} <= set(data["schemas"])
    assert data["mode"] == "contracts_only" and not data["plugin_runtime_available"]
    assert not data["connection_management"]["persistent_storage"]


def test_configuration_validation_never_builds_a_client_or_claims_connectivity(
    integration_client, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("Local validation must not build a network client")

    monkeypatch.setattr(integration_client.app.state.bank_registry, "build", forbidden)
    monkeypatch.setattr(integration_client.app.state.model_registry, "build", forbidden)
    bank = integration_client.post(
        "/v1/integrations/bank-connections/validate",
        json={
            "connection_ref": "future-bank",
            "provider": "not-installed",
            "environment": "external",
        },
    )
    assert bank.status_code == 200 and not bank.json()["provider_registered"]
    model = integration_client.post(
        "/v1/integrations/model-connections/validate",
        json={
            "connection_ref": "future-model",
            "provider": "not-installed",
            "protocol": "chat_completions",
            "base_url": "https://unreachable.example/v1",
            "model_id": "future-model",
            "credential_ref": "secret:user-key",
        },
    )
    assert model.status_code == 200
    data = model.json()
    assert (
        data["valid"]
        and not data["network_request_sent"]
        and not data["activated"]
        and not data["persisted"]
    )
    assert not data["protocol_implemented"] and data["credential_status"] == "reference_only"


def test_validation_does_not_accept_owner_or_plaintext_key(integration_client):
    body = {
        "connection_ref": "model-1",
        "provider": "custom",
        "protocol": "chat_completions",
        "base_url": "https://model.example/v1",
        "model_id": "my-model",
    }
    secret = "private-value-that-must-not-be-returned"
    response = integration_client.post(
        "/v1/integrations/model-connections/validate", json={**body, "api_key": secret}
    )
    assert response.status_code == 422 and secret not in response.text
    response = integration_client.post(
        "/v1/integrations/model-connections/validate", json={**body, "owner_ref": "someone-else"}
    )
    assert response.status_code == 422


def test_plugins_are_validated_without_registering_or_running(integration_client):
    payload = {"manifest": {"plugin_id": "user-report", "version": "1.0.0", "kind": "report"}}
    response = integration_client.post("/v1/integrations/plugins/validate", json=payload)
    assert response.status_code == 200
    assert not response.json()["registered"] and not response.json()["permissions_granted"]
    assert integration_client.app.state.plugin_registry.describe() == []
    response = integration_client.post(
        "/v1/integrations/plugins/validate",
        json={**payload, "configuration": {"unexpected": "private-value"}},
    )
    assert response.status_code == 422 and "private-value" not in response.text


def test_public_plugin_example_accepts_json_arrays_over_http(integration_client):
    manifest = json.loads(Path("plugins/examples/personal-report.json").read_text(encoding="utf-8"))
    response = integration_client.post(
        "/v1/integrations/plugins/validate",
        json={"manifest": manifest, "configuration": {"title": "个人月报"}},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["valid"] and not response.json()["permissions_granted"]


def test_contracts_require_a_current_scoped_identity(integration_client, backend, actor):
    integration_client.headers["Authorization"] = "Bearer " + backend.security.issue_token(
        actor.subject_ref, ("accounts.read",)
    )
    assert integration_client.get("/v1/integrations/contracts").status_code == 403
    integration_client.headers["Authorization"] = "Bearer invalid"
    assert integration_client.get("/v1/integrations/contracts").status_code == 401
