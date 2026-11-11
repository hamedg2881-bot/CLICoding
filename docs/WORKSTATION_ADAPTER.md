# CDS_AI workstation adapter reference

**Status:** Initial Python reference adapter implemented; editor-specific UI integrations remain future work.

## Trust boundary

The DGX hosts CDS_AI inference and conversation orchestration. This adapter runs on the developer workstation and is bound to one explicitly selected workspace root. It never accepts a workspace path from model-generated arguments and exposes no general-purpose shell tool.

The adapter's idempotency SQLite file must be stored outside the workspace. The workspace root is resolved at startup and every file path is checked as a relative path under that root. Symlinks, traversal, common credential files, and secret directories are blocked.

## Included tools

| Tool | Access | Approval |
|---|---|---|
| `workspace_list_files` | Bounded file listing | No |
| `workspace_read_file` | UTF-8 text read, size-limited | No |
| `workspace_search_text` | Literal text search, bounded files/results | No |
| `workspace_write_file` | Atomic file replacement with SHA-256 optimistic concurrency | Required; default deny |
| `workspace_git_status` | Fixed read-only Git status command | No |
| `workspace_git_diff` | Fixed read-only Git diff command | No |
| `workspace_dotnet_build` | Fixed `dotnet build --no-restore` command | Required |
| `workspace_dotnet_test` | Fixed `dotnet test --no-restore` command | Required |

There is no arbitrary `shell`, `run_command`, or arbitrary executable tool. Build/test can still run project-defined MSBuild targets and therefore require explicit user approval. The adapter invokes subprocesses with argument arrays and `shell=False`, a fixed working directory, a restricted environment, and a timeout.

## Approval and idempotency

Integrators provide an approval callback. If no callback is provided, file writes and build/test are denied. The approval request includes workspace ID, tool name, command or file path, hashes, and a bounded content preview.

Every tool call is bound to `workspace_id + tool_call_id` and a hash of the function name and arguments. A local SQLite ledger records a running invocation before execution. Successful results are cached; duplicate calls return the cached result. A call ID reused with different arguments is rejected. If the process crashes while a side effect is running, the ledger remains pending and refuses to execute the same call again automatically; the user must reconcile the workspace before retrying with a new call ID.

## Minimal integration

```python
from cds_ai_ide_adapter import CDSAIClient, ToolExecutionStore, WorkspaceToolHost

def ask_user_to_approve(request):
    # Replace with an IDE approval dialog showing request.summary/details.
    return False  # fail closed until the user explicitly approves

host = WorkspaceToolHost(
    workspace_root="/path/to/checked-out/project",
    workspace_id="my-project",
    store=ToolExecutionStore("/path/outside/workspace/cds-ai-tools.sqlite3"),
    approval=ask_user_to_approve,
)
client = CDSAIClient(
    "http://<DGX-LAN-address>:8090/v1",
    api_key="<configured API key>",
)

result = await client.chat_with_tools(
    [{"role": "user", "content": "Inspect this project and report its structure."}],
    host,
    task_type="chat",
)
print(result.message["content"])
```

The example deliberately defaults approval to denial. Do not hard-code API keys; load them from the IDE's secret store or the user's environment. The tool host should be instantiated separately for each trusted workspace.

## Current limitations

- This is a Python reference library, not a packaged VS Code, Visual Studio, Rider, or JetBrains extension.
- The client currently uses non-streaming tool rounds. Direct text chat streaming is implemented by the API, but tool-call SSE deltas are not.
- Only one tool call is accepted per assistant turn.
- The .NET build/test commands use `--no-restore`; dependency restore and package installation are intentionally not automated.
- Browser automation, Playwright screenshots, patch/diff application, project indexing, and non-.NET build adapters are not included yet.
- The current tool protocol's JSON Schema validator supports a deliberately limited subset; unsupported schema keywords are rejected.

## Acceptance gates

Before production use, add platform-specific IDE approval UI, signed/trusted adapter identity, workspace registration, Windows/macOS/Linux integration tests, process sandboxing, audit export, cancellation, result redaction, and a full offline end-to-end sample project workflow.
