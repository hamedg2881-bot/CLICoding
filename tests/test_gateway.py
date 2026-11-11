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


@pytest.mark.asyncio
async def test_openai_gateway_parses_upstream_sse_deltas(monkeypatch):
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

    deltas = [piece async for piece in gateway.stream(messages)]

    assert deltas == ["Good", " morning"]
    method, url, request = clients[0].request
    assert method == "POST"
    assert url == "http://model/v1/chat/completions"
    assert request["json"]["stream"] is True
    assert request["json"]["messages"] == messages


@pytest.mark.asyncio
async def test_openai_gateway_rejects_incomplete_sse_stream(monkeypatch):
    response = FakeResponse(
        ['data: ' + json.dumps({"choices": [{"delta": {"content": "partial"}}]})]
    )
    monkeypatch.setattr(
        "clicoding_orchestrator.gateway.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(response, **kwargs),
    )
    gateway = OpenAICompatibleGateway("http://model/v1", "served-model")

    with pytest.raises(GatewayError, match=r"before \[DONE\]"):
        _ = [piece async for piece in gateway.stream([{"role": "user", "content": "Hi"}])]
