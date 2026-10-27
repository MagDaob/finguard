import json
from datetime import UTC, datetime, timedelta

import pytest
from finguard_plugin_contracts.contracts import (
    PluginInvocation,
    PluginManifest,
    PluginResult,
    validate_invocation,
    validate_result,
)
from finguard_plugin_contracts.schemas import check_schema
from pydantic import ValidationError

from finguard.backend.errors import ServiceError
from finguard.plugins.registry import PluginRegistry


def manifest(**changes):
    return PluginManifest.model_validate_json(
        json.dumps(
            {
                "plugin_id": "personal-report",
                "version": "1.0.0",
                "kind": "report",
                "requested_permissions": ["transactions.read"],
                "tools": [
                    {
                        "name": "summarize",
                        "effect": "read",
                        "permissions": ["transactions.read"],
                        "input_schema": {
                            "type": "object",
                            "properties": {"days": {"type": "integer", "minimum": 1}},
                            "required": ["days"],
                            "additionalProperties": False,
                        },
                        "output_schema": {
                            "type": "object",
                            "properties": {"count": {"type": "integer"}},
                            "required": ["count"],
                            "additionalProperties": False,
                        },
                    }
                ],
                **changes,
            }
        )
    )


def invocation(**changes):
    return PluginInvocation(
        **{
            "invocation_ref": "invocation-1",
            "plugin_id": "personal-report",
            "plugin_version": "1.0.0",
            "tool_name": "summarize",
            "subject_ref": "owner",
            "deadline": datetime.now(UTC) + timedelta(minutes=1),
            "arguments": {"days": 30},
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"plugin_id": "core"},
        {"interface_version": "2.0"},
        {"entrypoint": "python:untrusted.module"},
        {"requested_permissions": ["approvals.issue"]},
    ],
)
def test_manifest_rejects_core_namespace_incompatible_versions_and_execution(changes):
    with pytest.raises(ValidationError):
        manifest(**changes)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "$ref": "https://untrusted.example/schema"},
        {"type": "object", "properties": {"value": {"$ref": "#"}}},
        {"type": "object", "required": "invalid"},
        {"type": "array"},
        {"type": "object", "properties": {"value": {"type": "string", "pattern": "(a+)+$"}}},
    ],
)
def test_schema_validation_is_inline_and_checks_schema_itself(schema):
    with pytest.raises(ValueError):
        check_schema(schema)


def test_declared_read_only_cannot_request_write_or_exceed_manifest_permissions():
    data = manifest().model_dump(mode="json")
    data["tools"][0]["permissions"] = ["transfers.write"]
    with pytest.raises(ValidationError):
        PluginManifest.model_validate_json(json.dumps(data))
    data["tools"][0]["permissions"] = ["accounts.read"]
    with pytest.raises(ValidationError):
        PluginManifest.model_validate_json(json.dumps(data))


def test_registration_does_not_execute_code_or_grant_permissions_and_copies_schemas():
    registry = PluginRegistry()
    declaration = manifest()
    registry.register(declaration)
    declaration.tools[0].input_schema["type"] = "array"
    assert registry.get("personal-report").manifest.tools[0].input_schema["type"] == "object"
    registry.enable("personal-report")
    assert registry.describe()[0]["enabled"]
    assert registry.describe()[0]["permissions_granted"] is False
    with pytest.raises(ServiceError) as error:
        registry.invoke("personal-report", invocation())
    assert error.value.code == "PLUGIN_RUNTIME_UNAVAILABLE"
    with pytest.raises(ServiceError):
        registry.unregister("personal-report")
    registry.disable("personal-report")
    registry.unregister("personal-report")
    assert registry.describe() == []


def test_core_tool_conflict_and_duplicate_registration_are_rejected():
    registry = PluginRegistry()
    declaration = manifest()
    registry.register(declaration)
    with pytest.raises(ServiceError):
        registry.register(declaration)
    data = declaration.model_dump(mode="json")
    data["plugin_id"] = "other-report"
    data["tools"][0]["name"] = "transfer.create"
    with pytest.raises(ServiceError) as error:
        registry.register(PluginManifest.model_validate_json(json.dumps(data)))
    assert error.value.code == "PLUGIN_TOOL_CONFLICT"


@pytest.mark.parametrize(
    "changes",
    [
        {"plugin_version": "2.0.0"},
        {"subject_ref": "owner", "deadline": datetime.now(UTC) - timedelta(seconds=1)},
        {"arguments": {"days": -1}},
        {"tool_name": "missing"},
    ],
)
def test_invocation_rejects_stale_versions_deadlines_and_bad_parameters(changes):
    with pytest.raises(ValueError):
        validate_invocation(manifest(), invocation(**changes))


def test_result_is_correlated_and_checked_but_not_business_success_evidence():
    declaration, request = manifest(), invocation()
    validate_invocation(declaration, request)
    result = PluginResult(
        invocation_ref=request.invocation_ref, status="completed", data={"count": 2}
    )
    validate_result(declaration, request, result)
    with pytest.raises(ValueError):
        validate_result(
            declaration, request, result.model_copy(update={"invocation_ref": "other-call"})
        )
    with pytest.raises(ValueError):
        validate_result(
            declaration, request, result.model_copy(update={"data": {"count": "invalid"}})
        )
    with pytest.raises(ValidationError):
        PluginResult(invocation_ref="invocation-1", status="completed", verified_success=True)
