# CDS_AI identity and conversation ownership

**Status:** Ownership isolation, environment-mapped tokens, durable token issue/revoke, per-principal rate limiting and admin audit export are implemented in source. Trusted Open WebUI header mode and production network boundaries still require live deployment verification.

## Authentication modes

The API supports three identity modes.

### 1. Single-user compatibility mode

With only `CLICODING_API_KEY` configured, that key retains the existing single-user/admin behavior and all conversations remain in the `shared` namespace. This mode is not multi-tenant isolation.

### 2. Per-user API tokens

Set `CLICODING_USER_API_KEYS` to a JSON object mapping stable user IDs to unique random bearer tokens (minimum 32 ASCII characters). Example shape only:

```json
{
  "developer-a": "<unique-random-token-at-least-32-characters>",
  "developer-b": "<unique-random-token-at-least-32-characters>",
  "admin": "<separate-admin-token-at-least-32-characters>"
}
```

Each token maps to its user ID. The reserved user ID `admin` has administrative access. If `CLICODING_API_KEY` is also configured and trusted user-header mode is disabled, that global key is an administrator/service key. Never commit these values.

### 3. Open WebUI trusted identity header

For a single Open WebUI connection that injects the authenticated user's identity, set `CLICODING_TRUST_USER_HEADER=true`. The global `CLICODING_API_KEY` authenticates the service connection; the request's `X-CLICoding-User-ID` becomes the conversation owner.

**Critical trust requirement:** a plain HTTP header is not cryptographic proof. Enable this only when the Orchestrator is reachable exclusively through a trusted private network/reverse proxy, and that proxy strips any client-supplied `X-CLICoding-User-ID` before injecting the authenticated Open WebUI identity. Do not expose the Orchestrator directly to untrusted clients in this mode. If this network boundary cannot be guaranteed, use per-user API tokens instead.

When trusted-header mode is enabled, the global key is user-scoped (not an admin bypass). A per-user token map may also be used; the global key still follows the trusted-header path, while mapped tokens resolve to their explicit user IDs.

## Ownership enforcement

- Conversations persist an `owner_id`; legacy databases migrate existing conversations to `shared`.
- Conversation creation, reads, turns, OpenAI-compatible chat continuation, execution reads, and sandbox approval/result operations enforce owner scope.
- An owner cannot access another owner's conversation by guessing its ID; non-owned records return 404.
- Worker/runtime diagnostics require an admin principal.
- Admin principals may access all conversations.
- The Open WebUI chat ID remains namespaced with the user ID when both configured headers are present.

## Migration note

Existing conversations created before owner migration remain owned by `shared`. In multi-user mode, normal user principals cannot access those legacy records. Use an administrator to export or explicitly reassign legacy conversations before enabling multi-user mode. Do not auto-claim shared history for the first user who requests it.

## Implemented token and operational controls

- Administrators can issue and list tokens through `POST/GET /api/v1/admin/tokens` and revoke through `DELETE /api/v1/admin/tokens/{token_id}`.
- Generated user tokens are cryptographically random; only SHA-256 hashes are stored. The plaintext is returned once at issuance.
- Authentication remains enabled after the first token is issued, even if all issued tokens are later revoked.
- Per-principal rate limiting is controlled by `CLICODING_RATE_LIMIT_PER_WINDOW` (default 120) and `CLICODING_RATE_LIMIT_WINDOW_SECONDS` (default 60).
- Administrators can read paginated metadata-only audit events through `GET /api/v1/admin/audit`; bodies, prompts and bearer-token values are excluded.

## Remaining security work

- The trusted-header mode depends on the deployment network boundary and does not sign the identity header. Verify Open WebUI header stripping/reinjection in a deployed multi-user acceptance test before enabling it.
- Define audit retention, export signing, alerting and key-rotation procedures.
- Cross-process turn locking/cancellation remains open; production must use one service worker until that gate is closed.
