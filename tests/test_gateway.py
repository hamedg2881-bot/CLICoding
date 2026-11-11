import asyncio
import json

import pytest

from clicoding_orchestrator.gateway import GatewayError, OpenAICompatibleGateway


class FakeResponse:
    def __init__(self, lines):
        self.lines = lines

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class FakeStreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeAsyncClient:
    def __init__(self, response, **kwargs):
        self.response = response
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def stream(self, method, url, **kwargs):
        self.request = (method, url, kwargs)
        return FakeStreamContext(self.response)


def test_openai_gateway_parses_upstream_sse_deltas(monkeypatch):
    response = FakeResponse(
        [
            ": keep-alive",
            "data: " + json.dumps({"choices": [{"delta": {"role": "assistant"}}]}),
            "data: " + json.dumps({"choices": [{"delta": {"content": "Good"}}]}),
            "data: " + json.dumps({"choices": [{"delta": {"content": " morning"}}]}),
            "data: [DONE]",
        ]
    )
    clients = []

    def make_client(**kwargs):
        client = FakeAsyncClient(response, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("clicoding_orchestrator.gateway.httpx.AsyncClient", make_client)
    gateway = OpenAICompatibleGateway("http://model/v1", "served-model")
    messages = [{"role": "user", "content": "Greet me"}]

    async def collect():
        return [piece async for piece in gateway.stream(
            messages, options={"temperature": 0.3, "max_tokens": 128}
        )]

    deltas = asyncio.run(collect())

    assert deltas == ["Good", " morning"]
    method, url, request = clients[0].request
    assert method == "POST"
    assert url == "http://model/v1/chat/completions"
    assert request["json"]["stream"] is True
    assert request["json"]["messages"] == messages
    assert request["json"]["temperature"] == 0.3
    assert request["json"]["max_tokens"] == 128


def test_openai_gateway_rejects_incomplete_sse_stream(monkeypatch):
    response = FakeResponse(
        ['data: ' + json.dumps({"choices": [{"delta": {"content": "partial"}}]})]
    )
    monkeypatch.setattr(
        "clicoding_orchestrator.gateway.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(response, **kwargs),
    )
    gateway = OpenAICompatibleGateway("http://model/v1", "served-model")

    async def collect():
        return [piece async for piece in gateway.stream([{"role": "user", "content": "Hi"}])]

    with pytest.raises(GatewayError, match="before"):
        asyncio.run(collect())


class FakeJSONResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeCompleteClient:
    def __init__(self, response, **kwargs):
        self.response = response
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        self.request = (url, kwargs)
        return self.response


def test_openai_gateway_forwards_tools_and_parses_tool_call_response(monkeypatch):
    tool_calls = [{
        "id": "call_123",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
    }]
    response = FakeJSONResponse({
        "model": "served-model",
        "choices": [{
            "message": {"role": "assistant", "content": None, "tool_calls": tool_calls},
            "finish_reason": "tool_calls",
        }],
    })
    clients = []

    def make_client(**kwargs):
        client = FakeCompleteClient(response, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("clicoding_orchestrator.gateway.httpx.AsyncClient", make_client)
    gateway = OpenAICompatibleGateway("http://model/v1", "served-model")
    tools = [{
        "type": "function",
        "function": {
            "name": "read_file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    }]

    completion = asyncio.run(gateway.complete(
        [{"role": "user", "content": "Read README"}],
        tools=tools,
        tool_choice="auto",
    ))

    assert completion.content == ""
    assert completion.tool_calls == tool_calls
    assert completion.finish_reason == "tool_calls"
    url, request = clients[0].request
    assert url == "http://model/v1/chat/completions"
    assert request["json"]["tools"] == tools
    assert request["json"]["tool_choice"] == "auto"
    assert request["json"]["parallel_tool_calls"] is False


def test_openai_gateway_forwards_generation_options_on_non_streaming_requests(monkeypatch):
    response = FakeJSONResponse({
        "model": "served-model",
        "choices": [{
            "message": {"role": "assistant", "content": "hello"},
            "finish_reason": "stop",
        }],
    })
    clients = []

    def make_client(**kwargs):
        client = FakeCompleteClient(response, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("clicoding_orchestrator.gateway.httpx.AsyncClient", make_client)
    gateway = OpenAICompatibleGateway("http://model/v1", "served-model")
    completion = asyncio.run(gateway.complete(
        [{"role": "user", "content": "Hi"}],
        options={"temperature": 0.2, "max_tokens": 64, "top_p": 0.8},
    ))
    assert completion.content == "hello"
    payload = clients[0].request[1]["json"]
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 64
    assert payload["top_p"] == 0.8
