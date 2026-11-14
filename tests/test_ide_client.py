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

class ToolErrorSequenceGateway:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, *, tools=None, tool_choice=None):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return Completion(
                content="",
                model="tool-model",
                tool_calls=[{
                    "id": "call_missing_file",
                    "type": "function",
                    "function": {
                        "name": "workspace_read_file",
                        "arguments": '{"path":"missing.txt"}',
                    },
                }],
                finish_reason="tool_calls",
            )
        tool_message = messages[-1]
        assert tool_message["role"] == "tool"
        result = json.loads(tool_message["content"])
        assert result["status"] == "error"
        return Completion(
            content="I could not read the file because it does not exist.",
            model="tool-model",
        )


def test_cds_ai_client_returns_tool_errors_to_model_and_continues_conversation(tmp_path):
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        host = WorkspaceToolHost(
            workspace,
            "ide-tool-error",
            ToolExecutionStore(tmp_path / "tool-error-ledger.sqlite3"),
        )
        gateway = ToolErrorSequenceGateway()
        database = Database(str(tmp_path / "tool-error-conversation.sqlite3"))
        registry = WorkerRegistry([
            Worker(
                "tool-worker",
                "http://model/v1",
                "tool-model",
                frozenset({"chat", "tool_calling"}),
            )
        ])
        app = create_app(
            settings=Settings(database_path=str(tmp_path / "tool-error-conversation.sqlite3")),
            gateway=gateway,
            database=database,
            registry=registry,
        )
        client = CDSAIClient(
            "http://test/v1",
            transport=httpx.ASGITransport(app=app),
        )
        result = await client.chat_with_tools(
            [{"role": "user", "content": "Read missing.txt"}],
            host,
        )

        assert result.tool_rounds == 1
        assert "does not exist" in result.message["content"]
        assert len(gateway.calls) == 2
        tool_message = gateway.calls[1][-1]
        assert tool_message["role"] == "tool"
        assert tool_message["tool_call_id"] == "call_missing_file"
        assert json.loads(tool_message["content"])["status"] == "error"

    asyncio.run(scenario())

def test_cancelling_client_turn_prevents_pending_approved_file_write(tmp_path):
    import threading

    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        approval_started = threading.Event()
        release_approval = threading.Event()
        execution_finished = threading.Event()

        def approval(_request):
            approval_started.set()
            release_approval.wait(timeout=5)
            return True

        host = WorkspaceToolHost(
            workspace,
            "ide-cancel-write",
            ToolExecutionStore(tmp_path / "cancel-ledger.sqlite3"),
            approval=approval,
        )
        original_execute = host.execute_tool_call

        def tracked_execute(*args, **kwargs):
            try:
                return original_execute(*args, **kwargs)
            finally:
                execution_finished.set()

        host.execute_tool_call = tracked_execute

        class WriteSequenceGateway:
            async def complete(self, messages, *, tools=None, tool_choice=None):
                return Completion(
                    content="",
                    model="tool-model",
                    tool_calls=[{
                        "id": "call_cancel_write",
                        "type": "function",
                        "function": {
                            "name": "workspace_write_file",
                            "arguments": json.dumps({
                                "path": "created.txt",
                                "content": "must not be written",
                            }),
                        },
                    }],
                    finish_reason="tool_calls",
                )

        database = Database(str(tmp_path / "cancel-conversation.sqlite3"))
        registry = WorkerRegistry([
            Worker(
                "tool-worker",
                "http://model/v1",
                "tool-model",
                frozenset({"chat", "tool_calling"}),
            )
        ])
        app = create_app(
            settings=Settings(database_path=str(tmp_path / "cancel-conversation.sqlite3")),
            gateway=WriteSequenceGateway(),
            database=database,
            registry=registry,
        )
        client = CDSAIClient(
            "http://test/v1",
            transport=httpx.ASGITransport(app=app),
        )

        turn = asyncio.create_task(client.chat_with_tools(
            [{"role": "user", "content": "Create created.txt"}],
            host,
        ))
        started = await asyncio.to_thread(approval_started.wait, 5)
        assert started
        turn.cancel()
        try:
            await turn
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("client turn should propagate cancellation")

        release_approval.set()
        finished = await asyncio.to_thread(execution_finished.wait, 5)
        assert finished
        assert not (workspace / "created.txt").exists()

    asyncio.run(scenario())
