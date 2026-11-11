import asyncio
import json

import httpx

from cds_ai_ide_adapter.client import CDSAIClient
from cds_ai_ide_adapter.tool_host import ToolExecutionStore, WorkspaceToolHost
from clicoding_orchestrator.app import create_app
from clicoding_orchestrator.config import Settings
from clicoding_orchestrator.database import Database
from clicoding_orchestrator.gateway import Completion
from clicoding_orchestrator.registry import Worker, WorkerRegistry


class ToolSequenceGateway:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, *, tools=None, tool_choice=None):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return Completion(
                content="",
                model="tool-model",
                tool_calls=[{
                    "id": "call_ide_1",
                    "type": "function",
                    "function": {
                        "name": "workspace_read_file",
                        "arguments": '{"path":"README.md"}',
                    },
                }],
                finish_reason="tool_calls",
            )
        return Completion(content="I read the README successfully.", model="tool-model")


def test_cds_ai_client_runs_tool_loop_on_workstation_and_continues_same_conversation(tmp_path):
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "README.md").write_text("Local project instructions", encoding="utf-8")
        host = WorkspaceToolHost(
            workspace,
            "ide-workspace",
            ToolExecutionStore(tmp_path / "ide-ledger.sqlite3"),
        )
        gateway = ToolSequenceGateway()
        database = Database(str(tmp_path / "conversation.sqlite3"))
        registry = WorkerRegistry([
            Worker(
                "tool-worker",
                "http://model/v1",
                "tool-model",
                frozenset({"chat", "tool_calling"}),
            )
        ])
        app = create_app(
            settings=Settings(database_path=str(tmp_path / "conversation.sqlite3")),
            gateway=gateway,
            database=database,
            registry=registry,
        )
        client = CDSAIClient(
            "http://test/v1",
            transport=httpx.ASGITransport(app=app),
        )
        result = await client.chat_with_tools(
            [{"role": "user", "content": "Read README.md"}],
            host,
        )
        assert result.tool_rounds == 1
        assert result.message["content"] == "I read the README successfully."
        assert result.conversation_id
        assert gateway.calls[1][-1]["role"] == "tool"
        tool_result = json.loads(gateway.calls[1][-1]["content"])
        assert tool_result["content"] == "Local project instructions"

    asyncio.run(scenario())
