import pytest

from clicoding_orchestrator.tool_protocol import (
    ToolProtocolError,
    pending_tool_call,
    validate_tool_calls,
    validate_tool_definitions,
    validate_tool_result,
)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file inside the active workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 2000}
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    }
]


def valid_call(arguments='{"path":"README.md"}'):
    return [{
        "id": "call_123",
        "type": "function",
        "function": {"name": "read_file", "arguments": arguments},
    }]


def test_validates_function_definitions_and_model_calls():
    definitions = validate_tool_definitions(TOOLS)
    calls = validate_tool_calls(valid_call(), definitions)

    assert calls[0]["id"] == "call_123"
    assert calls[0]["function"]["name"] == "read_file"
    assert calls[0]["function"]["arguments"] == '{"path":"README.md"}'


@pytest.mark.parametrize(
    "arguments",
    [
        "{}",
        '{"path": 42}',
        '{"path":"README.md","extra":true}',
        "not-json",
    ],
)
def test_rejects_arguments_that_do_not_match_declared_schema(arguments):
    definitions = validate_tool_definitions(TOOLS)
    with pytest.raises(ToolProtocolError):
        validate_tool_calls(valid_call(arguments), definitions)


def test_rejects_undeclared_tool_and_duplicate_definitions():
    definitions = validate_tool_definitions(TOOLS)
    unknown = [{
        "id": "call_123",
        "type": "function",
        "function": {"name": "run_shell", "arguments": "{}"},
    }]
    with pytest.raises(ToolProtocolError, match="undeclared"):
        validate_tool_calls(unknown, definitions)
    with pytest.raises(ToolProtocolError, match="duplicate"):
        validate_tool_definitions(TOOLS + TOOLS)


def test_tool_result_must_match_the_one_pending_call_and_can_only_complete_once():
    events = [
        {
            "role": "assistant",
            "metadata": {"tool_calls": valid_call()},
        }
    ]
    assert pending_tool_call(events) == ("call_123", "read_file")
    validate_tool_result(events, "call_123", "read_file")
    completed = events + [{
        "role": "tool",
        "metadata": {"tool_call_id": "call_123", "name": "read_file"},
        "content": "result",
    }]
    assert pending_tool_call(completed) is None
    validate_tool_result(completed, "call_123", "read_file", "result")
    with pytest.raises(ToolProtocolError, match="no pending"):
        validate_tool_result(completed, "call_123", "read_file", "different result")


def test_rejects_mismatched_tool_result_identity():
    events = [{"role": "assistant", "metadata": {"tool_calls": valid_call()}}]
    with pytest.raises(ToolProtocolError, match="does not match"):
        validate_tool_result(events, "call_wrong", "read_file")
    with pytest.raises(ToolProtocolError, match="name"):
        validate_tool_result(events, "call_123", "write_file")


@pytest.mark.parametrize(
    "calls",
    [
        [],
        [{}],
        [{"id": "call_123", "type": "function"}],
        [{"id": "bad id", "function": {"name": "read_file"}}],
        [{"id": "call_123", "function": {"name": ""}}],
        [{"id": "call_123", "function": {"name": "read_file"}}, {"id": "call_124", "function": {"name": "read_file"}}],
    ],
)
def test_pending_tool_call_rejects_malformed_imported_assistant_history(calls):
    events = [{"role": "assistant", "metadata": {"tool_calls": calls}}]
    with pytest.raises(ToolProtocolError):
        pending_tool_call(events)


def test_pending_tool_call_rejects_reused_call_ids_across_completed_calls():
    call = valid_call()[0]
    events = [
        {"role": "assistant", "metadata": {"tool_calls": [call]}},
        {"role": "tool", "metadata": {"tool_call_id": "call_123", "name": "read_file"}, "content": "ok"},
        {"role": "assistant", "metadata": {"tool_calls": [call]}},
    ]
    with pytest.raises(ToolProtocolError, match="reuses"):
        pending_tool_call(events)



@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "minLength": "1"},
        {"type": "string", "maxLength": -1},
        {"type": "string", "minLength": 10, "maxLength": 2},
        {"type": "array", "minItems": 3, "maxItems": 1, "items": {"type": "string"}},
        {"type": "integer", "minimum": "zero"},
        {"type": "boolean", "maximum": 1},
        {"type": "string", "enum": [1, 2]},
        {"type": "string", "enum": []},
        {"type": "object", "required": ["missing"]},
        {"type": "object", "required": ["path", "path"], "properties": {"path": {"type": "string"}}},
    ],
)
def test_rejects_malformed_tool_parameter_schema_constraints(schema):
    definitions = [{
        "type": "function",
        "function": {
            "name": "unsafe_schema",
            "parameters": {"type": "object", "properties": {"value": schema}},
        },
    }]
    with pytest.raises(ToolProtocolError):
        validate_tool_definitions(definitions)


def test_rejects_excessively_nested_tool_parameter_schema():
    nested = {"type": "string"}
    for _ in range(10):
        nested = {"type": "array", "items": nested}
    definitions = [{
        "type": "function",
        "function": {
            "name": "deep_schema",
            "parameters": {"type": "object", "properties": {"value": nested}},
        },
    }]
    with pytest.raises(ToolProtocolError, match="nesting depth"):
        validate_tool_definitions(definitions)
