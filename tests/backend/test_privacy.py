from finguard.backend.privacy import redact, redact_text


def test_known_secrets_cards_and_phones_are_masked():
    value = "卡号6222020200111234 手机13800138000 api_key=private-demo-value Bearer demo-token-value sk-example-private-value"
    result = redact_text(value)
    for secret in (
        "6222020200111234",
        "13800138000",
        "private-demo-value",
        "demo-token-value",
        "sk-example-private-value",
    ):
        assert secret not in result
    assert "****1234" in result and "138****8000" in result
    assert redact_text("给小王转100元，至少保留1000元") == "给小王转100元，至少保留1000元"


def test_nested_export_hides_secret_fields_without_changing_amounts_or_refs():
    result = redact(
        {
            "data": [{"api_key": "private-value", "amount_minor": 1234567890123}],
            "operation_ref": "operation-a12345678901234b",
        }
    )
    assert result["data"][0] == {"api_key": "[REDACTED]", "amount_minor": 1234567890123}
    assert result["operation_ref"] == "operation-a12345678901234b"


def test_quoted_secret_values_and_legacy_text_are_masked():
    for text in (
        '{"api_key":"private value"}',
        "password='private value'",
        '密钥："private value"',
    ):
        assert "private value" not in redact_text(text)
