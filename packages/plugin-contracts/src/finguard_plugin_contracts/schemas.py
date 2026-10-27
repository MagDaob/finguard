"""有界内联 JSON Schema 校验，不解析远程引用。"""

import json

from jsonschema import Draft202012Validator, SchemaError


def check_schema(schema: dict) -> None:
    if len(json.dumps(schema, ensure_ascii=False, allow_nan=False).encode()) > 32768:
        raise ValueError("插件模式超过大小限制。")
    nodes = 0

    def visit(value, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 16 or nodes > 2048:
            raise ValueError("插件模式过于复杂。")
        if isinstance(value, dict):
            if set(value) & {
                "$ref",
                "$dynamicRef",
                "$recursiveRef",
                "pattern",
                "patternProperties",
            }:
                raise ValueError("接口 1.0 只接受不含引用和正则表达式的内联模式。")
            for item in value.values():
                visit(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                visit(item, depth + 1)

    visit(schema)
    if schema.get("type") != "object":
        raise ValueError("配置和工具输入模式必须为对象。")
    if (
        schema.get("$schema", "https://json-schema.org/draft/2020-12/schema")
        != "https://json-schema.org/draft/2020-12/schema"
    ):
        raise ValueError("仅支持 JSON Schema Draft 2020-12。")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise ValueError("插件 JSON Schema 无效。") from None


def matches(schema: dict, value: dict) -> bool:
    check_schema(schema)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 65536:
        return False
    count = 0

    def bounded(item, depth=0):
        nonlocal count
        count += 1
        if depth > 16 or count > 512:
            return False
        if isinstance(item, dict):
            return len(item) <= 128 and all(bounded(v, depth + 1) for v in item.values())
        if isinstance(item, list):
            return len(item) <= 128 and all(bounded(v, depth + 1) for v in item)
        return True

    if not bounded(value):
        return False
    return Draft202012Validator(schema).is_valid(value)
