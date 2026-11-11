# CDS_AI product identity

**Product name:** CDS_AI  
**Canonical source repository:** https://github.com/hamedg2881-bot/CLICoding  
**Public assistant model ID:** `CDS_AI`

## Naming contract

- CDS_AI is the single user-facing assistant identity in Open WebUI and supported IDE adapters.
- The OpenAI-compatible `/v1/models` endpoint advertises `CDS_AI`.
- New clients should send `model: "CDS_AI"`.
- The previous public aliases `clicoding`, `assistant`, and `auto` remain accepted during migration; internal worker model names are never exposed.
- Existing environment-variable names, database paths, SQLite storage markers, and service identifiers remain unchanged until a separately validated migration is prepared. This avoids breaking deployed installations and persisted conversations.
- The GitHub repository remains named CLICoding; the product/assistant identity is CDS_AI.

## User experience

CDS_AI is one assistant with one continuous conversation identity. Model selection, specialist handoffs, and tool execution remain internal implementation details unless a concise user-visible status is useful. Ordinary chat must not depend on Planner availability. Coding and IDE work use the same conversation/policy layer and a workstation-side, allow-listed tool host.

## Release principle

A branding change is not a feature-completion claim. Release requires passing the product acceptance gates in `docs/CHAT_EXPERIENCE_SPEC.md`, `docs/IDE_TOOL_PROTOCOL.md`, and the deployment checklist. No deployment to DGX is implied by these source changes.
