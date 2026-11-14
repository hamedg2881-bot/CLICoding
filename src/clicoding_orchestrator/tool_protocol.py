from __future__ import annotations

import json
import math
import re
from typing import Any


_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SCHEMA_KEYS = {
    "type", "description", "properties", "required", "additionalProperties",
    "items", "enum", "minimum", "maximum", "minLength", "maxLength",
    "minItems", "maxItems",
}


class ToolProtocolError(ValueError):
    """A client tool contract or model-generated tool call is invalid."""


def validate_tool_definitions(
    tools: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Validate a bounded OpenAI function-tool list and return it indexed by name."""
    if tools is None:
        return {}
    if not isinstance(tools, list) or not 1 <= len(tools) <= 32:
        raise ToolProtocolError("tools must contain between 1 and 32 function definitions")

    definitions: dict[str, dict[str, Any]] = {}
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise ToolProtocolError(f"tools[{index}] must be a function tool")
        function = tool.get("function")
        if not isinstance(function, dict):
            raise ToolProtocolError(f"tools[{index}].function must be an object")
        name = function.get("name")
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            raise ToolProtocolError(f"tools[{index}] has an invalid function name")
        if name in definitions:
            raise ToolProtocolError(f"duplicate tool name: {name}")
        description = function.get("description", "")
        if not isinstance(description, str) or len(description) > 1024:
            raise ToolProtocolError(f"tools[{index}].description must be at most 1024 characters")
        schema = function.get("parameters")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ToolProtocolError(f"tools[{index}].parameters must be an object schema")
        _validate_schema_definition(schema, f"tools[{index}].parameters")
        definitions[name] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": schema,
            },
        }
    return definitions


def _validate_schema_definition(
    schema: dict[str, Any], path: str, depth: int = 0
) -> None:
    if depth > 8:
        raise ToolProtocolError(f"{path} exceeds the maximum schema nesting depth")
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        raise ToolProtocolError(f"{path} uses unsupported schema keywords: {sorted(unknown)}")
    schema_type = schema.get("type")
    if schema_type not in {"object", "array", "string", "integer", "number", "boolean"}:
        raise ToolProtocolError(f"{path}.type is unsupported")

    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not 1 <= len(values) <= 100:
            raise ToolProtocolError(f"{path}.enum must contain 1-100 values")
        if any(not _matches_type(value, schema_type) for value in values):
            raise ToolProtocolError(f"{path}.enum values must match {schema_type}")

    for key in ("minimum", "maximum"):
        if key in schema and (
            schema_type not in {"integer", "number"}
            or not isinstance(schema[key], (int, float))
            or isinstance(schema[key], bool)
            or not math.isfinite(schema[key])
        ):
            raise ToolProtocolError(f"{path}.{key} is only valid as a numeric bound")
    if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
        raise ToolProtocolError(f"{path} minimum cannot exceed maximum")

    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (
            schema_type not in ({"string"} if "Length" in key else {"array"})
            or not isinstance(schema[key], int)
            or isinstance(schema[key], bool)
            or schema[key] < 0
            or schema[key] > 1000000
        ):
            raise ToolProtocolError(f"{path}.{key} is invalid for {schema_type}")
    for low, high in (("minLength", "maxLength"), ("minItems", "maxItems")):
        if low in schema and high in schema and schema[low] > schema[high]:
            raise ToolProtocolError(f"{path}.{low} cannot exceed {high}")

    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or len(properties) > 64:
            raise ToolProtocolError(f"{path}.properties must be a bounded object")
        if not isinstance(required, list) or any(
            not isinstance(item, str) or item not in properties for item in required
        ):
            raise ToolProtocolError(f"{path}.required must reference declared properties")
        if len(set(required)) != len(required):
            raise ToolProtocolError(f"{path}.required cannot contain duplicates")
        additional = schema.get("additionalProperties", False)
        if not isinstance(additional, bool):
            raise ToolProtocolError(f"{path}.additionalProperties must be boolean")
        for key, value in properties.items():
            if not isinstance(key, str) or not key or len(key) > 128 or not isinstance(value, dict):
                raise ToolProtocolError(f"{path}.properties entries must have bounded names and schemas")
            _validate_schema_definition(value, f"{path}.properties.{key}", depth + 1)
    elif schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            raise ToolProtocolError(f"{path}.items must be a schema")
        _validate_schema_definition(items, f"{path}.items", depth + 1)


def _matches_type(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    if schema_type == "boolean":
        return isinstance(value, bool)
    return False


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    schema_type = schema["type"]
    if not _matches_type(value, schema_type):
        raise ToolProtocolError(f"{path} must be {schema_type}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolProtocolError(f"{path} is not an allowed enum value")

    if schema_type == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise ToolProtocolError(f"{path} is missing required fields: {sorted(missing)}")
        if schema.get("additionalProperties", False) is False:
            extra = set(value) - set(properties)
            if extra:
                raise ToolProtocolError(f"{path} contains unknown fields: {sorted(extra)}")
        for key, item in value.items():
            if key in properties:
                _validate_value(item, properties[key], f"{path}.{key}")
    elif schema_type == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 256):
            raise ToolProtocolError(f"{path} has an invalid number of items")
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], f"{path}[{index}]")
    elif schema_type == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 20000):
            raise ToolProtocolError(f"{path} has an invalid string length")
    elif schema_type in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolProtocolError(f"{path} is below its minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolProtocolError(f"{path} exceeds its maximum")


def validate_tool_calls(
    tool_calls: list[dict[str, Any]] | None,
    definitions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate model output against only the tool schemas supplied by this client."""
    if tool_calls is None:
        return []
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise ToolProtocolError("exactly one tool call is supported per assistant turn")
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ToolProtocolError(f"tool_calls[{index}] must be a function call")
        call_id = call.get("id")
        if not isinstance(call_id, str) or not _CALL_ID.fullmatch(call_id) or call_id in seen_ids:
            raise ToolProtocolError(f"tool_calls[{index}] has an invalid or duplicate id")
        seen_ids.add(call_id)
        function = call.get("function")
        if not isinstance(function, dict):
            raise ToolProtocolError(f"tool_calls[{index}].function must be an object")
        name = function.get("name")
        if not isinstance(name, str) or name not in definitions:
            raise ToolProtocolError(f"model requested an undeclared tool: {name!r}")
        raw_arguments = function.get("arguments")
        if not isinstance(raw_arguments, str) or len(raw_arguments) > 65536:
            raise ToolProtocolError(f"tool {name} arguments must be bounded JSON text")
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ToolProtocolError(f"tool {name} arguments are not valid JSON") from exc
        schema = definitions[name]["function"]["parameters"]
        _validate_value(arguments, schema, f"tool {name} arguments")
        result.append({
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
            },
        })
    return result


