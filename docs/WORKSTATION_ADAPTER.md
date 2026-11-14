# CDS_AI workstation adapter reference

**Status:** Python reference adapter implemented; editor-specific UI integrations remain future work.

## Trust boundary

The DGX hosts CDS_AI inference and conversation orchestration. The adapter runs on the developer workstation and is bound to one explicitly selected workspace root. It never accepts a workspace path from model-generated arguments and exposes no general-purpose shell tool.

Tool results are recursively redacted for common credential-shaped values before being cached or returned. This is defense in depth, not a guarantee that every proprietary or nonstandard secret format is detected; never expose secret-bearing files to the agent.

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
| `workspace_dotnet_build` | Snapshot-based .NET 10 build inside existing Docker sandbox | Required; default deny |
| `workspace_dotnet_test` | Snapshot-based .NET 10 test inside existing Docker sandbox | Required; default deny |

There is no arbitrary `shell`, `run_command`, or arbitrary executable tool. The adapter does not run project-controlled MSBuild targets or tests directly on the workstation host.

## Existing Docker sandbox integration

The workstation adapter collects a bounded UTF-8 text snapshot and submits it to the Orchestrator's owner-scoped workspace approval API. The server stores the immutable snapshot and SHA-256 manifest in SQLite. The user approval covers the exact action, project path, workspace ID, file manifest and aggregate snapshot hash. The Orchestrator then calls the **existing** root-owned Unix-socket broker; the broker rechecks every file digest and claims the approval once before invoking the **existing** Docker runner.

The multi-file runner supports .NET SDK 10.0.402 Build/Test only:
- Up to 200 files, 5 MiB total and 1 MiB per file.
- Only relative paths; traversal, absolute paths, symlinks, duplicate/case-colliding paths, secret files and sensitive directories are rejected.
- The selected `.sln`, `.slnx` or `.csproj` must be included in the snapshot.
- The workspace is mounted read-only; Docker copies it into a private writable container `/tmp` workspace.
- Network is disabled, the container root filesystem is read-only, capabilities are dropped, and memory/CPU/PID/time/output limits are fixed.
- NuGet restore uses only the root-owned local feed at `/var/cache/clicoding-sandbox/nuget-feed`. No public package feed or network fallback is allowed. Missing packages cause the build/test to fail.
- The Docker SDK image must already exist locally; the runner uses a pinned image digest and `--pull=never`.

This extends the same broker and runner used by single-file snippets. It does not create a parallel sandbox.

## Approval and idempotency

Integrators provide an approval callback. If no callback is provided, file writes and remote Build/Test are denied. The workspace approval request includes the workspace ID, operation, selected project, complete path/hash/size manifest, snapshot digest and bounded source previews after credential redaction. A production editor integration must still provide a human-readable full diff review and clearly indicate truncated previews before approval.

Every tool call is bound to `workspace_id + conversation_id + tool_call_id` and a hash of the function name and arguments. A local SQLite ledger records a running invocation before execution. Successful results are cached; duplicate calls return the cached result. A call ID reused with different arguments is rejected. If the process crashes while a side effect is running, the ledger remains pending and refuses to execute the same call again automatically; the user must reconcile the workspace before retrying with a new call ID.

## Minimal integration

```python
from cds_ai_ide_adapter import (
    CDSAIClient,
    ToolExecutionStore,
    WorkspaceToolHost,
)

def ask_user_to_approve(request):
    # Replace with an IDE approval dialog showing request.summary/details.
    # Return True only after the user reviews and approves this exact snapshot.
    return False

client = CDSAIClient(
    "http://<DGX-LAN-address>:8090/v1",
    api_key="<configured API key>",
)
sandbox_executor = client.create_sandbox_executor(ask_user_to_approve)

host = WorkspaceToolHost(
    workspace_root="/path/to/checked-out/project",
    workspace_id="my-project",
    store=ToolExecutionStore("/path/outside/workspace/cds-ai-tools.sqlite3"),
    approval=ask_user_to_approve,
    sandbox_executor=sandbox_executor,
)

result = await client.chat_with_tools(
    [{"role": "user", "content": "Inspect this project and report its structure."}],
    host,
    task_type="chat",
)
print(result.message["content"])
```

The example deliberately defaults approval to denial. Do not hard-code API keys; load them from the IDE's secret store or the user's environment. Instantiate the tool host separately for each trusted workspace.

## Current limitations

- This is a Python reference library, not a packaged VS Code, Visual Studio, Rider, or JetBrains extension.
- Workspace snapshots currently support UTF-8 text files only; required binary assets are rejected rather than silently omitted.
- Snapshot limits are intentionally conservative (200 files / 5 MiB / 1 MiB per file); large solutions need a reviewed limit increase and corresponding resource testing.
- A root-owned offline NuGet feed must be populated with the exact dependencies required by each project. Package restore never accesses the public internet.
- Remote Stop/cancel is routed through the same broker and terminates the labeled Docker workspace container; live DGX cancellation acceptance is still required.
- Live DGX ARM64 Docker execution, exact image availability, local NuGet feed restore and real sample-project Build/Test acceptance have not yet been performed.
- Browser automation, Playwright screenshots, patch/diff application, project indexing, and non-.NET build adapters are not included yet.
- The current tool protocol's JSON Schema validator supports a deliberately limited subset; unsupported schema keywords are rejected.

## Acceptance gates

Before production use, add platform-specific IDE approval/diff UI, signed/trusted adapter identity, workspace registration, Windows/macOS/Linux integration tests, remote sandbox cancellation, binary snapshot support or explicit binary exclusions per project, audit export, and a complete offline sample-project build/test workflow with captured evidence.
