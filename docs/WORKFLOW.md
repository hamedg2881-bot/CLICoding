# End-to-End Development Workflow

## 0. Task intake
Capture:
- desired outcome and acceptance criteria;
- target project/solution and branch;
- constraints (framework, compatibility, design system, performance, security);
- whether UI/screenshot validation is required.

## 1. Discovery (read-only)
Agent checks:
- current branch, working tree, uncommitted changes;
- solution/project structure and target frameworks;
- repository instructions and architecture docs;
- relevant source, tests, assets, configuration;
- existing build/test status where practical.

Do not overwrite user changes. Report uncertainty and missing evidence.

## 2. Planning (no edits)
Return:
- understanding of requested behavior;
- files/components likely affected;
- implementation sequence;
- risks and alternatives;
- tests/build/browser checks;
- screenshots/viewports/states required for UI work.

Wait for explicit developer approval before implementation.

## 3. Implementation
- Work only in the selected workspace.
- Prefer small, coherent patches.
- Keep existing user changes intact.
- Do not modify unrelated files.
- Present diffs and explain behavior changes.
- Ask approval for destructive or broad-scope operations.

## 4. Engineering validation
Run the appropriate real commands, for example:
- dotnet restore (only against approved local/internal feeds);
- dotnet build;
- dotnet test;
- dotnet format --verify-no-changes;
- configured analyzers/security checks.

Capture exit codes and relevant output. A model statement is not test evidence.

## 5. Visual validation
When UI is involved:
1. Start the app using the project's documented local run command.
2. Use Playwright on the workstation.
3. Capture agreed viewport sizes and interaction states.
4. Send screenshot image content, not only a file path, to a verified Vision-capable model.
5. Compare against explicit acceptance criteria and, when available, a baseline screenshot.
6. Report issues with element/region, observed behavior, expected behavior, and confidence.
7. Apply approved changes and repeat screenshot capture.
8. Preserve before/after screenshots as test artifacts when appropriate; avoid committing sensitive user data.

Manual screenshot workflow must also be available: developer attaches an image and gives a natural-language instruction. Agent combines image evidence with repository context.

## 6. Iteration limits
- Fix issues supported by test/browser/image evidence.
- Re-run only relevant checks after each patch, then full required checks before delivery.
- Bound automatic attempts and report blockers rather than looping indefinitely.
- Never claim visual perfection; list remaining known differences.

## 7. Review and delivery
Provide:
- summary of changed files and behavior;
- final diff;
- build/test/analyzer results;
- screenshot before/after or visual findings where relevant;
- security/regression risks and unresolved issues;
- suggested commit message.

Developer explicitly approves commit/push. Record final outcome and evidence in the project status or implementation log.