def pending_tool_call(events: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Return the single outstanding call ID/name, or None when the turn is complete."""
    pending: tuple[str, str] | None = None
    seen_call_ids: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ToolProtocolError(f"conversation event {index} must be an object")
        metadata = event.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ToolProtocolError(f"conversation event {index} metadata must be an object")
        if event.get("role") == "assistant":
            if "tool_calls" not in metadata:
                continue
            calls = metadata["tool_calls"]
            if not isinstance(calls, list) or len(calls) != 1:
                raise ToolProtocolError("assistant event must contain exactly one supported tool call")
            call = calls[0]
            if not isinstance(call, dict):
                raise ToolProtocolError("assistant tool call must be an object")
            call_id = call.get("id")
            function = call.get("function")
            if not isinstance(call_id, str) or not _CALL_ID.fullmatch(call_id):
                raise ToolProtocolError("assistant tool call has an invalid call ID")
            if call_id in seen_call_ids:
                raise ToolProtocolError("conversation reuses a tool call ID")
            if not isinstance(function, dict):
                raise ToolProtocolError("assistant tool call function must be an object")
            name = function.get("name")
            if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
                raise ToolProtocolError("assistant tool call has an invalid function name")
            if pending is not None:
                raise ToolProtocolError("conversation contains overlapping pending tool calls")
            seen_call_ids.add(call_id)
            pending = (call_id, name)
        elif event.get("role") == "tool":
            call_id = metadata.get("tool_call_id")
            tool_name = metadata.get("name")
            if pending is None or call_id != pending[0]:
                raise ToolProtocolError("tool result does not match the pending assistant tool call")
            if tool_name is not None and tool_name != pending[1]:
                raise ToolProtocolError("tool result name does not match the pending assistant tool call")
            pending = None
    return pending


def validate_tool_result(
    events: list[dict[str, Any]],
    tool_call_id: str | None,
    tool_name: str | None,
    content: str | None = None,
) -> None:
    if not tool_call_id or not _CALL_ID.fullmatch(tool_call_id):
        raise ToolProtocolError("tool result requires a valid tool_call_id")
    pending = pending_tool_call(events)
    if pending is None:
        # An exact duplicate of the last tool result is an idempotent retry after
        # the previous model continuation failed or was cancelled.
        if events and events[-1].get("role") == "tool":
            last = events[-1]
            metadata = last.get("metadata") or {}
            same_call = metadata.get("tool_call_id") == tool_call_id
            same_name = tool_name is None or metadata.get("name") == tool_name
            same_content = content is None or last.get("content") == content
            if same_call and same_name and same_content:
                return
        raise ToolProtocolError("there is no pending tool call for this conversation")
    if tool_call_id != pending[0]:
        raise ToolProtocolError("tool_call_id does not match the pending assistant tool call")
    if tool_name is not None and tool_name != pending[1]:
        raise ToolProtocolError("tool result name does not match the pending assistant tool call")
